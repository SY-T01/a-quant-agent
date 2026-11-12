"""LLM allocation proposals, checked against budget, evidence and current review gates."""
from decimal import Decimal,InvalidOperation,ROUND_DOWN
import hashlib
import html
import json
import re
from .storage import PROJECT,atomic_json

SETTINGS=PROJECT/'configs/research_preferences.json'
STYLES={
    'conservative':dict(label='保守',max_single_weight=.15,max_industry_weight=.3,min_cash_weight=.4),
    'balanced':dict(label='均衡',max_single_weight=.2,max_industry_weight=.35,min_cash_weight=.25),
    'active':dict(label='积极',max_single_weight=.25,max_industry_weight=.4,min_cash_weight=.1),
}
CENT=Decimal('.01')
TOL=Decimal('.000001')


def money(value):
    if isinstance(value,bool):raise ValueError('资金金额无效')
    try:n=Decimal(str(value))
    except (InvalidOperation,ValueError):raise ValueError('资金金额无效') from None
    if not n.is_finite() or not Decimal('1')<=n<=Decimal('1000000000') or n!=n.quantize(CENT):
        raise ValueError('资金应为1元至10亿元，最多两位小数')
    return n.quantize(CENT)


def parse_amount(value):
    text=value.strip().replace(',','').replace('，','')
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?万?',text):raise ValueError('请输入金额，例如100000或10万')
    return money(Decimal(text.rstrip('万'))*(10000 if text.endswith('万') else 1))


def settings(amount,style='balanced'):
    if style not in STYLES:raise ValueError('资金分配风格无效')
    return dict(budget_yuan=str(money(amount)),currency='CNY',style=style,**STYLES[style],
                scope='本次计划投入的新资金，不含已有持仓；分配比例均以本次资金为基数。',
                limits_note='风格对应的集中度与现金下限是程序研究约束，不是经过验证的最优仓位，也不代表已评估个人风险承受能力。')


def load(path=None):
    path=path or SETTINGS
    data=json.loads(path.read_text()) if path.exists() else {}
    if not data.get('capital_yuan'):return None
    return settings(data['capital_yuan'],data.get('allocation_style','balanced'))


def save(amount,style='balanced',path=None):
    selected=settings(amount,style);path=path or SETTINGS
    data=json.loads(path.read_text()) if path.exists() else {}
    data.update(capital_yuan=selected['budget_yuan'],allocation_style=style)
    atomic_json(path,data)
    return selected


def input_hash(report):
    from .model_review import candidate_hash
    facts={'schema':'capital-v1','candidate_hash':candidate_hash(report),'capital_settings':report.get('capital_settings')}
    return hashlib.sha256(json.dumps(facts,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def weight(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)):raise ValueError('比例必须为0至1的数字')
    n=Decimal(str(value))
    if not n.is_finite() or not 0<=n<=1:raise ValueError('比例必须为0至1的数字')
    return n


def trading_constraints(report):
    rows=[]
    for c in report.get('stock_recommendations',{}).get('stocks',[]):
        try:price=Decimal(str(c.get('quote',{}).get('price')))
        except InvalidOperation:continue
        if not price.is_finite() or price<=0:continue
        minimum=200 if c['symbol'].endswith('.SH') and c['symbol'].startswith(('688','689')) else 100
        rows.append(dict(symbol=c['symbol'],min_buy_shares=minimum,reference_min_amount_yuan=str((price*minimum).quantize(CENT)),note='按参考价估算最低单笔买入金额，忽略费用；不是实时委托'))
    return rows


def validate_portfolio(raw,kind,report,decisions,config):
    if not isinstance(raw,dict) or not isinstance(raw.get('allocations'),list):raise ValueError('缺少股票与现金分配结构')
    cash_weight=weight(raw.get('cash_weight'));rows=[];seen=set();industries={};total=cash_weight
    budget=money(config['budget_yuan']);candidates={c['symbol']:c for c in report.get('stock_recommendations',{}).get('stocks',[])}
    by_symbol={d['symbol']:d for d in decisions}
    from .context_research import evidence_ids
    allowed=evidence_ids(report.get('research_context',{}))
    allowed.update(c['evidence_id'] for c in candidates.values())
    allowed.update(s['evidence_id'] for s in report.get('sectors',[]) if s.get('evidence_id'))
    for item in raw['allocations']:
        if not isinstance(item,dict) or not isinstance(item.get('symbol'),str):raise ValueError('股票记录无效')
        symbol=item['symbol']
        if symbol not in candidates or symbol in seen:raise ValueError('出现未知或重复股票')
        seen.add(symbol);c=candidates[symbol];d=by_symbol.get(symbol,{})
        accepted=('approve_conditional',) if kind=='current' else ('approve_conditional','defer','plan_incomplete')
        if d.get('verdict') not in accepted:raise ValueError('股票复核结论不适用于此分配方案：'+symbol)
        if any(not isinstance(item.get(k),str) or not item[k].strip() for k in ('reason','condition')):raise ValueError('分配缺少理由或触发条件')
        ids=item.get('evidence_ids')
        if not isinstance(ids,list) or c['evidence_id'] not in ids or any(not isinstance(i,str) or i not in allowed for i in ids):raise ValueError('分配未引用有效的本股证据')
        w=weight(item.get('weight'));total+=w
        if w>Decimal(str(config['max_single_weight'])):raise ValueError('超过单股资金上限')
        industry=c['industry_code'];industries[industry]=industries.get(industry,Decimal(0))+w
        if industries[industry]>Decimal(str(config['max_industry_weight'])):raise ValueError('超过单行业资金上限')
        amount=(budget*w).quantize(CENT,rounding=ROUND_DOWN)
        minimum=next((t for t in trading_constraints(report) if t['symbol']==symbol),None)
        if w>0 and minimum and amount<Decimal(minimum['reference_min_amount_yuan']):raise ValueError('预算不足按参考价买入最低申报量：'+symbol)
        row=dict(symbol=symbol,name=c['name'],industry=c.get('industry_name',industry),weight=float(w),amount_yuan=str(amount),reason=item['reason'],condition=item['condition'],tranches=[])
        # This is a reservation of money, not an order or a share-count calculation.
        if kind=='current':
            plan=(d.get('review') or {}).get('buy_plan') or {};remaining=amount;tranches=plan.get('tranches',[])
            for i,t in enumerate(tranches):
                portion=remaining if i==len(tranches)-1 else min(remaining,(amount*Decimal(str(t['fraction']))).quantize(CENT,rounding=ROUND_DOWN))
                remaining-=portion;row['tranches'].append(dict(amount_yuan=str(portion),condition=t['condition']))
        rows.append(row)
    if abs(total-1)>TOL:raise ValueError('股票与现金比例合计必须为100%')
    if cash_weight+TOL<Decimal(str(config['min_cash_weight'])):raise ValueError('低于所选风格的现金保留下限')
    cash=budget-sum((Decimal(r['amount_yuan']) for r in rows),Decimal(0))
    if cash<0:raise ValueError('分配金额超过预算')
    if cash<budget*Decimal(str(config['min_cash_weight'])):raise ValueError('实际现金低于所选风格下限')
    invested=budget-cash
    stress=[dict(shock_pct=change,pnl_yuan=str((invested*Decimal(change)/100).quantize(CENT)),budget_change_pct=float(invested*Decimal(change)/budget)) for change in (-20,20)] if invested else []
    return dict(status='已核对',allocations=rows,cash_yuan=str(cash),cash_weight=float(cash/budget),stress=stress,
                title='当前可考虑的条件分配' if kind=='current' else '条件满足后的备选分配（当前不执行）')


def build(report,pipeline):
    config=report.get('capital_settings')
    if not config:return dict(status='未设置资金',note='在应用菜单9设置计划投入资金和分配风格，再选择菜单4调用大模型。',portfolios=[])
    result=dict(status='待大模型分配',settings=config,summary='',portfolios=[],issues=[],
                note='金额是预算预留，已按参考价检查最低买入量；未按实时价格、交易权限和费用换算可成交股数。两个方案是同一笔资金的替代情景，不能相加投入。')
    ai=report.get('ai') or {}
    matched=ai.get('allocation_input_hash')==input_hash(report)
    raw=(ai.get('result') or {}).get('portfolio_allocation') if matched else None
    if not isinstance(raw,dict):
        result['issues'].append('尚无与本次金额、风格、期限及证据匹配的大模型资金方案；更改金额不会自动沿用旧方案。')
    else:
        if isinstance(raw.get('summary'),str):result['summary']=raw['summary']
        for kind in ('current','conditional'):
            try:result['portfolios'].append(validate_portfolio(raw.get(kind),kind,report,pipeline['decisions'],config))
            except (ValueError,TypeError,KeyError,InvalidOperation) as e:result['issues'].append(('当前方案：' if kind=='current' else '备选方案：')+str(e))
        result['status']='已收到大模型分配' if not result['issues'] else '部分方案未通过核对，其他有效内容保留'
    if not pipeline['final_buy_candidates'] and not any(p['title']=='当前可考虑的条件分配' for p in result['portfolios']):
        result['portfolios'].insert(0,dict(status='程序约束，不是新增模型建议',title='当前分配：保留现金',allocations=[],cash_yuan=config['budget_yuan'],cash_weight=1.))
    return result


def sections(report):
    result=report.get('capital_allocation') or {};config=result.get('settings')
    paragraphs=[result.get('status','未设置资金')]
    if not config:return paragraphs+[result.get('note','')]
    paragraphs += [f"计划投入 {Decimal(config['budget_yuan']):,.2f} 元；{config['label']}风格；单股上限 {config['max_single_weight']:.0%}，单行业上限 {config['max_industry_weight']:.0%}，现金下限 {config['min_cash_weight']:.0%}。",config['scope'],config['limits_note']]
    if result.get('summary'):paragraphs.append('大模型分配说明：'+result['summary'])
    for p in result['portfolios']:
        paragraphs += [p['title']+'；'+p['status']]
        for row in p['allocations']:
            paragraphs.append(f"{row['name']}（{row['symbol']}）：{row['weight']:.1%}，预留 {Decimal(row['amount_yuan']):,.2f} 元。理由：{row['reason']}；条件：{row['condition']}")
            for i,t in enumerate(row['tranches'],1):paragraphs.append(f"第{i}批预算 {Decimal(t['amount_yuan']):,.2f} 元；触发条件：{t['condition']}")
        paragraphs.append(f"保留现金：{Decimal(p['cash_yuan']):,.2f} 元（{p['cash_weight']:.1%}）。")
        if p.get('stress'):
            paragraphs.append('算术压力情景：'+ '；'.join(f"若所配股票全部同步{t['shock_pct']:+.0f}%，账面盈亏{Decimal(t['pnl_yuan']):+,.2f}元，占总预算{t['budget_change_pct']:+.1f}%" for t in p['stress'])+'。现金不变，忽略费用与分红；这是人为冲击设定，不是回报或概率预测。')
    paragraphs += result['issues']+[result['note']]
    return paragraphs


def html_section(report):
    return '<h2>按计划资金分配</h2><section>'+''.join('<p>'+html.escape(p)+'</p>' for p in sections(report))+'</section>'


def markdown_section(report):
    return ['','## 按计划资金分配','']+[p+'\n' for p in sections(report)]

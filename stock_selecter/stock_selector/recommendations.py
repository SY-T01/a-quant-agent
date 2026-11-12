"""A small conditional shortlist, with explicit gates and source timestamps."""
import json
import re
from datetime import datetime
from urllib.request import Request,urlopen
from .storage import now,atomic_json

POLICY=dict(quote_pool_limit=120,max_stocks=5,max_per_industry=2,min_profit_yuan=1e8,min_revenue_yuan=1e9,min_cash_cover=.8,max_cash_cover=5,max_pe=45,max_pb=6,max_quote_age_days=5,min_amount_yuan=2e7,max_sector_age_days=7)

def research_policy(report,override=None):
    from .investment_horizon import get
    return dict(POLICY,**dict(get(report)['gates'],**(override or {})))

def base_reasons(candidate,sector,horizon,policy):
    reasons=[]
    if not re.fullmatch(r'\d{6}\.(SH|SZ)',candidate.get('symbol','')):reasons.append('证券代码不适用')
    if horizon['flow_gate']=='both_positive':
        if sector.get('flow_state')!='5/10日均净流入':reasons.append('行业5/10日资金未同时净流入')
    elif not isinstance(sector.get('net_10d_yuan'),(int,float)) or sector['net_10d_yuan']<=0:reasons.append('行业10日资金未净流入')
    cover=candidate.get('cash_profit_ratio')
    if cover is None:reasons.append('缺少可比经营现金流覆盖')
    elif not policy['min_cash_cover']<=cover<=policy['max_cash_cover']:reasons.append('现金流覆盖未通过门槛')
    if candidate['profit_yuan']<policy['min_profit_yuan'] or candidate['revenue_yuan']<policy['min_revenue_yuan']:reasons.append('收入或利润规模未通过门槛')
    if any(k in candidate['name'].upper() for k in ('ST','退')):reasons.append('风险警示证券名称')
    return reasons

def eligible_pool(report,scope='primary'):
    from .investment_horizon import get
    horizon=get(report);policy=research_policy(report)
    sectors={s['code']:s for s in report['sectors']};unique={}
    if scope not in ('primary','supplementary'):raise ValueError('未知选股范围')
    priorities=set(report.get('research_priorities',[]))
    for c in report.get('fundamental_candidates',[]):
        if (c['industry_code'] in priorities)!=(scope=='primary'):continue
        s=sectors.get(c['industry_code'],{})
        if base_reasons(c,s,horizon,policy):continue
        if c['symbol'] in unique and unique[c['symbol']]!=c:raise ValueError('重复证券记录存在冲突，停止推荐')
        unique[c['symbol']]=c
    return list(unique.values())

def pool(report,limit=None,scope='primary'):
    """Deterministic round-robin across industries; not the first N input records."""
    from collections import defaultdict
    groups=defaultdict(list)
    for c in eligible_pool(report,scope):groups[c['industry_code']].append(c)
    for group in groups.values():
        group.sort(key=lambda c:(-(.6*min(c['cash_profit_ratio'],2)/2+.4*c['growth_score']/100),c['symbol']))
    priority_order={code:i for i,code in enumerate(report.get('research_priorities',[]))}
    result=[];level=0;codes=sorted(groups,key=lambda code:(priority_order.get(code,999),code))
    while len(result)<(limit or POLICY['quote_pool_limit']):
        batch=[groups[k][level] for k in codes if len(groups[k])>level]
        if not batch:break
        result.extend(batch);level+=1
    return result[:limit or POLICY['quote_pool_limit']]

def parse_quotes(text):
    output={}
    for market,code,body in re.findall(r'v_(sh|sz)(\d{6})="([^"\n]+)"',text):
        v=body.split('~')
        if len(v)<47 or v[2]!=code:raise ValueError('腾讯行情字段或代码改变')
        def value(i):
            import math
            if not v[i]:return None
            n=float(v[i])
            if not math.isfinite(n):raise ValueError('行情数值非法')
            return n
        symbol=code+('.SH' if market=='sh' else '.SZ')
        if symbol in output:raise ValueError('行情响应包含重复证券')
        stamp=datetime.strptime(v[30],'%Y%m%d%H%M%S').isoformat()+'+08:00'
        output[symbol]=dict(symbol=symbol,name=v[1],price=value(3),pe_vendor=value(39),pb=value(46),amount_yuan=value(37)*10000 if value(37) is not None else None,quote_at=stamp,source_url='https://qt.gtimg.cn/q='+market+code)
    if not output:raise ValueError('腾讯未返回候选行情')
    return output

def fetch_quotes(candidates,store):
    codes=','.join(c['symbol'][-2:].lower()+c['symbol'][:6] for c in candidates)
    url='https://qt.gtimg.cn/q='+codes
    with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.qq.com/'}),timeout=20) as f:text=f.read().decode('gb18030')
    rows=parse_quotes(text)
    ref=store.save('tencent','recommendation_quotes','batch',dict(url=url,response=text),[dict(date=now()[:10],quotes=rows)])
    return dict(quotes=rows,snapshot=ref,observed_at=now())

def select(report,quotes,asof=None,policy=None,scope='primary'):
    from .investment_horizon import get
    horizon=get(report);policy=research_policy(report,policy);weights=horizon['weights']
    cutoff=datetime.fromisoformat(asof or report['asof'])
    if cutoff.tzinfo is None:raise ValueError('推荐时点必须包含时区')
    sectors={s['code']:s for s in report['sectors']};eligible=[];rejected=[]
    candidates=pool(report,scope=scope)
    for c in candidates:
        q=quotes.get(c['symbol']); reasons=[]
        if c['cash_profit_ratio']<policy['min_cash_cover']:reasons.append('现金流覆盖未通过本情景门槛')
        sector=sectors[c['industry_code']]
        stamp=sector.get('data_time')
        def age(value):
            try:
                dt=datetime.fromisoformat(value)
                if dt.tzinfo is None:return None
                return (cutoff-dt).total_seconds()/86400
            except (ValueError,TypeError):return None
        sector_age=age(stamp)
        if sector_age is None or not 0<=sector_age<=policy['max_sector_age_days']:reasons.append('行业资金快照过旧或无有效时间')
        try:
            report_age=(cutoff.date()-datetime.fromisoformat(c['report_period']).date()).days
            notice=datetime.fromisoformat(c.get('notice_date','')).date()
            if not 0<=report_age<=180:reasons.append('财报报告期过旧或在未来')
            if notice>=cutoff.date():reasons.append('财报披露日期尚不可用于本次研究')
        except (ValueError,TypeError):reasons.append('缺少有效财报/披露日期')
        if not q:reasons.append('未取得有效行情')
        else:
            quote_age=age(q.get('quote_at'))
            if quote_age is None or not 0<=quote_age<=policy['max_quote_age_days']:reasons.append('行情时间不适用')
            if any(k in q['name'].upper() for k in ('ST','退')):reasons.append('风险警示名称')
            if q['name']!=c['name']:reasons.append('证券名称与财报不一致')
            if not q.get('price') or q['price']<=0 or not q.get('amount_yuan') or q['amount_yuan']<policy['min_amount_yuan']:reasons.append('最近行情无成交')
            if q.get('pe_vendor') is None or not 0<q['pe_vendor']<=policy['max_pe']:reasons.append('PE估值未通过初始上限')
            if q.get('pb') is None or not 0<q['pb']<=policy['max_pb']:reasons.append('PB未通过初始上限')
        if reasons:rejected.append(dict(symbol=c['symbol'],name=c['name'],industry_code=c['industry_code'],reasons=reasons));continue
        s=sectors[c['industry_code']]
        components=dict(cash_quality=weights['cash_quality']*min(c['cash_profit_ratio'],2)/2,growth=weights['growth']*c['growth_score']/100,valuation=weights['valuation']*(1-q['pe_vendor']/policy['max_pe']),sector_flow=weights['sector_flow']*(s.get('flow_watch_score') or 0)/100)
        score=sum(components.values())/100
        risks=['季报累计同比不代表下一季度；买前核对公告、ST/停牌及可成交状态','行业历史趋势尚未完整验证，名单为条件性一般研究建议']
        if c['profit_growth_pct']>100:risks.append('利润增幅较高，需剔除低基数和一次性收益影响')
        if any(k in c['industry_name'] for k in ('金属','材料','化工')):risks.append('周期行业需跟踪产品价格和盈利回落风险')
        eligible.append(dict(**{k:v for k,v in c.items() if k!="evidence_id"},quote=q,score_components=components,recommendation_score=round(score*100,1),recommendation='满足买入前提后可优先考虑',
                             reasons=[f"{horizon['label']}研究：{horizon['style']}权重；行业{s.get('flow_state','资金方向待核对')}，10日净额{s['net_10d_yuan']/1e8:.2f}亿元",f"营收同比{c['revenue_growth_pct']:.1f}%，利润同比{c['profit_growth_pct']:.1f}%",f"经营现金流/归母净利润{c['cash_profit_ratio']:.2f}，供应商PE {q['pe_vendor']:.2f}倍"],
                             entry_condition='下一交易日复核未ST/停牌、估值仍在筛选范围且无重大不利公告后，再考虑分批；参考价不是委托价',
                             invalidation='下一次财报收入或利润转为下降、经营现金流转负，或行业5/10日均转为净流出时重新评估',risks=risks,evidence_id='recommendation:'+c['symbol']))
    selected=[];counts={}
    for r in sorted(eligible,key=lambda r:(-r['recommendation_score'],r['symbol'])):
        code=r['industry_code']
        if counts.get(code,0)>=policy['max_per_industry']:continue
        selected.append(r);counts[code]=counts.get(code,0)+1
        if len(selected)>=policy['max_stocks']:break
    return dict(stocks=selected,rejected=rejected,eligible_stocks=eligible,selection_policy='industry-first-v1',investment_horizon=horizon,weights=weights,scope=dict(financial_candidates=len(report.get('fundamental_candidates',[])),eligible_before_quotes=len(eligible_pool(report,scope)),quote_pool=len(candidates),quotes_available=sum(c['symbol'] in quotes for c in candidates)),policy=policy,note='主名单仅来自优先行业；外围公司另列补充观察，不补位、不进入资金分配。不强行凑满5只；门槛未经过收益优化。')

def acquisition_pool(report):
    primary=pool(report)
    remaining=POLICY['quote_pool_limit']-len(primary)
    return primary+(pool(report,limit=remaining,scope='supplementary') if remaining>0 else [])

def update(report,store,refresh=False):
    path=store.root/'normalized/sector_research/recommendation_quotes.json'
    data=json.loads(path.read_text()) if path.exists() else {'quotes':{}}
    error=None
    if refresh and acquisition_pool(report):
        try:
            data=fetch_quotes(acquisition_pool(report),store);atomic_json(path,data)
        except (OSError,ValueError) as e:error=type(e).__name__+'：行情更新未完成，采用时间有效的缓存'
        # Freeze the decision time after fetching, rather than rejecting newly received quotes as future.
        report['asof']=now()
    for q in data['quotes'].values():
        if 'pe_vendor' not in q and 'pe_ttm' in q:q['pe_vendor']=q.pop('pe_ttm')
    result=select(report,data['quotes']);result['quote_update_error']=error;result['quote_snapshot']=data.get('snapshot')
    supplementary=select(report,data['quotes'],scope='supplementary')
    result['supplementary_stocks']=supplementary['stocks']
    result['supplementary_eligible_stocks']=supplementary['eligible_stocks']
    result['selection_policy']='industry-first-v1'
    from .industry_selection import audit
    result['industry_audit']=audit(report,result)
    base={s['symbol'] for s in result['stocks']}
    sensitivity=[]
    active=research_policy(report)
    for label,change in [('估值上限收紧20%',{'max_pe':active['max_pe']*.8}),('现金流覆盖门槛进一步收紧',{'min_cash_cover':max(1.,active['min_cash_cover']+.2)})]:
        variants=select(report,data['quotes'],policy=change)
        # Cash cover is applied here as the base acquisition pool is deliberately held fixed.
        stocks=[s for s in variants['stocks'] if s['cash_profit_ratio']>=change.get('min_cash_cover',POLICY['min_cash_cover'])]
        symbols={s['symbol'] for s in stocks}
        sensitivity.append(dict(scenario=label,stocks=sorted(symbols),retained_from_base=len(base&symbols),base_count=len(base)))
    result['sensitivity']=sensitivity
    report['stock_recommendations']=result
    return result

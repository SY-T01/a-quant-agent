"""Parallel research and data readiness; cannot silently replace production rules."""
import argparse
import csv
import json
import hashlib
import statistics
from itertools import combinations
from pathlib import Path
from .storage import PROJECT,atomic_json
from .quant_factors import time_factors,breadth,grouped_ranks,full_mean,weighted_score,instant,finite

CONFIG=PROJECT/'configs/research_lab.json'
EVIDENCE=PROJECT/'data/research/market_evidence.json'


def verified_item(value,asof):
    if not isinstance(value,dict) or not value.get('source') or value.get('verified') is not True:return False
    try:return instant(value['available_at'])<=instant(asof) and (not value.get('expires_at') or instant(asof)<instant(value['expires_at']))
    except (ValueError,KeyError,TypeError):return False


def construct(report,enrichment=None,evidence=None,config=None):
    enrichment=enrichment or {};evidence={} if evidence is None else evidence;config=json.loads(CONFIG.read_text()) if config is None else config
    if not isinstance(evidence,dict) or not isinstance(config,dict):raise ValueError('研究输入与配置必须为JSON对象')
    if config.get('production_promotion') is not False or config.get('mode')!='parallel_research_only':raise ValueError('未经样本外验收，不允许研究模型自动替代主流程')
    for key in ('industry_weights','stock_weights'):weighted_score({k:None for k in config[key]},config[key])
    asof=report['asof'];errors=[];industry_rows=[]
    benchmark=evidence.get('benchmark') or enrichment.get('benchmark',[])
    histories=evidence.get('industry_history',{})
    for s in report.get('sectors',[]):
        code=s['code'];taxonomy=evidence.get('industry_taxonomy',{}).get(code)
        level=taxonomy.get('level') if verified_item(taxonomy,asof) and isinstance(taxonomy.get('level'),str) else None
        ext=histories.get(code)
        if verified_item(ext,asof):series=ext.get('series',[])
        else:
            prices=enrichment.get('price_history',{}).get(code,{}).get('series',[])
            flows=enrichment.get('history',{}).get(code,{}).get('series',[])
            by_date={r['date']:dict(r) for r in flows}
            for row in prices:
                existing=by_date.get(row['date'])
                if existing and existing.get('close') and abs(existing['close']-row['close'])>.01:
                    errors.append({'kind':'history_join','key':code,'error':'价格与资金序列收盘价不一致，不拼接该日资金'})
                    by_date[row['date']]=dict(row)
                else:by_date.setdefault(row['date'],{}).update(row)
            series=sorted(by_date.values(),key=lambda r:r['date'])
        try:metrics=time_factors(series,benchmark,asof,require_availability=bool(ext and verified_item(ext,asof)))
        except (ValueError,KeyError,TypeError) as e:metrics={};errors.append({'kind':'industry_history','key':code,'error':str(e)})
        # Snapshot 5/10-day totals can be divided by independently observed matching turnover.
        from .quant_factors import bars_at
        try:
            strict=bool(ext and verified_item(ext,asof))
            aligned=bars_at(series,asof,strict);calendar=bars_at(benchmark,asof,strict)
            if aligned and calendar and aligned[-1]['date']==(s.get('data_time') or '')[:10] and aligned[-1]['date']==calendar[-1]['date']:
                for n in (5,10):
                    window=aligned[-n:]
                    net=finite(s.get(f'net_{n}d_yuan'))
                    if len(window)==n and [r['date'] for r in window]==[r['date'] for r in calendar[-n:]] and net is not None and all(r['amount_yuan'] is not None and r['amount_yuan']>0 for r in window):
                        metrics[f'flow_{n}d']=net/sum(r['amount_yuan'] for r in window)
                if metrics.get('flow_5d') is not None and metrics.get('flow_10d') is not None and s.get('net_5d_yuan') is not None and s.get('net_10d_yuan') is not None:
                    prior=aligned[-10:-5];recent=aligned[-5:]
                    metrics['acceleration']=s['net_5d_yuan']/sum(r['amount_yuan'] for r in recent)-(s['net_10d_yuan']-s['net_5d_yuan'])/sum(r['amount_yuan'] for r in prior)
        except (ValueError,KeyError,TypeError) as e:errors.append({'kind':'turnover_alignment','key':code,'error':str(e)})
        try:b=breadth(evidence.get('memberships',{}).get(code),{k:v.get('series',[]) for k,v in evidence.get('stock_history',{}).items() if verified_item(v,asof)},evidence.get('benchmark',[]),asof)
        except (ValueError,KeyError,TypeError) as e:b={};errors.append({'kind':'breadth','key':code,'error':str(e)})
        f=s.get('financials',{});event=evidence.get('verified_events',{}).get(code)
        event_score=None
        if verified_item(event,asof) and isinstance(event.get('fact'),str) and event['fact'].strip() and event.get('expires_at'):
            try:
                event_score=finite(event.get('research_score'))
                if event_score is not None and not 0<=event_score<=100:raise ValueError('事件研究分须在0至100之间')
            except ValueError as e:errors.append({'kind':'event','key':code,'error':str(e)});event_score=None
        industry_rows.append(dict(key=code,name=s['name'],level=level,**metrics,
            flow_today=(s['net_today_yuan']/s['amount_today_yuan']) if s.get('net_today_yuan') is not None and s.get('amount_today_yuan') is not None and s['amount_today_yuan']>0 else None,
            vendor_ratio_5d_pct=s.get('ratio_5d_pct'),vendor_ratio_10d_pct=s.get('ratio_10d_pct'),
            advancers_5d=b.get('advancers_5d'),above_ma20=b.get('above_ma20'),breadth_coverage=b.get('coverage'),
            growth_breadth=f.get('growth_breadth') if f.get('disclosed_companies',0)>=3 else None,
            event_score=event_score))
    industry_fields=('flow_5d','flow_10d','acceleration','positive_10d','excess_5d','excess_10d','excess_20d','advancers_5d','above_ma20','growth_breadth')
    ranks={key:grouped_ranks(industry_rows,key,'level') for key in industry_fields}
    for row in industry_rows:
        key=row['key'];get=lambda field:ranks[field].get(key)
        components=dict(flow=full_mean([get(f) for f in ('flow_5d','flow_10d','acceleration','positive_10d')]),
            strength=full_mean([get(f) for f in ('excess_5d','excess_10d','excess_20d')]),
            breadth=full_mean([get('advancers_5d'),get('above_ma20')]),fundamental=get('growth_breadth'),event=row['event_score'])
        row.update(components=components,**weighted_score(components,config['industry_weights']))
        row['equal_weight_score']=weighted_score(components,{key:20 for key in components})['score']
        if row['score'] is not None and not row['level']:row['score']=None
        required=(row.get('flow_5d'),row.get('flow_10d'),row.get('excess_10d'))
        row['momentum_pool']=all(v>0 for v in required) if all(v is not None for v in required) else None
        required=(row.get('flow_10d'),row.get('acceleration'))
        row['reversal_watch']=required[0]<0 and required[1]>0 if all(v is not None for v in required) else None
    candidates=report.get('stock_recommendations',{});stocks=[]
    universe=candidates.get('eligible_stocks',candidates.get('stocks',[]))+candidates.get('supplementary_eligible_stocks',candidates.get('supplementary_stocks',[]))
    universe=list({c['symbol']:c for c in universe}.values())
    for c in universe:
        ext=evidence.get('stock_history',{}).get(c['symbol']);metrics={}
        if verified_item(ext,asof):
            try:metrics=time_factors(ext.get('series',[]),evidence.get('benchmark',[]),asof,True,True)
            except (ValueError,KeyError,TypeError) as e:errors.append({'kind':'stock_history','key':c['symbol'],'error':str(e)})
        supplement=evidence.get('financial_supplements',{}).get(c['symbol']);roe=None
        if verified_item(supplement,asof):
            try:roe=finite(supplement.get('roe'))
            except ValueError as e:errors.append({'kind':'financial_supplement','key':c['symbol'],'error':str(e)})
        pe=(c.get('quote') or {}).get('pe_vendor')
        income=enrichment.get('RPT_DMSK_FN_INCOME',{}).get(c['symbol'],{})
        deducted=investment=None
        if income.get('NOTICE_DATE','')[:10] and income['NOTICE_DATE'][:10]<asof[:10] and income.get('REPORT_DATE','')[:10]==c.get('report_period') and income.get('REPORT_DATE','')[:10]<=asof[:10]:
            try:deducted=finite(income.get('DEDUCT_PARENT_NETPROFIT'));investment=finite(income.get('INVEST_INCOME'))
            except ValueError as e:errors.append({'kind':'earnings_detail','key':c['symbol'],'error':str(e)})
        profit=c.get('profit_yuan')
        stocks.append(dict(key=c['symbol'],name=c['name'],industry=c['industry_code'],**metrics,cash_cover=c.get('cash_profit_ratio'),growth_score=c.get('growth_score'),roe=roe,
            evidence_id=c.get('evidence_id'),financial_source_url=c.get('source_url','https://data.eastmoney.com/bbsj/lrb.html'),
            parent_profit_yuan=profit,deducted_parent_profit_yuan=deducted,deducted_profit_share=deducted/profit if deducted is not None and profit and profit>0 else None,
            profit_minus_deducted_yuan=profit-deducted if profit is not None and deducted is not None else None,investment_income_yuan=investment,
            financial_period=c.get('report_period'),financial_notice_date=c.get('notice_date'),financial_basis='供应商当前修订版，累计报告期；投资收益不一律等同非经常性损益',
            pe_vendor=pe if pe is not None and pe>0 else None,volatility_quality=-metrics['volatility_20d'] if metrics.get('volatility_20d') is not None else None))
    stock_fields=('relative_volume','amount_20d_mean','flow_5d','excess_5d','excess_10d','excess_20d','cash_cover','growth_score','roe','pe_vendor','volatility_quality','drawdown_20d')
    ranked={field:grouped_ranks(stocks,field,'industry',reverse=field=='pe_vendor') for field in stock_fields}
    for row in stocks:
        get=lambda field:ranked[field].get(row['key'])
        components=dict(volume_flow=full_mean([get(f) for f in ('relative_volume','amount_20d_mean','flow_5d')]),strength=full_mean([get(f) for f in ('excess_5d','excess_10d','excess_20d')]),
            quality=full_mean([get(f) for f in ('cash_cover','growth_score','roe')]),valuation=get('pe_vendor'),risk=full_mean([get('volatility_quality'),get('drawdown_20d')]))
        row.update(components=components,**weighted_score(components,config['stock_weights']))
        industry_score=next((s['score'] for s in industry_rows if s['key']==row['industry']),None)
        row['industry_stock_joint_score']=round(industry_score*row['score']/100,2) if industry_score is not None and row['score'] is not None else None
    def coverage(label,rows,field,note):
        count=sum(r.get(field) is not None for r in rows)
        return dict(item=label,available=count,total=len(rows),status='完整' if count==len(rows) and count else '部分' if count else '未取得',note=note)
    audit=[coverage('供应商5日资金占比',industry_rows,'vendor_ratio_5d_pct','现有基线已使用，不能再当成独立新增资金因子重复计权'),
        coverage('当日净额/成交额',industry_rows,'flow_today','使用原始快照净额和成交额直接核算，只代表当日，不能代替多日强度'),
        coverage('资金净额/同期成交额',industry_rows,'flow_10d','需要逐日净额与成交额，不从供应商窗口占比反推成交额'),
        coverage('5/10/20日相对沪深300',industry_rows,'excess_20d','要求同交易日窗口、有效价格，缺少交易日不跨空缺计算'),
        coverage('完整成分市场宽度',industry_rows,'above_ma20','完整时点成分和复权个股行情；资金榜样本不能代替'),
        coverage('已核验行业层级',industry_rows,'level','只在同一已核验层级内缩尾和分位标准化，不猜父子映射'),
        coverage('候选股量价历史',stocks,'relative_volume','覆盖已有报价且通过基线门槛的公司池，不代表全市场'),
        coverage('完整行业增强分',industry_rows,'score','缺少分量时保留空值，不补0、不放大剩余权重')]
    limits=config['study_limits'];min_cash=max(limits['min_cash_weight'],1-limits['max_stocks']*limits['max_single_weight'])
    redundant=[]
    for level in sorted({s['level'] for s in industry_rows if s['level']}):
        for left,right in combinations(('flow_5d','flow_10d','acceleration','excess_5d','excess_10d','excess_20d'),2):
            pairs=[(s[left],s[right]) for s in industry_rows if s['level']==level and s.get(left) is not None and s.get(right) is not None]
            if len(pairs)<10:continue
            x,y=zip(*pairs);mx,my=statistics.mean(x),statistics.mean(y)
            denom=(sum((v-mx)**2 for v in x)*sum((v-my)**2 for v in y))**.5
            corr=sum((a-mx)*(b-my) for a,b in pairs)/denom if denom else None
            redundant.append(dict(level=level,left=left,right=right,samples=len(pairs),pearson=corr,high_redundancy=abs(corr)>=.85 if corr is not None else None))
    checksum=lambda obj:hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    months=report.get('investment_horizon',{}).get('months',6)
    industry_flow_weight=.8 if months<=2 else .5 if months<=6 else .25 if months<=12 else .15
    code_hashes={name:hashlib.sha256((PROJECT/'stock_selector'/name).read_bytes()).hexdigest() for name in ('quant_factors.py','research_lab.py','forward_validation.py','recommendations.py','research_report.py','investment_horizon.py')}
    return dict(version=config['version'],mode='并行研究，不替代当前主名单',asof=asof,baseline_unchanged=True,config=config,config_sha256=checksum(config),evidence_sha256=checksum(evidence),
        code_sha256=code_hashes,
        factors_sha256=checksum({'industries':industry_rows,'stocks':stocks}),
        baseline_manifest=dict(model_version=report.get('model_version'),weights=candidates.get('weights'),policy=candidates.get('policy'),holding_months=months,
            industry_weights={'flow':industry_flow_weight,'growth_breadth':round(1-industry_flow_weight,2)},provider_flow_percentile_weights={'today':.15,'five_day':.35,'ten_day':.5},priority_industries=report.get('research_priorities',[])),
        data_audit=audit,industry_factors=industry_rows,stock_factors=stocks,errors=errors,
        redundancy_checks=redundant,
        study_risk=dict(**limits,effective_min_cash_weight=min_cash,note='最多5只且每只最多10%，意味着至少50%现金；只用于研究对照，不覆盖用户原资金偏好'),
        validation=dict(status='尚不具备多年真实样本外验证条件',real_out_of_sample_complete=False,
            blockers=['缺少完整3—5年行业历史成分、证券状态与历史首次披露版本','当前公开财报是供应商修订版，不可直接当历史时点可见数据','未取得完整成交额与资金历史时，不能声称新增资金因子有超额收益']),
        interpretation='增强分是待验证假设，不是收益概率；关键证据不齐时不生成完整分数，继续保留原可复算规则与独立AI复核。')


def attach(report,enrichment=None):
    try:
        evidence=json.loads(EVIDENCE.read_text()) if EVIDENCE.exists() else {}
        report['research_lab']=construct(report,enrichment,evidence)
    except (ValueError,KeyError,TypeError,OSError) as e:
        report['research_lab']=dict(version='research-lab-v1',mode='并行研究异常，基线选股保留',baseline_unchanged=True,
            interpretation='增强研究未完成：'+str(e),data_audit=[],industry_factors=[],stock_factors=[],errors=[dict(error=str(e))],
            study_risk={'note':'未修改用户风险偏好或主名单'},validation={'status':'等待修复研究输入','blockers':['数据或配置校验未通过'],'real_out_of_sample_complete':False})
    return report['research_lab']


def save_outputs(report,out):
    lab=report.get('research_lab') or {};out=Path(out);out.mkdir(parents=True,exist_ok=True)
    atomic_json(out/'research_lab.json',lab)
    fields=['key','name','level','vendor_ratio_5d_pct','vendor_ratio_10d_pct','flow_5d','flow_10d','acceleration','excess_5d','excess_10d','excess_20d','advancers_5d','above_ma20','score','coverage_weight_pct']
    with (out/'行业增强因子.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(lab.get('industry_factors',[]))
    fields=['key','name','industry','parent_profit_yuan','deducted_parent_profit_yuan','deducted_profit_share','profit_minus_deducted_yuan','investment_income_yuan','financial_period','financial_notice_date','pe_vendor','cash_cover','roe','relative_volume','excess_20d','volatility_20d','score']
    with (out/'公司增强核对.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(lab.get('stock_factors',[]))


def paragraphs(report):
    lab=report.get('research_lab') or {}
    if not lab:return []
    lines=[lab['mode'],lab['interpretation']]
    months=report.get('investment_horizon',{}).get('months',6)
    lines.append('本组增强权重针对1—4周的短期研究；'+('与本次偏短期持有设置相符，仍须验证。' if months<=2 else '当前持有期限较长，不用短期实验权重替代现有期限规则。'))
    lines += [f"{r['item']}：{r['available']}/{r['total']}，{r['status']}。{r['note']}。" for r in lab['data_audit']]
    lines += [lab['study_risk']['note'],lab['validation']['status']+'；'+'；'.join(lab['validation']['blockers'])]
    tracking=report.get('forward_tracking')
    if tracking:lines.append('已登记前瞻观察，首次生成时间 '+tracking['decision_at'][:19].replace('T',' ')+'；等待之后的交易日，不回填历史预测。可在菜单12查看到期与受阻状态。')
    primary={c['symbol'] for c in report.get('stock_recommendations',{}).get('stocks',[])}
    earnings=[r for r in lab['stock_factors'] if r['key'] in primary and r.get('deducted_parent_profit_yuan') is not None]
    if earnings:
        lines.append('主名单财务增强核对：扣非利润和投资收益分别展示，差额尚不代表完成低基数或主营增长归因，不能只凭同比高增外推。')
        for r in earnings:
            share='未计算占比' if r['deducted_profit_share'] is None else f"占归母净利润{r['deducted_profit_share']*100:.1f}%"
            investment='未提供' if r['investment_income_yuan'] is None else f"{r['investment_income_yuan']/1e8:.2f}亿元"
            lines.append(f"{r['name']}：扣非归母净利润{r['deducted_parent_profit_yuan']/1e8:.2f}亿元，{share}；投资收益{investment}。报告期{r.get('financial_period') or '待核对'}。")
    validation=report.get('forward_validation',{})
    for row in validation.get('horizons',[]):lines.append(f"前瞻{row['horizon']}交易日：已测{row['measured']}，等待{row['pending']}，受阻{row['blocked']}；未到期不是0收益。")
    return lines


def html_section(report):
    import html
    lines=paragraphs(report)
    if not lines:return ''
    out='<h2 id="research-lab">增强量化研究与数据审计</h2><section>'+''.join('<p>'+html.escape(line)+'</p>' for line in lines)
    rows=[row for row in priority_rows(report) if any(row.get(k) is not None for k in ('flow_5d','flow_10d','excess_20d'))]
    if rows:
        out+='<h3>当前可计算的优先行业因子</h3><div class="scroll"><table><tr><th>优先行业</th><th>5日净额/成交额</th><th>10日净额/成交额</th><th>20日相对沪深300</th></tr>'
        for row in rows:
            values=[row['name']]+['—' if row.get(k) is None else f"{row[k]*100:+.2f}%" for k in ('flow_5d','flow_10d','excess_20d')]
            out+='<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in values)+'</tr>'
        out+='</table></div><p>以上为可复算的原始因子观察，尚不是新增模型通过名单；行业层级未核验时不跨层级给出增强排名。</p>'
    return out+'</section>'


def priority_rows(report):
    rows={r['key']:r for r in report.get('research_lab',{}).get('industry_factors',[])}
    return [rows[code] for code in report.get('research_priorities',[]) if code in rows]


def main():
    p=argparse.ArgumentParser(description='离线数据审计与增强因子观察，不调用大模型')
    p.add_argument('--report',default=str(PROJECT/'reports/sector_research/research.json'));p.add_argument('--out',default=str(PROJECT/'reports/research_lab'))
    a=p.parse_args();report=json.loads(Path(a.report).read_text());cache=PROJECT/'data/normalized/sector_research/enrichment.json'
    lab=attach(report,json.loads(cache.read_text()) if cache.exists() else {});save_outputs(report,a.out)
    out=Path(a.out)
    (out/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>数据审计与增强因子观察</title><style>body{font:16px/1.8 system-ui;max-width:1050px;margin:35px auto;padding:20px;color:#18344a}section{padding:20px;background:#f3f7fb}a{color:#096383}table{width:100%;border-collapse:collapse}td,th{padding:9px;border-bottom:1px solid #dce3ea;text-align:left}th{background:#e2edf4}</style>'+html_section(report)+'<p><a href="行业增强因子.csv">下载行业因子明细</a>　<a href="公司增强核对.csv">下载公司财务核对</a>　<a href="../research_validation/index.html">查看前瞻验证状态</a></p>',encoding='utf-8')
    for row in lab['data_audit']:print(f"{row['item']}：{row['available']}/{row['total']}（{row['status']}）")
    print('结果已保存；没有联网或调用付费API。')


if __name__=='__main__':main()

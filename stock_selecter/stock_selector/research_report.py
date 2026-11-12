"""Deterministic evidence summaries and human-readable HTML/Markdown reports."""
import html
import json
from collections import Counter


def prepare(report,enrichment=None,previous=None):
    enrichment=enrichment or {}
    old={r['code']:i+1 for i,r in enumerate((previous or {}).get('sectors',[]))}
    for i,s in enumerate(report['sectors']):
        metrics=enrichment.get('history',{}).get(s['code'],{}).get('metrics',{})
        s.pop('missing_evidence',None)
        s['history_metrics']=metrics
        s['financials']=enrichment.get('sector_financials',{}).get(s['code'],{})
        s['rank_change']=old[s['code']]-(i+1) if s['code'] in old else None
        a,b=s.get('net_5d_yuan'),s.get('net_10d_yuan')
        if a is not None and b is not None:
            prior=b-a
            s['previous_5d_net_yuan']=prior;s['net_change_5d_yuan']=a-prior
            s['flow_change']='流入改善' if a>prior else '净额回落' if a<prior else '持平'
        else: s['flow_change']='暂不可比较'
        n60=metrics.get('net_60d_yuan');e60=metrics.get('excess_60d');n20=metrics.get('net_20d_yuan');e20=metrics.get('excess_20d')
        if n60 is not None and e60 is not None and n60>0 and e60>0 and metrics.get('positive_60d',0)>=.5:
            s['research_state']='持续改善'
            s['next_check']='跟踪下次季报利润兑现及估值；若60日相对强弱转负，重新评估'
        elif n20 is not None and e20 is not None and n20>0 and e20>0:
            s['research_state']='短期升温'
            s['next_check']='观察20日净流入能否延续至60日，并核对利润增长'
        elif n60 is not None and e60 is not None and n60<0 and e60<0:
            s['research_state']='资金与趋势偏弱'
            s['next_check']='先观察资金和相对走势企稳，不因单日流入上调研究优先级'
        elif metrics:
            s['research_state']='资金价格分歧'
            s['next_check']='核对近期流入是否只是反弹，检查盈利或事件驱动'
        elif s.get('flow_state')=='5/10日均净流入' and s['financials'].get('disclosed_companies',0)>=3 and s['financials'].get('growth_breadth',0)>=.5:
            s['research_state']='资金与盈利共振'
            s['next_check']='核对当前估值与盈利可持续性；该状态尚未确认中期价格趋势'
        else:
            s['research_state']='短期净流入' if s.get('flow_state')=='5/10日均净流入' else '短期净流出' if s.get('flow_state')=='5/10日均净流出' else '资金方向分歧'
            s['next_check']='按20/60日历史资金与相对强弱继续验证'
        # Keep machine compatibility, but do not repeat a generic disclaimer per displayed row.
        s['investment_status']=s['research_state']
    company_rows=enrichment.get('financial_candidates',[])
    by_sector=Counter(r['industry_code'] for r in company_rows)
    for s in report['sectors']: s['growing_company_count']=by_sector[s['code']]
    order={'持续改善':0,'资金与盈利共振':1,'短期升温':2,'资金价格分歧':2,'短期净流入':3,'资金方向分歧':4,'短期净流出':5,'资金与趋势偏弱':6}
    research=sorted(report['sectors'],key=lambda s:(order[s['research_state']],s.get('financials',{}).get('disclosed_companies',0)<3,-(s.get('flow_watch_score') or 0)))
    if report.get('investment_horizon'):
        months=report['investment_horizon']['months']
        flow_weight=.8 if months<=2 else .5 if months<=6 else .25 if months<=12 else .15
        for s in report['sectors']:
            f=s['financials'];breadth=(f.get('growth_breadth') or 0)*100 if f.get('disclosed_companies',0)>=3 else 0
            s['horizon_research_score']=round(flow_weight*(s.get('flow_watch_score') or 0)+(1-flow_weight)*breadth,1)
        research=sorted(research,key=lambda s:-s['horizon_research_score'])
    # Only directly comparable company coverage can support industry -> company selection.
    actionable=[s for s in research if s['financials'].get('disclosed_companies',0)>=3]
    report['research_priorities']=[s['code'] for s in actionable[:10]]
    report['market_observation_priorities']=[s['code'] for s in research if s['financials'].get('disclosed_companies',0)<3][:5]
    report['industry_scope_note']='优先行业须直接匹配至少3家已披露公司财报；财报映射不足的细分板块单列市场观察，不借用父子行业补齐。'
    focus=set(report['research_priorities'])
    company_rows=sorted(company_rows,key=lambda r:(r['industry_code'] not in focus, report['research_priorities'].index(r['industry_code']) if r['industry_code'] in focus else 999))
    report['fundamental_candidates']=company_rows
    report['enrichment_scope']=dict(period=enrichment.get('period'),statements=enrichment.get('financial_scope',{}),history_sectors=len(enrichment.get('history',{})),
                                    generated_at=enrichment.get('generated_at'),issues=enrichment.get('issues',[]),benchmark='沪深300',
                                    sector_count=len(report['sectors']),matched_growth_companies=len(company_rows))
    report['market_summary']=dict(flow_states=dict(Counter(s['flow_state'] for s in report['sectors'])),research_states=dict(Counter(s['research_state'] for s in report['sectors'])))
    return report


def money(v): return '—' if v is None else f'{v/1e8:+.2f}'
def pct(v): return '—' if v is None else f'{v*100:+.1f}%'
def plain_ai(report):
    ai=report.get('ai') or {}
    result=ai.get('result')
    if not isinstance(result,dict):
        sectors={s['code']:s for s in report['sectors']}
        focus=[sectors[k] for k in report.get('research_priorities',[])[:3] if k in sectors]
        text='；'.join(s['name']+'：'+s.get('research_state','资金观察')+'，10日净额'+money(s.get('net_10d_yuan'))+'亿元，已披露公司的收入利润双增长占比 '+pct(s.get('financials',{}).get('growth_breadth')) for s in focus)
        return [('本地计算摘要',text or '行业资金明细见上表。'),('公司筛选结果',f"已匹配 {len(report.get('fundamental_candidates',[]))} 家收入与利润同比双增长的盈利公司，优先核对经营现金流覆盖，再检查估值与交易状态。"),('解读方式','本次由可复算指标直接生成摘要。'+('DeepSeek未完成：'+ai['error'] if ai.get('error') else '可另行启动DeepSeek，对同一批证据进行中文解读。'))]
    names={s['evidence_id']:s['name'] for s in report['sectors']}
    for collection in ('companies','fundamental_candidates'):
        names.update({r['evidence_id']:r['name'] for r in report.get(collection,[])})
    names.update({r['evidence_id']:r['name'] for r in report.get('stock_recommendations',{}).get('stocks',[])})
    names.update({r['evidence_id']:r['name'] for r in report.get('watchlists',{}).get('rows',[])})
    for source in report.get('research_context',{}).get('macro',{}).get('sources',[]):names[source['evidence_id']]=source['title']
    for company in report.get('research_context',{}).get('companies',[]):
        if company.get('profile',{}).get('evidence_id'):names[company['profile']['evidence_id']]=company['name']+'业务资料'
        for doc in company.get('documents',[]):names[doc['evidence_id']]=company['name']+' '+doc['title']
    paragraphs=[('研究摘要',result['summary'])]
    for i,item in enumerate(result.get('findings',[]),1):
        labels='、'.join(names.get(k,k) for k in item['evidence_ids'])
        paragraphs.append((f'观察{i}',item['claim'].replace('本次JSON中','本次材料中')+'（依据：'+labels+'）'))
    if result.get('missing_evidence'): paragraphs.append(('下一步验证','；'.join(result['missing_evidence']).replace('本次JSON中','本次材料中')))
    return paragraphs


def render_report(report,out):
    e=lambda v:html.escape(str(v))
    sectors=report['sectors'];sm={s['code']:s for s in sectors}
    scope=report.get('enrichment_scope',{})
    candidates=report.get('fundamental_candidates',[])
    priorities=[sm[k] for k in report.get('research_priorities',[]) if k in sm]
    show_history=any(s.get('history_metrics',{}).get('net_60d_yuan') is not None for s in sectors)
    source_times=report.get('data_times',{})
    fresh=source_times.get('last') or '未知'
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>行业轮动与公司研究周报</title><style>
:root{color-scheme:light}body{font:16px/1.7 system-ui,-apple-system,sans-serif;background:#f2f5fa;color:#1b2b42;margin:0}main{max-width:1380px;margin:0 auto;padding:26px}header{background:#12324e;color:#fff;border-radius:16px;padding:28px}h1{font-size:30px;margin:0}h2{font-size:22px;margin-top:30px}h3{font-size:17px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}.card,section{background:white;border:1px solid #e2e7ef;border-radius:12px;padding:18px}.card strong{font-size:23px;color:#0d5770}.muted{color:#64748b;font-size:14px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px;white-space:nowrap}th,td{text-align:right;padding:10px;border-bottom:1px solid #e7ebf1}th:first-child,td:first-child{text-align:left}th{background:#edf2f8}input{font:inherit;padding:10px;border:1px solid #c9d3e0;border-radius:8px}details{margin-top:18px}.tag{display:inline-block;background:#edf5fc;padding:3px 9px;border-radius:6px}a{color:#096383}p{margin:8px 0}li{margin:5px 0}@media(max-width:650px){main{padding:12px}h1{font-size:24px}}
</style><main><header><h1>行业轮动与公司研究周报</h1><p>研究周期 3—6个月 · 先看资金与趋势，再核对盈利与现金流</p></header>'''
    page+=f'<p class="muted">报告生成：{e(report["asof"])}　资金快照：{e(fresh)}　财报期：{e(scope.get("period") or "尚未下载")}</p>'
    horizon=report.get('investment_horizon')
    if horizon:
        page=page.replace('研究周期 3—6个月','预计持有 '+e(horizon['label']))
        labels={'cash_quality':'现金流质量','growth':'增长','valuation':'估值','sector_flow':'行业资金'}
        page+='<section><b>本次筛选偏好：</b>'+e(horizon['focus'])+'<p>股票评分权重：'+e('；'.join(labels[k]+' '+str(v)+'%' for k,v in horizon['weights'].items()))+'</p><p>'+e(horizon['validation'])+'</p></section>'
    capital=report.get('capital_settings')
    page+='<p><b>本次计划资金：</b>'+e(capital['budget_yuan']+'元 · '+capital['label']+'风格' if capital else '尚未设置，可在应用菜单9设置')+'</p>'
    page+='<nav style="position:sticky;top:0;background:#fff;padding:10px;border-radius:8px;z-index:2"><a href="#industry-route">①优先行业</a>　<a href="#review">②股票与复核</a>　<a href="#allocation">③资金分配</a>　<a href="#supplementary">④补充观察</a>　<a href="#research-lab">⑤增强研究</a></nav>'
    page+='<div class="cards">'+''.join(f'<div class="card">{e(label)}<br><strong>{e(value)}</strong></div>' for label,value in [('板块快照',len(sectors)),('历史序列已核验',scope.get('history_sectors',0)),('季报公司覆盖',scope.get('statements',{}).get('RPT_DMSK_FN_INCOME',0)),('收入利润双增长线索',len(candidates))])+'</div>'
    health=report.get('health',{})
    if health:
        page+='<section style="margin-top:18px"><b>运行状态：</b>'+e(health.get('status','待检查'))+'<p class="muted">板块、财报、行情的更新状态与接口覆盖见页末；工程检查不等于选股收益验证。</p></section>'
    page+='<h2>本期要点</h2><section>'
    states=report.get('market_summary',{}).get('research_states',{})
    page+='<p>'+e('当前可计算的研究状态中，'+ '、'.join(f'{k}{states.get(k,0)}个' for k in ('持续改善','资金与盈利共振','短期升温','资金与趋势偏弱'))+'。未核验历史的板块仅参与短期资金观察。')+'</p>'
    inflow=sorted([s for s in sectors if s.get('net_10d_yuan') is not None],key=lambda s:-s['net_10d_yuan'])[:3]
    outflow=sorted([s for s in sectors if s.get('net_10d_yuan') is not None],key=lambda s:s['net_10d_yuan'])[:3]
    page+='<p><b>10日净额居前：</b>'+e('；'.join(s['name']+' '+money(s['net_10d_yuan'])+'亿元' for s in inflow))+'</p>'
    page+='<p><b>10日流出居前：</b>'+e('；'.join(s['name']+' '+money(s['net_10d_yuan'])+'亿元' for s in outflow))+'</p>'
    page+='<p class="muted">父子板块可能重叠，以上金额不加总；“持续改善”等状态为可复算的研究分类，不是收益承诺。</p></section>'
    from .industry_selection import html_section as industry_html,supplementary_html,markdown_section as industry_md,supplementary_markdown
    page+=industry_html(report)
    page+='<div id="review"></div>'
    from .model_review import html_section,markdown_section
    page+=html_section(report)
    from .capital_allocation import html_section as capital_html,markdown_section as capital_md
    page+='<div id="allocation"></div>'+capital_html(report)
    picks=report.get('stock_recommendations',{})
    page+='<h2>优先行业内：股票主名单明细</h2><section><p>以下仅为第一阶段的量化提案，全部来自本期优先行业；大模型复核结论与最终计划见上方，不强行凑满5只。</p><div class="cards">'
    for r in picks.get('stocks',[]):
        q=r['quote']
        page+=f'<article class="card"><h3>{e(r["name"])}（{e(r["symbol"])}）</h3><p>{e(r["industry_name"])} · 量化初筛通过，最终处理以大模型复核表为准</p><p>行情参考价 {q["price"]:.2f}元；供应商PE {q["pe_vendor"]:.2f}倍；PB {q["pb"]:.2f}倍。</p><p class="muted">行情时间：{e(q["quote_at"])}</p><ul>'+''.join('<li>'+e(v)+'</li>' for v in r['reasons'])+'</ul><p><b>初筛提出的买入前提：</b>'+e(r['entry_condition'])+'</p><p><b>重新评估条件：</b>'+e(r['invalidation'])+'</p><p class="muted">'+e('；'.join(r['risks']))+'</p></article>'
    page+='</div>'
    if not picks.get('stocks'):page+='<p>当前没有同时通过财务、估值和有效行情检查的股票，不用缺失数据凑出买入名单。</p>'
    if picks.get('quote_update_error'):page+='<p>'+e(picks['quote_update_error'])+'</p>'
    limits=picks.get('policy',{})
    page+='<p class="muted">最多5只、每行业最多2只。行情PE为供应商快照口径，尚未交叉核验为TTM。'+e(f"本期PE上限{limits.get('max_pe','待配置')}、PB上限{limits.get('max_pb','待配置')}、现金流覆盖下限{limits.get('min_cash_cover','待配置')}，均为待验证的初始条件，不代表合理价值或上涨概率。")+'</p></section>'
    page+=supplementary_html(report)
    stability=picks.get('sensitivity',[])
    if stability:
        page+='<details><summary>名单对门槛变化的敏感性</summary><section><p>在同一批已核验行情中改变单个门槛，不进行历史收益寻优。</p><ul>'+''.join('<li>'+e(r['scenario'])+'：原名单保留 '+str(r['retained_from_base'])+'/'+str(r['base_count'])+' 只。</li>' for r in stability)+'</ul><p class="muted">名单保留率不是上涨概率，也不表示参数已验证有效。</p></section></details>'
    from .context_report import render_html
    page+=render_html(report)
    from .independent_flow import html_section as independent_html
    page+=independent_html(report)
    from .research_lab import html_section as lab_html,paragraphs as lab_paragraphs
    page+=lab_html(report)
    page+='<details><summary>全部优先行业的资金与盈利明细（'+str(len(priorities))+'个，与主名单选股范围一致）</summary><div class="cards">'
    for s in priorities:
        m=s.get('history_metrics',{})
        funds=(f'20日净额 {money(m.get("net_20d_yuan"))}亿元；60日净额 {money(m.get("net_60d_yuan"))}亿元。' if m.get('net_60d_yuan') is not None else f'5日净额 {money(s.get("net_5d_yuan"))}亿元；10日净额 {money(s.get("net_10d_yuan"))}亿元。')
        trend=(f'60日相对沪深300 {pct(m.get("excess_60d"))}；60日正流入占比 {pct(m.get("positive_60d"))}。' if m.get('excess_60d') is not None else f'近5日相对前5日净额变化 {money(s.get("net_change_5d_yuan"))}亿元。')
        f=s.get('financials',{})
        business=f'已披露 {f.get("disclosed_companies",0)} 家；收入利润双增长占比 {pct(f.get("growth_breadth"))}。' if f else ''
        page+=f'<article class="card"><h3>{e(s["name"])} <small>{e(s["code"])}</small></h3><span class="tag">{e(s["research_state"])}</span><p>{funds}</p><p>{trend}</p><p>{e(business)}</p><p>匹配收入利润双增长公司 {s.get("growing_company_count",0)} 家。</p><p class="muted">{e(s.get("next_check",""))}</p></article>'
    page+='</div></details><details><summary>查看全部行业资金、盈利覆盖与趋势数据（支持搜索）</summary><section><input id="filter" placeholder="搜索行业名称、代码或状态"><div class="scroll"><table><thead><tr>'+''.join('<th>'+v+'</th>' for v in (['行业','代码','5日净额 亿','10日净额 亿','近5日较前5日 亿']+(['60日净额 亿','60日相对收益'] if show_history else ['已披露公司','双增长占比'])+['研究状态','较上次排名']))+'</tr></thead><tbody id="rows">'
    for s in sectors:
        m=s.get('history_metrics',{});delta=s.get('rank_change')
        values=[s['name'],s['code'],money(s.get('net_5d_yuan')),money(s.get('net_10d_yuan')),money(s.get('net_change_5d_yuan'))]+([money(m.get('net_60d_yuan')),pct(m.get('excess_60d'))] if show_history else [s.get('financials',{}).get('disclosed_companies','—'),pct(s.get('financials',{}).get('growth_breadth'))])+[s.get('research_state','资金观察'),'首次记录' if delta is None else f'{delta:+d}']
        page+='<tr>'+''.join('<td>'+e(v)+'</td>' for v in values)+'</tr>'
    page+='</tbody></table></div></section></details><h2>公司研究线索</h2><section><p>按实际披露季报筛出收入和净利润同比均增长且盈利的公司；现金流单独展示。估值、审计及交易状态未核实前，不生成买入指令。</p><div class="scroll"><table><tr>'+''.join('<th>'+v+'</th>' for v in ['公司','代码','行业','营收同比','利润同比','现金流/净利润','报告期','披露日期'])+'</tr>'
    for r in candidates[:40]:
        values=[r['name'],r['symbol'],r['industry_name'],f"{r['revenue_growth_pct']:+.1f}%",f"{r['profit_growth_pct']:+.1f}%",'金融业另评' if r['cash_profit_ratio'] is None and any(k in r['industry_name'] for k in ('银行','保险','证券')) else '—' if r['cash_profit_ratio'] is None else f"{r['cash_profit_ratio']:.2f}",r['report_period'],r['notice_date']]
        page+='<tr>'+''.join('<td>'+e(v)+'</td>' for v in values)+'</tr>'
    page+='</table></div>'
    if not candidates: page+='<p>本次公司数据未成功取得或未匹配到行业，具体失败原因见页末运行记录。</p>'
    page+='<p class="muted">展示前40条公司线索，完整明细见公司线索.csv。按已验证代码和行业名称匹配；现金流比值口径含合并现金流与归母净利润差异，异常高比值仍需核对。</p></section><h2>综合研究解读</h2><section>'
    for title,text in plain_ai(report): page+='<h3>'+e(title)+'</h3><p>'+e(text).replace('\n','<br>')+'</p>'
    page+='</section><details><summary>数据来源、计算方法与运行记录</summary><section><p>公开来源：东方财富行业快照及历史资金序列、东方财富季报数据库；基准：腾讯沪深300。原始响应和时间戳均保存在项目data目录。</p><p>资金变化=最近5日净额−前5日净额，前5日=10日−5日，避免把重叠窗口当独立证据。20/60/120日收益按相同起止日期与基准比较；无日期匹配不计算超额收益。</p><p>持续改善：60日净额为正、相对收益为正、正流入日占比≥50%；短期升温：20日净额和相对收益为正；偏弱：60日净额和相对收益均负。其余归为分歧。阈值未作收益优化。</p><ul>'+''.join('<li>'+e(v)+'</li>' for v in report.get('limitations',[]))+'</ul>'
    page+='<p>财报期：'+e(scope.get('period','—'))+'；已核验历史序列：'+str(scope.get('history_sectors',0))+'个板块；财报公司线索：'+str(len(candidates))+'条。</p>'
    issues=Counter(str(v.get('error','接口异常')) for v in scope.get('issues',[]))
    if issues: page+='<p>本次接口记录：</p><ul>'+''.join('<li>'+e(message)+'（'+str(count)+'项）</li>' for message,count in issues.items())+'</ul>'
    if report.get('ai',{}).get('diagnostics'):
        d=report['ai']['diagnostics'];page+='<p>AI调用：模型 '+e(d.get('model','未知'))+'；耗时 '+e(d.get('elapsed_seconds','未知'))+'秒；结束状态 '+e(d.get('finish_reason',d.get('error_type','未知')))+'</p>'
    page+='</section></details><script>document.getElementById("filter").addEventListener("input",e=>{for(const r of document.querySelectorAll("#rows tr"))r.hidden=!r.textContent.toLowerCase().includes(e.target.value.toLowerCase())})</script></main></html>'
    out.mkdir(parents=True,exist_ok=True)
    (out/'index.html').write_text(page,encoding='utf-8')
    # A plain-language companion report for weekly archiving and reading outside the browser.
    lines=['# 行业轮动与公司研究周报','',f'生成时间：{report["asof"]}；资金源时间：{fresh}。','', '## 本期研究方向','']
    for s in priorities[:10]:
        m=s.get('history_metrics',{})
        facts=(f"60日净额{money(m.get('net_60d_yuan'))}亿元，相对沪深300 {pct(m.get('excess_60d'))}" if m.get('excess_60d') is not None else f"5日净额{money(s.get('net_5d_yuan'))}亿元，10日净额{money(s.get('net_10d_yuan'))}亿元")
        lines += [f"- {s['name']}（{s['code']}）：{s['research_state']}。{facts}。{s.get('next_check','')}"]
    if horizon:lines+=['','预计持有：'+horizon['label']+'。'+horizon['focus'],'',horizon['validation'],'']
    lines+=industry_md(report)
    lines+=['']+markdown_section(report)
    lines+=capital_md(report)
    lines+=supplementary_markdown(report)
    lines+=['','## 量化提案明细（不是最终买入名单）','']
    for r in report.get('stock_recommendations',{}).get('stocks',[]):
        q=r['quote']
        lines += [f"### {r['name']}（{r['symbol']}）",'',f"{r['industry_name']}；参考价{q['price']:.2f}元，供应商PE {q['pe_vendor']:.2f}倍。行情时间：{q['quote_at']}。",'', '入选理由：'+'；'.join(r['reasons'])+'。','', '买入前提：'+r['entry_condition']+'。','', '重新评估条件：'+r['invalidation']+'。','', '主要风险：'+'；'.join(r['risks'])+'。','']
    lines+=['','## 公司财务线索','', '|公司|行业|营收同比|利润同比|现金流/利润|报告期|','|---|---|---:|---:|---:|---|']
    for r in candidates[:20]:
        cover='—' if r['cash_profit_ratio'] is None else f"{r['cash_profit_ratio']:.2f}"
        lines.append(f"|{r['name']} {r['symbol']}|{r['industry_name']}|{r['revenue_growth_pct']:+.1f}%|{r['profit_growth_pct']:+.1f}%|{cover}|{r['report_period']}|")
    from .context_report import render_markdown
    lines+=render_markdown(report)
    from .independent_flow import markdown_section as independent_markdown
    lines+=independent_markdown(report)
    if report.get('research_lab'):
        lines+=['','## 增强量化研究与数据审计','']
        for line in lab_paragraphs(report):lines += [line,'']
    lines+=['','## 综合解读','']
    for title,text in plain_ai(report):lines += ['### '+title,'',text,'']
    (out/'研究周报.md').write_text('\n'.join(lines),encoding='utf-8')
    if candidates:
        import csv
        with (out/'公司线索.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(candidates[0]));w.writeheader();w.writerows(candidates)
    else: (out/'公司线索.csv').unlink(missing_ok=True)

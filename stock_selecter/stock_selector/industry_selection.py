"""One traceable industry scope shared by selection and all report formats."""
from collections import Counter
import html


def audit(report,result):
    from .investment_horizon import get
    from .recommendations import base_reasons,pool
    horizon=get(report);sectors={s['code']:s for s in report['sectors']}
    quoted_pool={c['symbol'] for c in pool(report)}
    eligible={c['symbol'] for c in result.get('eligible_stocks',[])}
    rejected={c['symbol']:c['reasons'] for c in result.get('rejected',[])}
    output=[]
    for rank,code in enumerate(report.get('research_priorities',[]),1):
        s=sectors.get(code,{})
        companies=[c for c in report.get('fundamental_candidates',[]) if c['industry_code']==code]
        picks=[c for c in result['stocks'] if c['industry_code']==code]
        reasons=Counter();in_pool=0;passed=0
        for c in companies:
            initial=base_reasons(c,s,horizon,result['policy'])
            if initial:reasons.update(initial)
            elif c['symbol'] not in quoted_pool:reasons.update(['本次行情池数量上限，尚未核验'])
            else:
                in_pool+=1
                if c['symbol'] in eligible:passed+=1
                else:reasons.update(rejected.get(c['symbol'],['尚未完成个股核验']))
        if report.get('health',{}).get('blocked'):
            status='暂停选股';explanation='数据健康检查未通过，保留研究方向，暂停生成股票主名单'
        elif picks:
            status='已选出候选';explanation='主名单：'+'、'.join(c['name'] for c in picks)
        elif not companies:
            status='暂无可筛公司'
            explanation=('该行业尚无可比财报匹配；不借用父子板块公司补位' if not s.get('financials',{}).get('disclosed_companies') else '已匹配财报，但暂无满足盈利且收入、利润同比双增长的公司')
        elif passed:
            status='通过门槛，未入主名单';explanation='受最多5只、每行业最多2只和综合排序约束；不代表公司被否决'
        else:
            status='本期未选出候选';explanation='；'.join(f'{reason}（{count}家）' for reason,count in reasons.most_common(4))
        output.append(dict(rank=rank,code=code,name=s.get('name',code),state=s.get('research_state',s.get('flow_state','待核验')),score=s.get('horizon_research_score'),financial_candidates=len(companies),quote_pool=in_pool,passed=passed,selected=[c['symbol'] for c in picks],selected_names=[c['name'] for c in picks],status=status,explanation=explanation,reasons=dict(reasons)))
    return output


def html_section(report):
    e=lambda v:html.escape(str(v));result=report.get('stock_recommendations',{});rows=result.get('industry_audit',[])
    out='<h2 id="industry-route">先选行业，再选公司</h2><section><p>下列全部优先行业共同构成主名单的选股范围；按行业代码严格匹配。行业外公司单列补充观察，不补入主名单、不进入资金分配。</p>'
    if report.get('industry_scope_note'):out+='<p>'+e(report['industry_scope_note'])+'</p>'
    if not rows:return out+'<p>尚无本期行业筛选核验结果，请更新报告。</p></section>'
    out+='<div class="scroll"><table><tr><th>顺序</th><th>优先行业</th><th>财务增长公司</th><th>入选主名单</th><th>筛选结果与原因</th></tr>'
    for r in rows:
        values=[r['rank'],r['name']+' '+r['code'],r['financial_candidates'],'、'.join(r['selected_names']) or '无',r['status']+'：'+r['explanation']]
        out+='<tr>'+''.join('<td style="white-space:normal;min-width:65px">'+e(v)+'</td>' for v in values)+'</tr>'
    out+='</table></div><p class="muted">同一家公司可能在供应商的父子板块中被讨论，本程序只按财报的明确行业归属选股，不推测重分类。原因统计可能一家公司命中多项，不可加总。</p>'
    sectors={s['code']:s for s in report.get('sectors',[])}
    observation=[sectors[c]['name'] for c in report.get('market_observation_priorities',[]) if c in sectors]
    if observation:out+='<details><summary>仅作市场观察的细分板块（暂不作为选股方向）</summary><p>'+e('、'.join(observation))+'</p><p>直接匹配的可比公司财报不足3家；保留资金观察，不据此生成股票主名单。</p></details>'
    return out+'</section>'


def supplementary_html(report):
    e=lambda v:html.escape(str(v));stocks=report.get('stock_recommendations',{}).get('supplementary_stocks',[])
    out='<h2 id="supplementary">优先行业以外：补充观察</h2><section><p>以下公司虽通过个股初筛，但所属行业未进入本期优先方向，仅作外围观察；未送入主名单的逐股复核或资金配置。</p>'
    if not stocks:return out+'<p>本期没有已取得有效行情且通过门槛的外围观察公司。</p></section>'
    out+='<ul>'
    for c in stocks:out+='<li>'+e(c['name']+'（'+c['symbol']+'） · '+c['industry_name']+' · 初筛分 '+str(c['recommendation_score']))+'</li>'
    return out+'</ul></section>'


def markdown_section(report):
    lines=['','## 先选行业，再选公司','','主名单只在以下全部优先行业内选股；外围公司单列观察，不补位、不进入资金配置。','']
    if report.get('industry_scope_note'):lines += [report['industry_scope_note'],'']
    for row in report.get('stock_recommendations',{}).get('industry_audit',[]):
        lines += [f"{row['rank']}. {row['name']}（{row['code']}）：{row['status']}。{row['explanation']}",'']
    return lines


def supplementary_markdown(report):
    lines=['','## 优先行业以外：补充观察','','外围观察不属于主名单，不进入本期资金配置。','']
    for c in report.get('stock_recommendations',{}).get('supplementary_stocks',[]):lines += [c['name']+'（'+c['symbol']+'）：'+c['industry_name']+'，初筛分 '+str(c['recommendation_score'])+'。','']
    return lines

"""Human-readable macro and company context, separating facts from hypotheses."""
import html


def sections(report):
    context=report.get('research_context',{});macro=context.get('macro',{});output=[]
    indicators=macro.get('indicators',[])
    source_map={s['evidence_id']:s for s in macro.get('sources',[])}
    paragraphs=[]
    for i in indicators:
        delta=i.get('change');change=f'，较前期{delta:+.2f}个百分点' if delta is not None else ''
        percentile=f"；本次取得{i['sample_months']}期中的分位{i['percentile']*100:.0f}%" if i.get('percentile') is not None else ''
        unit='' if i['unit']=='指数' else i['unit']
        paragraphs.append(f"{i['name']}（{i['period']}）：{i['value']:.2f}{unit}{change}，{i['state']}{percentile}。")
    views=report.get('ai',{}).get('context_analysis',{})
    if views.get('macro_review'):paragraphs.append('大模型推断：'+views['macro_review']['assessment'])
    if paragraphs:
        paragraphs.append(macro.get('method',''))
        output.append(dict(title='宏观环境：需求、价格与信用',paragraphs=paragraphs,sources=[dict(title=s['title'],url=s['url'],date=s['published_at']) for s in macro.get('sources',[])]))
    exposures=context.get('industry_exposures',[])[:6]
    if exposures:output.append(dict(title='优先行业的宏观传导',paragraphs=[v['industry']+'：'+'；'.join(v['effects']) for v in exposures],sources=[]))
    outlook={c['symbol']:c for c in views.get('company_outlooks',[])}
    cases={'optimistic':'乐观情景','base':'基准情景','pessimistic':'悲观情景'}
    for company in context.get('companies',[]):
        profile=company.get('profile',{});p=[]
        if profile.get('business'):p.append('业务概况（供应商整理）：'+profile['business'])
        if profile.get('introduction'):p.append('公司介绍（未独立核验宣传性表述）：'+profile['introduction'])
        if profile.get('product_mix'):p.append('业务构成：'+profile['product_mix']+'；供应商列示比例：'+profile.get('product_mix_ratios','未提供')+'。相关期末提示：'+profile.get('data_period_hint','未知')+'。')
        p+=['宏观传导假说：'+v for v in company.get('macro_exposure',{}).get('effects',[])]
        sources=[]
        if profile.get('source_url'):sources.append(dict(title='公司业务资料（供应商整理，观察时间'+profile.get('observed_at','')[:10]+'）',url=profile['source_url'],date=profile.get('observed_at','')[:10]))
        for d in company.get('documents',[]):
            p.append('已检索原始公告：'+d['title']+'，披露日期'+d['published_at'][:10]+'。')
            # A short primary-source excerpt is explicitly distinct from the model's inference.
            excerpt=next((x for x in d.get('excerpts',[]) if x['topic']=='发展方向'),None)
            if excerpt:p.append('管理层发展表述节选（PDF第'+str(excerpt['pdf_page'])+'页）：'+excerpt['excerpt'][:200])
            sources.append(dict(title=d['title'],url=d['source_url'],date=d['published_at'][:10]))
        for failed in company.get('failed_documents',[]):
            p.append('公告正文未纳入证据：'+failed['title']+'。原文链接保留供人工查看，不能据此声称已核对内容。')
            sources.append(dict(title='未读取正文：'+failed['title'],url=failed['source_url'],date=failed['published_at'][:10]))
        view=outlook.get(company['symbol'])
        if view:
            p.append('大模型归纳（推断）：'+view['business_summary'])
            p.append('发展驱动：'+'；'.join(view['drivers']))
            p.append('反方风险：'+'；'.join(view['risks']))
            for s in view['scenarios']:p.append(cases[s['case']]+'：成立条件 '+'；'.join(s['assumptions'])+'；跟踪指标 '+'；'.join(s['watch_items']))
        else:
            p.append('以下为基于当前财务与主营的条件研究框架，不是大模型已经验证的前景或收益预测：')
            for s in company.get('scenarios',[]):p.append(s['label']+'：'+s['condition'])
        if company.get('issues'):p.append('本公司资料运行记录：'+'；'.join(company['issues']))
        output.append(dict(title=company['name']+'（'+company['symbol']+'）发展介绍与前景',paragraphs=p,sources=sources))
    return output


def render_html(report):
    e=lambda s:html.escape(str(s));blocks=sections(report)
    if not blocks:return ''
    out='<h2>宏观分析与公司发展研究</h2><p class="muted">公开原文、供应商整理、管理层说法和模型推断分别标注；情景为成立条件，不是收益预测。</p>'
    for block in blocks:
        out+='<section style="margin:14px 0"><h3>'+e(block['title'])+'</h3>'
        out+=''.join('<p>'+e(p)+'</p>' for p in block['paragraphs'])
        if block['sources']:
            out+='<details><summary>原始资料与发布日期</summary><ul>'
            for source in block['sources']:out+='<li><a href="'+e(source['url'])+'" target="_blank" rel="noopener noreferrer">'+e(source['title'])+'</a> · '+e(source['date'])+'</li>'
            out+='</ul></details>'
        out+='</section>'
    return out


def render_markdown(report):
    result=['','## 宏观分析与公司发展研究','']
    for block in sections(report):
        result+=['### '+block['title'],'']
        for text in block['paragraphs']:result+=[text,'']
        for s in block['sources']:result+=['来源：['+s['title']+']('+s['url']+')，'+s['date']+'。','']
    return result

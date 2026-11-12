"""Quantitative proposal -> independent LLM challenge -> gated final decision."""
import hashlib
import json
import math

LABELS={'approve_conditional':'有条件通过','defer':'暂缓','reject':'否决','plan_incomplete':'已复核，计划待补全','invalid':'已返回，需核实','pending':'待大模型复核'}


def candidate_hash(report):
    picks=report.get('stock_recommendations',{}).get('stocks',[])
    # Excludes generation time; includes every proposed stock and its price/fundamental facts.
    context=[{k:s.get(k) for k in ('code','name','flow_state','net_5d_yuan','net_10d_yuan','ratio_5d_pct','ratio_10d_pct','history_metrics','financials')} for s in report.get('sectors',[])]
    from .context_research import evidence_packet
    facts={'research_context':evidence_packet(report.get('research_context',{})),'stocks':picks,'sector_context':context,'peer_context':report.get('fundamental_candidates',[])[:20],'source_times':report.get('data_times'), 'model':report.get('model_version'),'limitations':report.get('limitations')}
    if 'investment_horizon' in report:facts['investment_horizon']=report['investment_horizon']
    if report.get('research_lab'):
        lab=report['research_lab'];facts['parallel_research']={k:lab.get(k) for k in ('version','config_sha256','evidence_sha256','factors_sha256')}
    if report.get('stock_recommendations',{}).get('selection_policy'):
        facts['selection_scope']={'policy':report['stock_recommendations']['selection_policy'],'industries':report.get('research_priorities',[])}
    return hashlib.sha256(json.dumps(facts,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()


def validate_reviews(result,candidates,allowed_ids):
    raw=result.get('stock_reviews')
    if not candidates:
        if raw not in (None,[]):raise ValueError('没有初筛候选，不允许大模型新增推荐')
        return []
    if not isinstance(raw,list):raise ValueError('大模型缺少逐股复核，不能视为已通过')
    expected={c['symbol']:c for c in candidates};seen=set()
    for r in raw:
        if not isinstance(r,dict) or r.get('symbol') not in expected or r['symbol'] in seen:raise ValueError('大模型复核出现未知或重复股票')
        seen.add(r['symbol'])
        if r.get('verdict') not in ('approve_conditional','defer','reject'):raise ValueError('复核结论必须为通过、暂缓或否决')
        for key in ('reason','counter_case'):
            if not isinstance(r.get(key),str) or not r[key].strip():raise ValueError('复核必须包含独立理由和反方论证')
        if not isinstance(r.get('blockers'),list) or any(not isinstance(v,str) for v in r['blockers']):raise ValueError('复核缺口结构无效')
        ids=r.get('evidence_ids')
        if not isinstance(ids,list) or any(not isinstance(i,str) for i in ids) or expected[r['symbol']]['evidence_id'] not in ids or any(i not in allowed_ids for i in ids):raise ValueError('逐股复核必须引用该股票的真实初筛证据')
        plan=r.get('buy_plan')
        if r['verdict']!='approve_conditional':
            if isinstance(plan,dict) and plan.get('tranches'):raise ValueError('暂缓或否决股票不得同时输出买入分批计划')
            continue
        if not isinstance(plan,dict):raise ValueError('复核缺少买入或暂缓计划')
        for key in ('entry_condition','cancel_condition','exit_review_condition'):
            if not isinstance(plan.get(key),str) or not plan[key].strip():raise ValueError('计划缺少触发、取消或退出复核条件')
        tranches=plan.get('tranches')
        if not isinstance(tranches,list):raise ValueError('分批计划必须为列表')
        if r['verdict']!='approve_conditional' and tranches:raise ValueError('暂缓或否决股票不得同时输出买入分批计划')
        if r['verdict']=='approve_conditional':
            if r['blockers']:raise ValueError('仍有待解决阻断项的股票不能通过复核')
            if not tranches or len(tranches)>4:raise ValueError('通过的股票必须提供1至4批条件计划')
            fractions=[]
            for t in tranches:
                if not isinstance(t,dict) or isinstance(t.get('fraction'),bool) or not isinstance(t.get('fraction'),(int,float)) or not math.isfinite(t['fraction']) or not 0<t['fraction']<=1 or not isinstance(t.get('condition'),str) or not t['condition'].strip():raise ValueError('分批比例或条件无效')
                fractions.append(t['fraction'])
            if not math.isclose(sum(fractions),1,abs_tol=1e-6):raise ValueError('分批比例合计必须为计划金额的100%')
    if seen!=set(expected):raise ValueError('大模型没有逐只覆盖全部初筛候选')
    return raw


def collect_reviews(result,candidates,allowed_ids):
    """Keep each stock's opinion independently; a bad plan never erases other reviews."""
    raw=result.get('stock_reviews',[])
    if not isinstance(raw,list):raw=[]
    decisions={};issues=[]
    expected={c['symbol']:c for c in candidates}
    grouped={}
    for r in raw:
        if not isinstance(r,dict) or not isinstance(r.get('symbol'),str) or r.get('symbol') not in expected:
            issues.append(dict(symbol=None,error='返回了未知股票或无效记录，未纳入名单'));continue
        grouped.setdefault(r['symbol'],[]).append(r)
    for symbol,rows in grouped.items():
        r=json.loads(json.dumps(rows[0],ensure_ascii=False))
        issue=None;status=r.get('verdict')
        plan=r.get('buy_plan')
        if isinstance(plan,dict):
            aliases={'entry_condition':('entry_trigger','trigger'),'cancel_condition':('cancel_if','cancellation_condition'),'exit_review_condition':('exit_condition','exit_trigger')}
            for field,alternatives in aliases.items():
                if not plan.get(field):
                    for alias in alternatives:
                        if isinstance(plan.get(alias),str) and plan[alias].strip():plan[field]=plan[alias];break
        try:
            if len(rows)!=1:raise ValueError('同一股票出现重复复核，不选择性采纳其中一条')
            validate_reviews({'stock_reviews':[r]},[expected[symbol]],allowed_ids)
        except (ValueError,TypeError,KeyError) as e:
            issue=str(e)
            # Only a valid, cited opinion with a broken plan can be called reviewed.
            valid_base=(r.get('verdict')=='approve_conditional' and isinstance(r.get('reason'),str) and bool(r['reason'].strip()) and isinstance(r.get('counter_case'),str) and bool(r['counter_case'].strip()) and isinstance(r.get('blockers'),list) and not r['blockers'] and isinstance(r.get('evidence_ids'),list) and all(isinstance(i,str) for i in r['evidence_ids']) and expected[symbol]['evidence_id'] in r['evidence_ids'] and all(i in allowed_ids for i in r['evidence_ids']) and len(rows)==1)
            status='plan_incomplete' if valid_base else 'invalid'
            issues.append(dict(symbol=symbol,error=issue))
        if not isinstance(r.get('buy_plan'),dict):r['buy_plan']={}
        plan=r['buy_plan']
        for field in ('entry_condition','cancel_condition','exit_review_condition'):
            if not isinstance(plan.get(field),str) or not plan[field].strip():plan[field]='模型未提供此项；系统不据此生成买入操作'
        if status!='approve_conditional':plan['tranches']=[]
        decisions[symbol]=dict(review=r,status=status,issue=issue)
    for symbol in expected:
        if symbol not in grouped:issues.append(dict(symbol=symbol,error='模型本次未覆盖该股票'))
    return decisions,issues


def attach_history(report,archive_dir):
    """Retain opinions as dated history only; no historical opinion authorizes a trade."""
    from pathlib import Path
    from datetime import datetime
    history={}
    for path in sorted(Path(archive_dir).glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        try:
            saved=json.loads(path.read_text())
            rows=(saved.get('result') or {}).get('stock_reviews',[])
            if saved.get('candidate_hash')==candidate_hash(report):continue
            if not isinstance(rows,list):continue
            for row in rows:
                if isinstance(row,dict) and row.get('symbol') not in history and row.get('verdict') in ('approve_conditional','defer','reject') and isinstance(row.get('reason'),str):
                    history[row.get('symbol')]={'verdict':LABELS[row['verdict']],'reason':row['reason'],'date':saved.get('received_at') or datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(),'source':path.name}
        except (ValueError,OSError,TypeError,AttributeError):continue
    report['historical_ai_reviews']=history


def review_availability(ai,trusted):
    if ai.get('error'):
        diag=ai.get('diagnostics') or {}
        received=diag.get('http_status')==200 and diag.get('stream_complete')
        return ('response_unusable' if received else 'request_failed',
                ('本次API已返回完整响应，但程序未能采用正文：' if received else '本次解读未完成：')+str(ai['error']))
    if not ai:return 'not_called','本期未进行大模型解读。'
    if not trusted:return 'evidence_changed','已有模型意见与本期证据版本不匹配，保留为历史参考。'
    return 'received','本期模型响应已收到；逐股意见与计划分别检查。'


def finalize(report):
    picks=report.get('stock_recommendations',{}).get('stocks',[])
    ai=report.get('ai') or {};fingerprint=candidate_hash(report)
    trusted=ai.get('candidate_hash')==fingerprint
    availability,availability_note=review_availability(ai,trusted)
    if not ai and report.get('last_ai_failure'):
        failure=report['last_ai_failure'];availability_note='本次量化研究已更新，未调用AI。上次调用（'+str(failure.get('at',''))[:19].replace('T',' ')+')未完成：'+str(failure['error'])
    if not trusted and not ai.get('error') and report.get('review_reset_reason'):
        availability='evidence_changed';availability_note=report['review_reset_reason']
    reviews=(ai.get('result') or {}).get('stock_reviews',[]) if trusted else []
    ids={s['evidence_id'] for key in ('sectors','companies','fundamental_candidates') for s in report.get(key,[]) if s.get('evidence_id')}
    from .context_research import evidence_ids
    ids.update(evidence_ids(report.get('research_context',{})))
    ids.update(s['evidence_id'] for s in picks)
    ids.update(s['evidence_id'] for s in report.get('watchlists',{}).get('rows',[]))
    by_symbol,issues=collect_reviews({'stock_reviews':reviews},picks,ids)
    decisions=[]
    for c in picks:
        accepted=by_symbol.get(c['symbol'])
        review=accepted['review'] if accepted else None
        verdict=accepted['status'] if accepted else 'pending'
        pending_note=('模型本次未返回该股票的逐股意见。' if trusted else availability_note)
        if not accepted and availability=='evidence_changed':
            pending_note=('本股待按本期行业范围重新复核；上次意见仅作历史参考。' if report.get('historical_ai_reviews',{}).get(c['symbol']) else '本股为本期候选，尚无与当前范围匹配的模型意见。')
        elif not accepted and availability in ('request_failed','response_unusable'):
            pending_note='本次接口响应未能形成有效逐股意见；具体失败原因见报告开头，量化研究结果保留。'
        elif not accepted and report.get('last_ai_failure'):
            pending_note='本股尚无本期模型意见；上次API失败原因见报告开头，量化研究结果保留。'
        from .scenario_analysis import build as build_scenario
        scenario=build_scenario(c,review,report.get('research_context'),ids,report.get('sectors',[]),report.get('investment_horizon'))
        decisions.append(dict(scenario_analysis=scenario,symbol=c['symbol'],name=c['name'],industry_name=c.get('industry_name','未知'),industry_code=c.get('industry_code'),small_model='规则评分器M0（尚未训练收益预测模型）',small_model_score=c['recommendation_score'],
                              small_model_reasons=c['reasons'],verdict=verdict,label=(LABELS[verdict] if accepted else {'response_unusable':'本次返回未能解析','request_failed':'本次解读失败','not_called':'本期未调用','evidence_changed':'证据已更新，待重审'}.get(availability,'模型本次漏审')),review=review,
                              pending_reason=pending_note if not accepted else None,historical_review=report.get('historical_ai_reviews',{}).get(c['symbol']) if not accepted else None,
                              final_action='仅在计划触发且人工核对后考虑买入' if verdict=='approve_conditional' else '不进入买入计划',
                              validation_issue=accepted['issue'] if accepted else None,
                              disagreement=verdict in ('defer','reject')))
    report['decision_pipeline']=dict(candidate_hash=fingerprint,small_model_type='透明规则评分器，不是已训练的小参数预测模型',review_model=ai.get('model'),
                                     availability=availability,availability_note=availability_note,review_current=trusted and bool(by_symbol),decisions=decisions,validation_issues=issues,
                                     final_buy_candidates=[d['symbol'] for d in decisions if d['verdict']=='approve_conditional'],
                                     counts={k:sum(d['verdict']==k for d in decisions) for k in LABELS},
                                     verification_boundary='大模型对提供的证据作反方审查，不等同独立数据源核实或对未来收益验证；程序检查代码、引用、覆盖及分批比例，文本需人工核实。')
    from .capital_allocation import build as build_allocation
    report['capital_allocation']=build_allocation(report,report['decision_pipeline'])
    return report['decision_pipeline']


def html_section(report):
    import html
    e=lambda v:html.escape(str(v))
    pipeline=report.get('decision_pipeline') or finalize(report)
    counts=pipeline['counts'];out='<h2>小模型初筛 → 大模型反向复核 → 买入计划</h2><section>'
    out+='<p>初筛器：透明量化规则评分器；大模型：'+e(pipeline.get('review_model') or (report.get('ai',{}).get('diagnostics',{}).get('model')) or '未记录模型名称')+'。初筛负责找线索，复核负责挑战理由、暂缓或否决。</p>'
    out+='<p><b>本次解读状态：</b>'+e(pipeline['availability_note'])+'</p>'
    out+='<p>'+e('；'.join(LABELS[k]+str(counts[k])+'只' for k in LABELS))+'。</p>'
    out+='<details><summary>这些状态是什么意思？</summary><p>有条件通过：认可研究逻辑且条件计划完整，触发前不买；暂缓：已审阅，但关键问题未解决；否决：不支持当前投资逻辑；已复核，计划待补全：有肯定意见，但操作计划缺项；已返回，需核实：意见存在引用、结构或冲突问题；待大模型复核：尚无可用于本期的意见，具体原因见逐股说明。数量为0只表示没有股票被归入该类。</p></details>'
    out+='<p><b>最终有条件通过名单：</b>'+e('、'.join(d['name']+'（'+d['symbol']+'）' for d in pipeline['decisions'] if d['verdict']=='approve_conditional') or '暂无，初筛候选尚未通过本期复核')+'</p>'
    out+='<div class="scroll"><table><tr><th>股票</th><th>所属优先行业</th><th>小模型初筛</th><th>大模型意见</th><th>最终处理</th></tr>'
    for d in pipeline['decisions']:
        out+='<tr>'+''.join('<td>'+e(v)+'</td>' for v in [d['name']+' '+d['symbol'],d.get('industry_name','未知'),f"规则分 {d['small_model_score']:.1f}",d['label'],d['final_action']])+'</tr>'
    out+='</table></div>'
    for d in pipeline['decisions']:
        out+='<h3>'+e(d['name'])+'：'+e(d['label'])+'</h3>'
        out+='<p><b>初筛依据：</b>'+e('；'.join(d['small_model_reasons']))+'</p>'
        r=d['review']
        if not r:
            out+='<p>'+e(d['pending_reason'])+'</p>'
            old=d.get('historical_review')
            if old:out+='<p><b>上次模型意见（历史参考，'+e(old['date'][:19].replace('T',' '))+'）：'+e(old['verdict'])+'</b>。'+e(old['reason'])+' 本期新资料尚未复核，此意见不构成本期买入计划。</p>'
            from .scenario_analysis import paragraphs
            for title,text in paragraphs(d['scenario_analysis']):out+='<p><b>'+e(title)+'：</b>'+e(text)+'</p>'
            continue
        out+='<p><b>模型原始意见：</b>'+e(r.get('verdict','未提供'))+'</p><p><b>独立复核理由：</b>'+e(r.get('reason','模型未提供'))+'</p><p><b>反方论证：</b>'+e(r.get('counter_case','模型未提供'))+'</p>'
        if d.get('validation_issue'):out+='<p><b>程序检查：</b>'+e(d['validation_issue'])+'。仅本股受影响，其他复核已保留。</p>'
        if isinstance(r.get('blockers'),list) and r['blockers']:out+='<p><b>阻断项：</b>'+e('；'.join(str(v) for v in r['blockers']))+'</p>'
        from .scenario_analysis import paragraphs
        for title,text in paragraphs(d['scenario_analysis']):out+='<p><b>'+e(title)+'：</b>'+e(text)+'</p>'
        if d['verdict']!='approve_conditional':continue
        plan=r['buy_plan']
        out+='<p><b>开始条件：</b>'+e(plan['entry_condition'])+'</p>'
        if d['verdict']=='approve_conditional':
            out+='<p>以下比例以你另行确定的该股计划金额为基数，不是总资产；不自动下单。</p><ol>'
            for t in plan['tranches']:out+='<li>计划金额的 '+f"{t['fraction']*100:.0f}%"+'：'+e(t['condition'])+'</li>'
            out+='</ol>'
        out+='<p><b>取消条件：</b>'+e(plan['cancel_condition'])+'</p><p><b>退出/复核条件：</b>'+e(plan['exit_review_condition'])+'</p>'
    out+='<p class="muted">'+e(pipeline['verification_boundary'])+'</p></section>'
    return out


def markdown_section(report):
    pipeline=report.get('decision_pipeline') or finalize(report)
    lines=['## 小模型初筛与大模型复核','', '初筛器为规则评分器，未训练收益预测模型。大模型可通过、暂缓或否决；未复核股票不进入买入计划。','', '|股票|所属优先行业|初筛分|大模型意见|最终处理|','|---|---|---:|---|---|']
    for d in pipeline['decisions']:lines.append(f"|{d['name']} {d['symbol']}|{d.get('industry_name','未知')}|{d['small_model_score']:.1f}|{d['label']}|{d['final_action']}|")
    lines+=['','最终有条件通过名单：'+('、'.join(d['name'] for d in pipeline['decisions'] if d['verdict']=='approve_conditional') or '暂无')+'。','']
    for d in pipeline['decisions']:
        lines+=['### '+d['name']+'：'+d['label'],'','初筛依据：'+'；'.join(d['small_model_reasons'])+'。','']
        r=d['review']
        if not r:
            lines+=[d['pending_reason'],'']
            old=d.get('historical_review')
            if old:lines+=['上次模型意见（历史参考，'+old['date'][:19].replace('T',' ')+'）：'+old['verdict']+'。'+old['reason']+' 本期新资料尚未复核，不进入本期买入计划。','']
            from .scenario_analysis import paragraphs
            for title,text in paragraphs(d['scenario_analysis']):lines += [title+'：'+text,'']
            continue
        lines+=['模型原始意见：'+str(r.get('verdict','未提供')),'','大模型复核：'+str(r.get('reason','模型未提供')),'','反方论证：'+str(r.get('counter_case','模型未提供')),'']
        if isinstance(r.get('blockers'),list) and r['blockers']:lines+=['阻断项：'+'；'.join(str(v) for v in r['blockers']),'']
        if d.get('validation_issue'):lines+=['程序检查：'+d['validation_issue']+'；仅本股受影响。','']
        from .scenario_analysis import paragraphs
        for title,text in paragraphs(d['scenario_analysis']):lines += [title+'：'+text,'']
        if d['verdict']!='approve_conditional':continue
        plan=r['buy_plan'];lines+=['开始条件：'+plan['entry_condition'],'']
        for i,t in enumerate(plan['tranches'],1):lines+=[f"{i}. 该股计划金额的{t['fraction']*100:.0f}%：{t['condition']}"]
        lines+=['','取消条件：'+plan['cancel_condition'],'','退出/复核：'+plan['exit_review_condition'],'']
    return lines


def replay_response(report,saved):
    if not isinstance(saved,dict):raise ValueError('模型正文存档结构无效')
    if saved.get('candidate_hash')!=candidate_hash(report):raise ValueError('已保存响应与当前股票或证据不一致，不能重放为本期复核')
    raw=saved.get('result')
    if not isinstance(raw,dict):raise ValueError('存档不是有效的模型JSON对象')
    ids={r['evidence_id'] for k in ('sectors','companies','fundamental_candidates') for r in report.get(k,[]) if r.get('evidence_id')}
    from .context_research import evidence_ids,validate_analysis
    ids.update(evidence_ids(report.get('research_context',{})))
    ids.update(r['evidence_id'] for r in report.get('stock_recommendations',{}).get('stocks',[]))
    result=dict(raw)
    result['summary']=raw.get('summary') if isinstance(raw.get('summary'),str) else '概要格式不完整，逐股复核见下方。'
    result['missing_evidence']=[v for v in raw.get('missing_evidence',[]) if isinstance(v,str)] if isinstance(raw.get('missing_evidence'),list) else []
    findings=raw.get('findings',[])
    result['findings']=[v for v in findings if isinstance(v,dict) and isinstance(v.get('claim'),str) and isinstance(v.get('evidence_ids'),list) and v['evidence_ids'] and all(isinstance(i,str) and i in ids for i in v['evidence_ids'])] if isinstance(findings,list) else []
    return dict(model=saved.get('model'),candidate_hash=saved['candidate_hash'],allocation_input_hash=saved.get('allocation_input_hash'),result=result,usage=saved.get('usage'),context_analysis=validate_analysis(result,report.get('research_context',{})),status='已从模型正文存档重新处理，未调用API',replayed=True)

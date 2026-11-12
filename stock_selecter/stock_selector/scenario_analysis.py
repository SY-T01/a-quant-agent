"""Counterfactual research is independent of the current buy decision."""
import math

FIELDS=('entry_condition','cancel_condition','exit_review_condition','upside_case','base_case','downside_case')
TITLES=('重新考虑的开始条件','取消投资假设的条件','假设已买入：退出或复核条件','持有3—6个月：向好情景','持有3—6个月：中性情景','持有3—6个月：不利情景')


def build(candidate,review,context=None,allowed_ids=(),sectors=(),horizon=None):
    """Missing model fields fall back to a clearly attributed local research framework."""
    review=review or {};raw=review.get('scenario_analysis')
    ids=raw.get('evidence_ids') if isinstance(raw,dict) else None
    cited=(isinstance(ids,list) and candidate['evidence_id'] in ids and all(isinstance(i,str) and i in allowed_ids for i in ids))
    complete=cited and all(isinstance(raw.get(k),str) and raw[k].strip() for k in FIELDS)
    blockers=review.get('blockers') if isinstance(review.get('blockers'),list) else []
    blockers=[x for x in blockers if isinstance(x,str) and x.strip()]
    outstanding='；'.join(blockers) or '增长可持续性、估值口径、交易状态及最新公告'
    company=next((c for c in (context or {}).get('companies',[]) if c.get('symbol')==candidate['symbol']),{})
    cases={v.get('label'):v.get('condition') for v in company.get('scenarios',[]) if isinstance(v,dict)}
    fallback={
        'entry_condition':'先逐项取得可核对的资料，解决“'+outstanding+'”；核实结果支持原投资逻辑后，更新估值与行业趋势并重新复核。满足这些研究条件不自动变为买入通过。',
        'cancel_condition':'若新增财报或公告否定收入、利润与现金流的可持续性，或上述关键问题证实不利，放弃本轮投资假设；资料仍缺失则继续观察，不能视为已满足条件。',
        'exit_review_condition':'假设已经买入，每周检查行业资金及重大公告，财报披露后检查盈利与现金流。若原投资逻辑被否定或出现重大不利变化，立即重新评估是否退出；不把等待资料当作继续持有的理由。'+str(candidate.get('invalidation') or ''),
        'upside_case':cases.get('乐观情景') or '若后续订单、利润和经营现金流共同验证增长，投资逻辑得到支持；股价仍取决于买入估值和市场变化。',
        'base_case':cases.get('基准情景') or '若业务维持但增长未超预期，收益更依赖买入估值；可能横盘，也可能因估值变化而亏损。',
        'downside_case':cases.get('悲观情景') or '若后续经营或行业景气转弱，利润下降和估值下调可能同时发生，带来亏损。',
    }
    if isinstance(review.get('counter_case'),str) and review['counter_case'].strip():
        fallback['downside_case']='需验证的本股不利路径：'+review['counter_case']+' 若该风险兑现，可能同时影响利润与估值；此处为条件推演，不确认风险已经发生。'
    sector=next((s for s in sectors if s.get('code')==candidate.get('industry_code')),None)
    input_note=None
    if sector and any('行业' in b or '板块' in b for b in blockers) and any('缺失' in b or '缺少' in b for b in blockers):
        input_note='输入核对：本报告实际包含'+str(candidate.get('industry_name','本行业'))+'板块资料；旧模型所称行业数据缺失可能来自送入资料遗漏，应重审该阻断项。现有5/10日资金不替代中期趋势验证。'
    if any('利润' in b or '低基数' in b for b in blockers):
        fallback['entry_condition']+=' 对利润构成，逐项比较本期和上年同期的扣非利润、投资收益及经营现金流，确认增长并非仅由基数或一次性项目解释。'
    # Existing model conditions remain useful even when its verdict is defer/reject.
    plan=review.get('buy_plan') or {}
    if isinstance(plan,dict):
        for k in FIELDS[:3]:
            if isinstance(plan.get(k),str) and plan[k].strip() and not plan[k].startswith('模型未提供'):fallback[k]=plan[k]
    result={k:raw[k].strip() for k in FIELDS} if complete else fallback
    result.update(source='大模型条件情景分析（不是买入批准）' if complete else '程序条件研究框架（结合本股资料与暂缓理由，不是新增的大模型意见）',
                  evidence_ids=ids if complete else [candidate['evidence_id']],model_analysis_status='完整' if complete else '未返回完整且引用有效的情景分析，采用程序框架',
                  research_only=True)
    result['input_note']=input_note
    result['horizon_label']=(horizon or {}).get('label','3—6个月')
    if horizon:
        result['entry_condition']+=' 本次预计持有'+horizon['label']+'：'+horizon['focus']
    price=(candidate.get('quote') or {}).get('price')
    result['sensitivity']=[]
    if isinstance(price,(int,float)) and not isinstance(price,bool) and math.isfinite(price) and price>0:
        for change in (-.2,-.1,.1,.2):
            result['sensitivity'].append(dict(change_pct=round(change*100),reference_price=round(price*(1+change),2),pnl_per_10000_yuan=round(10000*change)))
    result['sensitivity_note']='仅作价格敏感性算术：假设按报告参考价买入并投入1万元，忽略费用、分红及整手约束；涨跌幅为人为设定，不是'+result['horizon_label']+'预测、目标价、概率或止损线。'
    return result


def paragraphs(analysis):
    rows=[('条件情景分析',analysis['source'])]
    if analysis.get('input_note'):rows.append(('原复核意见的输入核对',analysis['input_note']))
    rows.extend((title.replace('3—6个月',analysis.get('horizon_label','3—6个月')),analysis[field]) for field,title in zip(FIELDS,TITLES))
    if analysis['sensitivity']:
        rows.append(('假设买入后的盈亏测算','；'.join(f"股价{x['change_pct']:+.0f}%：参考价{x['reference_price']:.2f}元，1万元账面盈亏{x['pnl_per_10000_yuan']:+.0f}元" for x in analysis['sensitivity'])))
        rows.append(('测算口径',analysis['sensitivity_note']))
    return rows

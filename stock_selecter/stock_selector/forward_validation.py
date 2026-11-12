"""Prospective, dated research observations and conservative forward execution checks."""
import argparse
import hashlib
import json
import math
import statistics
from datetime import datetime,timedelta
from pathlib import Path
from .storage import PROJECT,atomic_json,now
from .quant_factors import instant,finite,bars_at,trading_day

DEFAULT_COSTS=dict(commission=.0003,min_commission=5.,sell_tax=.0005,transfer_fee=.00001,slippage=.0005)


def record(report,root=None):
    from .model_review import candidate_hash
    root=Path(root or PROJECT/'data/research/forward_tracking')
    facts={'candidate_hash':candidate_hash(report),'model_version':report.get('model_version'),'holding_months':report.get('investment_horizon',{}).get('months')}
    key=hashlib.sha256(json.dumps(facts,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    path=root/'signals'/(key+'.json')
    if not path.exists():
        signal=dict(id=key,decision_at=report['asof'],source_day=(report.get('data_times',{}).get('last') or '')[:10],
            model='production_rule_baseline',facts=facts,budget_yuan=(report.get('capital_settings') or {}).get('budget_yuan','100000.00'),
            candidates=[dict(symbol=c['symbol'],industry=c['industry_code'],name=c['name'],score=c['recommendation_score']) for c in report.get('stock_recommendations',{}).get('stocks',[])],
            research_only=True,note='量化候选的前瞻观察，不是AI通过或买入委托。相同证据版本不重复记入，重试API不产生新收益样本。')
        atomic_json(path,signal)
    return dict(id=key,path=str(path),status='已记录前瞻观察；等待未来交易日与可核验交易数据',decision_at=json.loads(path.read_text())['decision_at'])


def load_evidence():
    path=PROJECT/'data/research/market_evidence.json'
    evidence=json.loads(path.read_text()) if path.exists() else {}
    if not evidence.get('benchmark'):
        cache=PROJECT/'data/normalized/sector_research/enrichment.json'
        stored=json.loads(cache.read_text()) if cache.exists() else {}
        observed=stored.get('generated_at')
        evidence['benchmark']=[dict(row,available_at=row.get('available_at') or observed,availability_kind='实际观察时间，不代表历史首次可知') for row in stored.get('benchmark',[]) if row.get('available_at') or observed]
    return evidence


def walk_forward_windows(calendar,train=126,validation=42,test=21,purge=20):
    """Fixed chronological windows; no random splits or test-based model selection."""
    if min(train,validation,test)<1 or purge<0:raise ValueError('验证窗口参数无效')
    if len(calendar)!=len(set(calendar)) or calendar!=sorted(calendar):raise ValueError('交易日历须唯一且递增')
    folds=[];test_start=train+validation+purge*2
    while test_start+test<=len(calendar):
        val_end=test_start-purge;val_start=val_end-validation;train_end=val_start-purge;train_start=train_end-train
        folds.append(dict(train=calendar[train_start:train_end],validation=calendar[val_start:val_end],test=calendar[test_start:test_start+test],purge_sessions=purge))
        test_start+=test
    return folds


def trade_trial(signal,candidate,history,benchmark,asof,horizon=5,costs=None):
    if horizon not in (5,10,20):raise ValueError('前瞻验证仅支持5/10/20个交易日')
    fees=dict(DEFAULT_COSTS,**(costs or {}))
    if any(isinstance(v,bool) or not math.isfinite(v) or v<0 for v in fees.values()) or fees['slippage']>=1:raise ValueError('研究费用与滑点必须非负且有限，滑点须小于1')
    result=dict(symbol=candidate['symbol'],horizon=horizon,status='pending',net_return=None,excess_return=None,fees_yuan=None)
    if instant(signal['decision_at'])>instant(asof):raise ValueError('观察记录晚于评估时点')
    rows=bars_at(history,asof,True);benchmark=bars_at(benchmark,asof,True)
    future=[b for b in benchmark if b['date']>trading_day(signal['decision_at'])]
    if not future:return result
    by_date={b['date']:b for b in rows};entry_day=future[0]['date']
    result['entry_day']=entry_day
    entry=by_date.get(entry_day)
    if not entry:result.update(status='blocked',reason='缺少下一交易日行情，不跳过缺失日挑选有利买点');return result
    fields=('open','amount_yuan','limit_up','limit_down','adj_factor','suspended','is_st','buy_min_shares','buy_step_shares','trade_rules_source')
    if any(entry.get(k) is None for k in fields):result.update(status='blocked',reason='缺少时点交易状态、费用容量或板块申报规则');return result
    if any(entry[k] not in (True,False,0,1) for k in ('suspended','is_st')):raise ValueError('交易状态必须是明确布尔值')
    if entry['suspended'] or entry['is_st'] or entry['amount_yuan']<=0:result.update(status='blocked',reason='停牌、风险警示或无成交');return result
    op=finite(entry['open']);limit=finite(entry['limit_up']);minimum=finite(entry['buy_min_shares']);step=finite(entry['buy_step_shares'])
    if op is None or op<=0 or limit is None or limit<=0 or minimum is None or step is None or minimum<=0 or step<=0 or minimum!=int(minimum) or step!=int(step) or not entry['trade_rules_source']:raise ValueError('交易价格或申报单位非法')
    if op>=limit or op*(1+fees['slippage'])>limit:result.update(status='blocked',reason='开盘涨停或含滑点价格超限');return result
    # Independent counterfactual allocation: 10% per stock, at most five stocks.
    budget=float(signal['budget_yuan'])*.1
    if not math.isfinite(budget) or budget<=0:raise ValueError('观察资金无效')
    buy_price=op*(1+fees['slippage']);qty=math.floor(budget/buy_price/step)*step
    # Only a small daily-capacity approximation; not a guarantee of opening execution.
    qty=min(qty,math.floor(entry['amount_yuan']*.01/buy_price/step)*step)
    charge=lambda value,sell=False:max(fees['min_commission'],value*fees['commission'])+value*(fees['transfer_fee']+(fees['sell_tax'] if sell else 0))
    while qty>=minimum and qty*buy_price+charge(qty*buy_price)>budget:qty-=step
    if qty<minimum:result.update(status='blocked',reason='预算或容量不足最低买入数量');return result
    if len(future)<horizon:return result
    exit_day=future[horizon-1]['date'];result['exit_day']=exit_day
    exit_bar=by_date.get(exit_day)
    if not exit_bar or any(exit_bar.get(k) is None for k in ('limit_down','suspended','adj_factor','sell_min_shares','sell_step_shares','trade_rules_source')):
        result.update(status='blocked',reason='退出日行情或卖出规则缺失');return result
    if not finite(exit_bar['adj_factor']) or finite(exit_bar['adj_factor'])<=0 or not finite(entry['adj_factor']) or finite(entry['adj_factor'])<=0:raise ValueError('复权因子必须为正')
    if finite(exit_bar['adj_factor'])!=finite(entry['adj_factor']):result.update(status='blocked',reason='跨除权分红事件，需要独立公司行动现金台账');return result
    sell_price=exit_bar['close']*(1-fees['slippage'])
    if exit_bar['suspended'] or sell_price<=exit_bar['limit_down']:result.update(status='blocked',reason='退出日停牌、跌停或滑点价超限，不假设成功退出');return result
    sell_step=finite(exit_bar['sell_step_shares']);sell_min=finite(exit_bar['sell_min_shares'])
    if not sell_step or not sell_min or sell_step<=0 or sell_min<=0 or sell_step!=int(sell_step) or sell_min!=int(sell_min) or not exit_bar['trade_rules_source']:raise ValueError('卖出申报规则无效')
    if qty>=sell_min and qty%sell_step:result.update(status='blocked',reason='卖出数量不符合时点申报单位');return result
    buy_value=qty*buy_price;sell_value=qty*sell_price;fee=charge(buy_value)+charge(sell_value,True);cost=buy_value+charge(buy_value)
    bm={b['date']:b for b in benchmark};benchmark_open=finite(bm[entry_day].get('open'))
    excess=None if not benchmark_open else (sell_value-buy_value-fee)/cost-(bm[exit_day]['close']/benchmark_open-1)
    result.update(status='measured',quantity=qty,net_return=(sell_value-buy_value-fee)/cost,excess_return=excess,fees_yuan=fee,
        note='独立观察单笔的日线近似，扣研究费用；不是组合年化表现，不能将重叠样本收益相加。')
    return result


def evaluate(root,evidence,asof):
    root=Path(root);trials=[];signals=[];errors=[]
    for path in sorted((root/'signals').glob('*.json')):
        signal=json.loads(path.read_text());signals.append(signal)
        for candidate in signal['candidates']:
            item=evidence.get('stock_history',{}).get(candidate['symbol']) or {}
            from .research_lab import verified_item
            history=item.get('series',[]) if verified_item(item,asof) else []
            for horizon in (5,10,20):
                try:row=trade_trial(signal,candidate,history,evidence.get('benchmark',[]),asof,horizon)
                except (ValueError,KeyError,TypeError) as e:
                    row=dict(symbol=candidate['symbol'],horizon=horizon,status='blocked',reason='输入数据无效：'+str(e),net_return=None,excess_return=None,fees_yuan=None)
                    errors.append(dict(signal=signal['id'],symbol=candidate['symbol'],error=str(e)))
                row['signal_id']=signal['id'];trials.append(row)
    summary=[]
    for horizon in (5,10,20):
        rows=[r for r in trials if r['horizon']==horizon];done=[r for r in rows if r['status']=='measured'];excess=[r['excess_return'] for r in done if r['excess_return'] is not None]
        summary.append(dict(horizon=horizon,total=len(rows),measured=len(done),pending=sum(r['status']=='pending' for r in rows),blocked=sum(r['status']=='blocked' for r in rows),
            mean_position_net_return=statistics.mean(r['net_return'] for r in done) if done else None,mean_excess_return=statistics.mean(excess) if excess else None))
    calendar=sorted({b['date'] for b in evidence.get('benchmark',[]) if b['date']<=asof[:10] and b.get('available_at') and instant(b['available_at'])<=instant(asof)})
    folds=walk_forward_windows(calendar)
    return dict(asof=asof,signal_count=len(signals),signals=[dict(id=s['id'],decision_at=s['decision_at'],source_day=s['source_day'],candidates=len(s['candidates'])) for s in signals],
        cost_assumptions=DEFAULT_COSTS,
        horizons=summary,trials=trials,errors=errors,walk_forward=dict(folds=len(folds),train_sessions=126,validation_sessions=42,test_sessions=21,purge_sessions=20,
            status='仅生成时序分窗，尚未完成旧版/RS/资金RS/增强模型的多模型样本外对照',windows=folds),
        status='前瞻跟踪中，尚无足够成熟验证样本' if not any(r['measured'] for r in summary) else '部分观察已到期；仍需完整样本与对照',
        real_strategy_validated=False,note='只记录实际生成后的未来表现；不能回填成历史预测。缺失/不可成交样本单独列出，不当作0收益或从失败率中删除。费用是研究假设；日成交额容量是事后近似，不证明开盘可成交。')


def main():
    p=argparse.ArgumentParser(description='前瞻跟踪与5/10/20交易日验证状态，不调用API')
    p.add_argument('--root',default=str(PROJECT/'data/research/forward_tracking'));p.add_argument('--out',default=str(PROJECT/'reports/research_validation'))
    a=p.parse_args();evidence=load_evidence()
    result=evaluate(a.root,evidence,now());out=Path(a.out);atomic_json(out/'validation.json',result)
    lines=['# 前瞻验证状态','',result['status'],'',result['note'],'']
    for row in result['horizons']:lines.append(f"{row['horizon']}交易日：到期可测 {row['measured']}，等待 {row['pending']}，受阻 {row['blocked']}；总样本 {row['total']}。")
    (out/'验证状态.md').write_text('\n'.join(lines),encoding='utf-8')
    import html
    (out/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>前瞻验证状态</title><style>body{font:16px/1.8 system-ui;max-width:1050px;margin:35px auto;padding:20px;color:#18344a}</style><h1>前瞻验证状态</h1>'+''.join('<p>'+html.escape(line)+'</p>' for line in lines[1:] if line),encoding='utf-8')
    print(result['status'])
    for line in lines[6:]:print(line)
    print('未联网、未调用大模型、未生成实盘委托。')


if __name__=='__main__':main()

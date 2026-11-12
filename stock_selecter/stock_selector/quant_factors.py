"""Auditable research factors. Missing evidence never becomes a neutral/zero score."""
from datetime import date,datetime,timezone
import math
import statistics
from zoneinfo import ZoneInfo

WINDOWS=(5,10,20)


def finite(value):
    if value is None:return None
    if isinstance(value,bool):raise ValueError('布尔值不是数值因子')
    try:value=float(value)
    except (ValueError,TypeError):raise ValueError('因子不是有效数字') from None
    if not math.isfinite(value):raise ValueError('因子包含非有限值')
    return value


def instant(value):
    stamp=datetime.fromisoformat(value)
    if stamp.tzinfo is None:raise ValueError('证据时间必须包含时区')
    return stamp.astimezone(timezone.utc)


def trading_day(value):
    return instant(value).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()


def bars_at(rows,asof,require_availability=False,adjusted=False):
    cutoff=instant(asof);cutoff_day=trading_day(asof);output=[];seen=set()
    for original in rows:
        day=date.fromisoformat(original['date']).isoformat()
        if day in seen:raise ValueError('历史序列出现重复日期')
        seen.add(day)
        if day>cutoff_day:continue
        if require_availability and not original.get('available_at'):raise ValueError('缺少历史可获知时间')
        if original.get('available_at'):
            available=instant(original['available_at'])
            if trading_day(original['available_at'])<day:raise ValueError('日线可获知时间早于行情日期')
            if available>cutoff:continue
        row=dict(original,date=day)
        close=finite(row.get('close'))
        if close is None or close<=0:raise ValueError('历史价格缺失或非正')
        if adjusted:
            adjustment=finite(row.get('adj_factor'))
            if adjustment is None or adjustment<=0:raise ValueError('个股价格缺少可核对复权因子')
            close*=adjustment
        row['close']=close
        for key in ('net_yuan','amount_yuan','volume_shares'):
            row[key]=finite(row.get(key))
        if row['amount_yuan'] is not None and row['amount_yuan']<0:raise ValueError('历史成交额不能为负')
        output.append(row)
    return sorted(output,key=lambda row:row['date'])


def time_factors(rows,benchmark,asof,require_availability=False,adjusted=False):
    history=bars_at(rows,asof,require_availability,adjusted)
    bm=bars_at(benchmark,asof,require_availability)
    factors={key:None for key in ('acceleration','positive_10d','relative_volume','amount_20d_mean','volatility_20d','drawdown_20d')}
    factors.update({f'{prefix}_{n}d':None for n in WINDOWS for prefix in ('flow','return','excess')})
    factors['observations']=len(history)
    if not history:return factors
    calendar=[b['date'] for b in bm];by_bm={b['date']:b['close'] for b in bm}
    end=history[-1]['date']
    def complete(n):
        # The benchmark calendar detects missing sessions and stale endpoints.
        dates=[b['date'] for b in history[-n:]]
        return len(dates)==n and len(calendar)>=n and dates==calendar[-n:] and end==calendar[-1]
    for n in WINDOWS:
        if complete(n):
            window=history[-n:]
            if all(b['amount_yuan'] is not None and b['amount_yuan']>0 and b['net_yuan'] is not None for b in window):
                factors[f'flow_{n}d']=sum(b['net_yuan'] for b in window)/sum(b['amount_yuan'] for b in window)
        if complete(n+1):
            window=history[-n-1:];start=window[0]['date']
            factors[f'return_{n}d']=window[-1]['close']/window[0]['close']-1
            factors[f'excess_{n}d']=factors[f'return_{n}d']-(by_bm[end]/by_bm[start]-1)
    if complete(10):
        window=history[-10:]
        if all(b['net_yuan'] is not None for b in window):factors['positive_10d']=sum(b['net_yuan']>0 for b in window)/10
        if all(b['amount_yuan'] is not None and b['amount_yuan']>0 and b['net_yuan'] is not None for b in window):
            recent=sum(b['net_yuan'] for b in window[-5:])/sum(b['amount_yuan'] for b in window[-5:])
            prior=sum(b['net_yuan'] for b in window[:5])/sum(b['amount_yuan'] for b in window[:5])
            factors['acceleration']=recent-prior
    if complete(21):
        window=history[-21:];prices=[b['close'] for b in window]
        factors['volatility_20d']=statistics.pstdev(b/a-1 for a,b in zip(prices,prices[1:]))
        peak=prices[0];dd=0
        for price in prices:peak=max(peak,price);dd=min(dd,price/peak-1)
        factors['drawdown_20d']=dd
        if all(b['amount_yuan'] is not None and b['amount_yuan']>0 for b in window[-20:]):
            factors['amount_20d_mean']=statistics.mean(b['amount_yuan'] for b in window[-20:])
        volumes=[b['volume_shares'] for b in window]
        if all(v is not None and v>0 for v in volumes):factors['relative_volume']=volumes[-1]/statistics.mean(volumes[:-1])
    return factors


def breadth(membership,stock_histories,benchmark,asof):
    result={'advancers_5d':None,'above_ma20':None,'members':0,'covered':0,'coverage':0.,'reason':None}
    if not membership or membership.get('complete') is not True or not membership.get('source'):
        result['reason']='需要完整且有来源的时点成分，资金榜前20只样本不可代替行业宽度';return result
    if instant(membership['available_at'])>instant(asof):result['reason']='成分资料在决策时点尚不可获知';return result
    day=trading_day(asof)
    members=[m for m in membership.get('members',[]) if m['valid_from']<=day and (not m.get('valid_to') or day<m['valid_to'])]
    symbols=[m['symbol'] for m in members]
    if len(set(symbols))!=len(symbols):raise ValueError('行业成分重复')
    result['members']=len(symbols);up=above=0
    if not symbols:result['reason']='研究时点无有效成分';return result
    calendar=[b['date'] for b in bars_at(benchmark,asof,True)]
    for symbol in symbols:
        rows=bars_at(stock_histories.get(symbol,[]),asof,True,True)
        if len(rows)<21 or [b['date'] for b in rows[-21:]]!=calendar[-21:]:continue
        closes=[b['close'] for b in rows[-21:]]
        result['covered']+=1;up+=closes[-1]>closes[-6];above+=closes[-1]>statistics.mean(closes[-20:])
    result['coverage']=result['covered']/result['members']
    if result['covered']!=result['members']:
        result['reason']='成分行情或复权覆盖不完整；不把缺失成分当作下跌或均线下方';return result
    result['advancers_5d']=up/result['covered'];result['above_ma20']=above/result['covered']
    return result


def quantile(values,fraction):
    ordered=sorted(values);index=(len(ordered)-1)*fraction;left=int(index);right=min(left+1,len(ordered)-1)
    return ordered[left]+(ordered[right]-ordered[left])*(index-left)


def grouped_ranks(rows,field,group_key,reverse=False):
    groups={};result={}
    for row in rows:
        value=finite(row.get(field));group=row.get(group_key)
        if value is not None and group:groups.setdefault(group,[]).append((row['key'],value))
    for group,items in groups.items():
        if len(items)<3:continue
        lower=quantile([v for _,v in items],.05);upper=quantile([v for _,v in items],.95)
        clipped={key:min(upper,max(lower,value)) for key,value in items};values=list(clipped.values());n=len(values)
        for key,value in clipped.items():
            rank=(sum(v<value for v in values)+(sum(v==value for v in values)-1)/2)/(n-1)*100
            result[key]=100-rank if reverse else rank
    return result


def full_mean(values):
    return statistics.mean(values) if values and all(v is not None for v in values) else None


def weighted_score(components,weights):
    if sum(weights.values())!=100 or any(v<0 for v in weights.values()):raise ValueError('研究权重必须非负且合计100')
    available=sum(weights[key] for key,value in components.items() if value is not None)
    if any(components.get(key) is None for key in weights):return {'score':None,'coverage_weight_pct':available}
    return {'score':round(sum(components[key]*weight for key,weight in weights.items())/100,2),'coverage_weight_pct':100}

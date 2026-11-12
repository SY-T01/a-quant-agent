from __future__ import annotations
import csv
import math
import statistics as st
from dataclasses import dataclass, asdict
from datetime import date, datetime
from collections import defaultdict

VERSION = 'm0-0.1'
REQUIRED = set('date symbol industry open close adj_factor amount moneyflow available_at provider method_version listed_on is_st suspended limit_up limit_down'.split())

@dataclass(frozen=True)
class Config:
    top_n: int = 10
    industry_cap: float = .30
    flow_weight: float = .5
    top_industries: int = 0
    rebalance_days: int = 5
    min_listed_days: int = 120
    min_amount: float = 20_000_000
    participation: float = .01
    lot: int = 100
    initial_cash: float = 1_000_000
    commission: float = .0003
    min_commission: float = 5
    sell_tax: float = .0005
    transfer_fee: float = .00001
    slippage: float = .0005

    def __post_init__(self):
        if self.top_n < 1 or self.rebalance_days < 1 or self.lot < 1:
            raise ValueError('持仓数、调仓间隔、交易单位必须为正')
        if not 0 < self.industry_cap <= 1 or not 0 <= self.flow_weight <= 1:
            raise ValueError('权重或行业上限无效')
        if not 0 < self.participation <= 1 or self.initial_cash <= 0:
            raise ValueError('参与率或初始资金无效')
        if min(self.commission, self.min_commission, self.sell_tax, self.transfer_fee, self.slippage) < 0:
            raise ValueError('成本不能为负')

def load(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if not REQUIRED <= set(reader.fieldnames or []):
            raise ValueError(f'缺少字段: {sorted(REQUIRED-set(reader.fieldnames or []))}')
        rows = list(reader)
    seen, sources = set(), set()
    for r in rows:
        key = (r['date'], r['symbol'])
        if key in seen:
            raise ValueError(f'重复主键 {key}')
        seen.add(key)
        date.fromisoformat(r['date']); date.fromisoformat(r['listed_on'])
        if r['listed_on'] > r['date'] or not r['industry'] or not r['symbol']:
            raise ValueError(f'证券身份无效 {key}')
        # Naive timestamps have a single explicit convention: Asia/Shanghai.
        ts = datetime.fromisoformat(r['available_at'])
        r['available_at'] = ts.isoformat()
        if ts.tzinfo is not None or ts.date().isoformat() < r['date']:
            raise ValueError('available_at 必须为上海本地无时区时间，且不得早于行情日期')
        for k in ['open','close','adj_factor','amount','moneyflow','limit_up','limit_down']:
            r[k] = float(r[k])
            if not math.isfinite(r[k]):
                raise ValueError(f'非有限数值 {key} {k}')
        for k in ['is_st','suspended']:
            if r[k] not in ('0','1'): raise ValueError(f'{k} 必须为0或1')
            r[k] = bool(int(r[k]))
        if min(r['open'], r['close'], r['adj_factor'], r['limit_down']) <= 0 or r['amount'] < 0 or r['limit_up'] < r['limit_down']:
            raise ValueError(f'价格或成交额无效 {key}')
        sources.add((r['provider'],r['method_version']))
    if len(sources) != 1: raise ValueError('一次研究仅允许同一供应商与资金流口径版本')
    if not rows: raise ValueError('空数据')
    return sorted(rows, key=lambda r:(r['date'],r['symbol']))

def ranks(values):
    """Average rank for ties; constant factors contribute a neutral 0.5."""
    groups = defaultdict(list)
    for k,v in values.items(): groups[v].append(k)
    result, offset, n = {}, 0, len(values)
    for value in sorted(groups):
        keys = groups[value]
        p = (offset + (len(keys)-1)/2)/(n-1) if n > 1 else .5
        for k in keys: result[k] = p
        offset += len(keys)
    return result

def features(rows, cfg):
    by_day, history, result = defaultdict(list), defaultdict(list), {}
    for r in rows: by_day[r['date']].append(r)
    dates = sorted(by_day)
    for idx,d in enumerate(dates):
        current = []
        for r in by_day[d]:
            history[r['symbol']].append(r)
            h = history[r['symbol']][-21:]
            # Missing, late, or suspended observations invalidate the complete window.
            cutoff = d+'T23:00:00'
            if len(h) < 21 or [v['date'] for v in h] != dates[idx-20:idx+1]: continue
            if any(v['available_at'].replace(' ','T') > cutoff or v['suspended'] or v['amount'] <= 0 for v in h): continue
            if r['is_st'] or (date.fromisoformat(d)-date.fromisoformat(r['listed_on'])).days < cfg.min_listed_days: continue
            if st.mean(v['amount'] for v in h[-20:]) < cfg.min_amount: continue
            f = dict(r)
            for n in (1,5,10,20):
                f[f'flow{n}'] = sum(v['moneyflow'] for v in h[-n:])/sum(v['amount'] for v in h[-n:])
                f[f'p{n}'] = sum(v['moneyflow'] > 0 for v in h[-n:])/n
            f['acceleration'] = st.mean(v['moneyflow']/v['amount'] for v in h[-5:])-st.mean(v['moneyflow']/v['amount'] for v in h[-10:-5])
            prices = [v['close']*v['adj_factor'] for v in h]
            f['momentum'] = prices[-1]/prices[0]-1
            f['return5'] = prices[-1]/prices[-6]-1
            f['volatility'] = st.pstdev(prices[j]/prices[j-1]-1 for j in range(1,21))
            current.append(f)
        industries = defaultdict(list)
        for f in current: industries[f['industry']].append(f)
        # Industry signals use only eligible PIT constituents; proxy, not official index RS.
        sector_raw = {k:sum(v['moneyflow'] for v in vs)/sum(v['amount'] for v in vs) for k,vs in industries.items()}
        sector_rank = ranks(sector_raw)
        sector_order = sorted(sector_rank,key=lambda k:(-sector_rank[k],k))
        fields = ['flow5','p10','acceleration','momentum','volatility']
        ranked = {k:ranks({f['symbol']:f[k] for f in current}) for k in fields}
        for f in current:
            s = f['symbol']
            f['industry_breadth'] = sum(v['moneyflow'] > 0 for v in industries[f['industry']])/len(industries[f['industry']])
            f['flow_score'] = (ranked['flow5'][s]+ranked['p10'][s]+ranked['acceleration'][s]+sector_rank[f['industry']])/4
            f['price_score'] = .8*ranked['momentum'][s]+.2*(1-ranked['volatility'][s])
            f['score'] = cfg.flow_weight*f['flow_score']+(1-cfg.flow_weight)*f['price_score']
            f['model_version'] = VERSION
        result[d] = sorted([f for f in current if not cfg.top_industries or f['industry'] in sector_order[:cfg.top_industries]],key=lambda f:(-f['score'],f['symbol']))
    return result

def select(candidates, cfg):
    chosen, counts = [], defaultdict(int)
    # Fixed 1/N target weight: unused allocation remains cash.
    for r in candidates:
        if (counts[r['industry']]+1)/cfg.top_n > cfg.industry_cap+1e-12: continue
        chosen.append(r); counts[r['industry']] += 1
        if len(chosen) == cfg.top_n: break
    return chosen

def backtest(rows, signals, cfg, start=None, end=None):
    days = defaultdict(dict)
    for r in rows: days[r['date']][r['symbol']] = r
    dates = [d for d in sorted(days) if (not start or d >= start) and (not end or d <= end)]
    cash, positions, marks, factors = cfg.initial_cash, {}, {}, {}
    pending, ledger, navs = None, [], []
    for i,d in enumerate(dates):
        today = days[d]
        for s in positions:
            if s not in today: raise ValueError(f'持仓行情缺失: {d} {s}；请提供停牌估值及退市结算记录')
        for s,r in today.items():
            if s in positions and s in factors:
                positions[s] *= r['adj_factor']/factors[s]
            marks[s] = r['open']; factors[s] = r['adj_factor']
        equity = cash+sum(q*marks[s] for s,q in positions.items())
        if pending is not None:
            targets = {r['symbol']:math.floor(equity/cfg.top_n/today[r['symbol']]['open']/cfg.lot)*cfg.lot for r in pending if r['symbol'] in today}
            universe = set(positions)|set(targets)
            # Sell first. Positions were purchased no later than the previous day (T+1).
            orders = sorted(((s, targets.get(s,0)-positions.get(s,0)) for s in universe),key=lambda v:(v[1]>0,v[0]))
            for s,delta in orders:
                if abs(delta) < 1e-8: continue
                r = today.get(s); buy = delta > 0
                reason = None
                if r is None: reason = 'missing_execution_bar'
                elif r['suspended']: reason = 'suspended'
                elif buy and r['is_st']: reason = 'st_buy_blocked'
                elif buy and r['open'] >= r['limit_up']-1e-8: reason = 'limit_up'
                elif not buy and r['open'] <= r['limit_down']+1e-8: reason = 'limit_down'
                qty, fee, value = 0, 0., 0.
                if not reason:
                    price = r['open']*(1+cfg.slippage if buy else 1-cfg.slippage)
                    price = min(price,r['limit_up']) if buy else max(price,r['limit_down'])
                    # Daily turnover is an ex-post capacity approximation, documented in report.
                    cap = math.floor(r['amount']*cfg.participation/price/cfg.lot)*cfg.lot
                    qty = min(abs(delta),cap)
                    if buy:
                        industry_value = sum(q*today[k]['open'] for k,q in positions.items() if today[k]['industry'] == r['industry'])
                        room = max(0, equity*cfg.industry_cap-industry_value)
                        qty = math.floor(min(qty,room/price)/cfg.lot)*cfg.lot
                    def cost(q):
                        v=q*price
                        return max(cfg.min_commission,v*cfg.commission)+v*(cfg.transfer_fee+(0 if buy else cfg.sell_tax)) if q else 0
                    if buy:
                        while qty > 0 and qty*price+cost(qty) > cash: qty -= cfg.lot
                    if qty <= 0: reason = 'cash_or_capacity'
                    else:
                        value=qty*price; fee=cost(qty)
                        cash += -value-fee if buy else value-fee
                        positions[s] = positions.get(s,0)+(qty if buy else -qty)
                        if positions[s] < 1e-8: positions.pop(s,None)
                        reason = 'filled' if abs(qty-abs(delta))<1e-8 else 'partial'
                ledger.append(dict(date=d,symbol=s,side='buy' if buy else 'sell',requested=abs(delta),quantity=qty,notional=value,fee=fee,status=reason))
        for s,r in today.items(): marks[s]=r['close']
        nav = cash+sum(q*marks[s] for s,q in positions.items())
        exposure = defaultdict(float)
        for s,q in positions.items(): exposure[today[s]['industry']]+=q*marks[s]/nav
        navs.append(dict(date=d,nav=nav,cash=cash,max_industry_weight=max(exposure.values(),default=0)))
        pending = select(signals.get(d,[]),cfg) if i % cfg.rebalance_days == 0 else None
    return {'metrics': metrics(navs,ledger,cfg.initial_cash), 'nav':navs,'orders':ledger,'config':asdict(cfg)}

def metrics(navs, orders, initial):
    vals=[initial]+[x['nav'] for x in navs]
    returns=[b/a-1 for a,b in zip(vals,vals[1:])]
    peak,dd=initial,0.
    for v in vals: peak=max(peak,v); dd=min(dd,v/peak-1)
    sigma=st.pstdev(returns) if returns else 0
    return dict(total_return=vals[-1]/initial-1, annualized_return=(vals[-1]/initial)**(252/max(len(returns),1))-1,max_drawdown=dd,sharpe=st.mean(returns)/sigma*math.sqrt(252) if sigma else 0,fees=sum(o['fee'] for o in orders), traded_notional=sum(o['notional'] for o in orders),rejection_rate=sum(o['quantity']==0 for o in orders)/len(orders) if orders else 0)

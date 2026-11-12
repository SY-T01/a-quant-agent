"""Small snapshot event replay, not a production historical market simulator.

Orders use the prior event's decision. No same-bar executions or imagined
liquidity. Corporate actions must already be reconciled by the data provider.
"""
from __future__ import annotations

import copy

from .domain import ValidationError, buy_quantity, cents, fee, sell_quantity, timestamp
from .llm import validate_synthesis
from .strategy import allocate, baseline, check_context, evidence


def replay_events(account, events, cfg, responses=None):
    if len(events) < 2:
        raise ValidationError("回放至少需要两个严格有序的行情事件")
    account = copy.deepcopy(account)
    previous_time = None
    previous_day = None
    pending = []
    trades, rejected, curve = [], [], []
    peak = 0
    max_dd = 0
    for i, market in enumerate(events):
        at = timestamp(market["as_of"])
        if previous_time and at <= previous_time:
            raise ValidationError("回放事件时间必须严格递增")
        if market.get("corporate_actions"):
            raise ValidationError("此最小回放不处理公司行动，请先完成外部对账")
        if previous_day and at.date() != previous_day:
            for h in account["holdings"].values():
                h["available"] = h["shares"]
        account["confirmed_at"] = at.isoformat()
        check_context(market, account, cfg, at, demo=market.get("demo", False))
        rows = {r["symbol"]: r for r in market["stocks"]}
        for order in pending:
            row = rows.get(order["symbol"])
            reason = None
            q = 0
            if row is None or row["suspended"]:
                reason = "缺少行情或停牌"
            elif not row.get("execution_volume") or row["execution_volume"] <= 0:
                reason = "缺少下一事件成交量，不假设成交"
            elif order["action"] == "BUY" and (row["price"] >= row["limit_up"] or row["price"] > order["budget_price"]):
                reason = "涨停或买价超预算"
            elif order["action"] == "SELL" and (row["price"] <= row["limit_down"] or row["price"] < order["budget_price"]):
                reason = "跌停或卖价低于预算"
            else:
                cap = min(order["quantity"], int(row["execution_volume"] * cfg["participation"]))
                h = account["holdings"].setdefault(order["symbol"], {
                    "shares": 0, "available": 0, "cost": 0,
                    "industry": row["industry"], "board": row["board"]})
                price = cents(row["price"])
                cash = cents(account["cash"])
                if order["action"] == "BUY":
                    q = buy_quantity(cap, row["board"])
                    while q and price * q + fee(price * q, "BUY", cfg["fees"]) > cash:
                        q = buy_quantity(q - 1, row["board"])
                    if q:
                        charges = fee(price * q, "BUY", cfg["fees"])
                        h["cost"] = (cents(h["cost"]) * h["shares"] + price * q + charges) / (h["shares"] + q) / 100
                        h["shares"] += q
                        cash -= price * q + charges
                else:
                    q = sell_quantity(cap, h["available"], row["board"])
                    if q:
                        charges = fee(price * q, "SELL", cfg["fees"])
                        h["shares"] -= q
                        h["available"] -= q
                        cash += price * q - charges
                if q:
                    account["cash"] = cash / 100
                    trades.append({"signal_at": previous_time.isoformat(), "filled_at": at.isoformat(),
                                   "symbol": order["symbol"], "side": order["action"],
                                   "quantity": q, "price": row["price"], "fee": charges / 100})
                else:
                    reason = "资金、可卖数量或交易单位不满足"
            if reason or q < order["quantity"]:
                rejected.append({"at": at.isoformat(), "symbol": order["symbol"],
                                 "unfilled": order["quantity"] - q,
                                 "reason": reason or "参与率或交易单位限制导致部分成交"})
        packet = evidence(market, account, cfg)
        if responses is not None:
            if market["as_of"] not in responses:
                raise ValidationError("冻结响应缺少事件时间")
            synthesis = validate_synthesis(responses[market["as_of"]], packet)
        else:
            synthesis = baseline(packet)
        decisions, projection = allocate(packet, synthesis, market, account, cfg)
        pending = [d for d in decisions if d["quantity"]]
        nav = projection["nav"]
        peak = max(peak, nav)
        max_dd = max(max_dd, 1 - nav / peak)
        curve.append({"at": at.isoformat(), "nav": nav, "cash": account["cash"]})
        previous_day, previous_time = at.date(), at
    return {"type": "snapshot_replay", "demo": any(e.get("demo") for e in events),
            "limitations": ["不是完整历史回测，不证明策略盈利", "未模拟盘口排队与公司行动",
                            "零成交量或缺失成交量不成交", "每笔委托仅在下一事件尝试，剩余部分撤销",
                            "最终事件的新建议不成交", "冻结LLM历史回放仍可能受模型历史知识污染"],
            "return": curve[-1]["nav"] / curve[0]["nav"] - 1,
            "max_drawdown": max_dd, "trade_count": len(trades),
            "total_fees": round(sum(t["fee"] for t in trades), 2),
            "trades": trades, "unfilled": rejected, "equity_curve": curve,
            "final_account": account}

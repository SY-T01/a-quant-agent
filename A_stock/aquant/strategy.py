from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from .domain import (ValidationError, buy_quantity, cents, fee, sell_quantity,
                     timestamp, validate_account, validate_market, validate_settings)


def evidence(market, account, cfg):
    """No account cash, cost basis or absolute share counts enter the LLM payload."""
    rows = market["stocks"]
    nav = account["cash"] + sum(h["shares"] * next(
        r["price"] for r in rows if r["symbol"] == code)
        for code, h in account["holdings"].items() if h["shares"])
    breadth = sum(r["price"] > r["ma20"] for r in rows) / len(rows)
    stocks = []
    for row in rows:
        trend = 1 if row["price"] > row["ma20"] > row["ma60"] else -1
        momentum = max(-1, min(1, row["momentum20"] / 0.15))
        industry = max(-1, min(1, row["industry_return20"] / 0.10))
        risk = min(1, row["atr14"] / row["price"] / 0.06)
        score = round(0.4 * trend + 0.35 * momentum + 0.25 * industry - 0.2 * risk, 4)
        holding = account["holdings"].get(row["symbol"], {})
        forced_exit = bool(holding.get("shares") and (
            row["st"] or row["price"] < row["ma60"] or row["atr14"] / row["price"] > 0.10))
        eligible = not (row["st"] or row["suspended"] or row["listing_days"] < 120)
        stocks.append({"id": f"quant:{row['symbol']}", "symbol": row["symbol"],
                       "trend": trend, "momentum": momentum, "industry_strength": industry,
                       "risk": round(risk, 4), "score": score, "eligible": eligible,
                       "forced_exit": forced_exit,
                       "holding_weight": round(holding.get("shares", 0) * row["price"] / nav, 4)})
    return {"template_version": "v1", "as_of": market["as_of"],
            "market": {"breadth": breadth, "scope": market["coverage"]},
            "constraints": {k: cfg[k] for k in ("max_equity", "max_stock", "max_industry")},
            "stocks": sorted(stocks, key=lambda s: (-s["score"], s["symbol"])),
            "news": [{k: n[k] for k in ("id", "title", "summary", "url", "published_at", "received_at") if k in n}
                     for n in market.get("news", [])]}


def baseline(packet):
    return {"summary": "规则基线：趋势、动量及行业强弱共同判断；评分不代表收益概率。",
            "recommendations": [
                {"symbol": s["symbol"], "action": "SELL" if s["forced_exit"] or s["score"] < -0.3
                 else "BUY" if s["eligible"] and s["score"] > 0.3 else "HOLD",
                 "reason": f"趋势{ s['trend'] }，动量{ s['momentum'] :.2f}，综合评分{ s['score'] :.2f}",
                 "evidence_ids": [s["id"]], "uncertainty": "规则未经收益准入验证"}
                for s in packet["stocks"]]}


def check_context(market, account, cfg, as_of, demo=False):
    validate_settings(cfg)
    validate_account(account)
    validate_market(market, as_of, cfg["quote_ttl_seconds"], demo)
    age = (as_of - timestamp(account["confirmed_at"])).total_seconds()
    if age < 0 or age > cfg["account_ttl_hours"] * 3600:
        raise ValidationError("账户未确认或快照过期")
    if timestamp(account["confirmed_at"]).date() != as_of.date():
        raise ValidationError("每天需确认券商账户及可卖数量")
    rows = {r["symbol"]: r for r in market["stocks"]}
    for code, holding in account["holdings"].items():
        if not holding["shares"]:
            continue
        if code not in rows:
            raise ValidationError(f"持仓{code}缺少有效价格，无法评估组合")
        if holding["board"] != rows[code]["board"] or holding["industry"] != rows[code]["industry"]:
            raise ValidationError(f"持仓{code}板块或行业与行情不一致")
    if account["cash"] + sum(rows[c]["price"] * h["shares"] for c, h in account["holdings"].items() if h["shares"]) <= 0:
        raise ValidationError("净资产必须大于0")
    day = as_of.date().isoformat()
    if not cfg["rules"]["effective_from"] <= day <= cfg["rules"]["reviewed_through"]:
        raise ValidationError("交易规则不在已审核有效区间")
    if not demo:
        if day not in market.get("trading_days", []):
            raise ValidationError("交易日历不包含本日，暂停建议")
        minutes = as_of.hour * 60 + as_of.minute
        if not (570 <= minutes < 690 or 780 <= minutes < 897):
            raise ValidationError("不在连续竞价时段")
        if not market.get("rules_verified", False):
            raise ValidationError("行情供应方尚未确认板块规则和涨跌停价格")


def allocate(packet, synthesis, market, account, cfg):
    """Conservative buys: don't spend planned sales or release their risk headroom."""
    rows = {r["symbol"]: r for r in market["stocks"]}
    holdings = account["holdings"]
    nav = cents(account["cash"]) + sum(cents(rows[c]["price"]) * h["shares"]
                                               for c, h in holdings.items() if h["shares"])
    if nav <= 0:
        raise ValidationError("净资产必须大于0")
    cash = cents(account["cash"])
    equity = nav - cash
    industries = {}
    for code, h in holdings.items():
        if h["shares"]:
            industry = rows[code]["industry"]
            industries[industry] = industries.get(industry, 0) + cents(rows[code]["price"]) * h["shares"]
    breadth = packet["market"]["breadth"]
    equity_cap = min(cfg["max_equity"], 0.85 if breadth >= 0.7 else 0.65 if breadth >= 0.4 else 0.4)
    # A separate hypothetical reduction book avoids overselling across holdings.
    # It is never used as released headroom for new buys in the real risk book.
    reduction_equity = equity
    reduction_industries = dict(industries)
    reductions = {}
    for code, holding in holdings.items():
        if not holding["shares"]:
            continue
        row = rows[code]
        value = cents(row["price"]) * holding["shares"]
        excess = max(0, value - int(nav * cfg["max_stock"]),
                     reduction_industries[row["industry"]] - int(nav * cfg["max_industry"]),
                     reduction_equity - int(nav * equity_cap))
        shares = min(holding["shares"], int((excess + cents(row["price"]) - 1) // cents(row["price"])))
        if shares:
            reductions[code] = shares
            reduction_equity -= shares * cents(row["price"])
            reduction_industries[row["industry"]] -= shares * cents(row["price"])
    signals = {s["symbol"]: s for s in packet["stocks"]}
    instructions = {s["symbol"]: s for s in synthesis["recommendations"]}
    decisions = []
    spent = 0
    sale_proceeds = 0
    sale_value = 0
    for signal in packet["stocks"]:
        code = signal["symbol"]
        row = rows[code]
        h = holdings.get(code, {"shares": 0, "available": 0})
        rec = instructions.get(code, {"action": "HOLD", "reason": "综合模型未给出明确意见"})
        action = "SELL" if signal["forced_exit"] or code in reductions else rec["action"]
        price = cents(row["price"])
        current = price * h["shares"]
        blockers = []
        quantity = 0
        fees = 0
        execution_price = price
        if row["suspended"]:
            blockers.append("停牌，无法执行")
        elif action == "SELL" and h["shares"]:
            if price <= cents(row["limit_down"]):
                blockers.append("跌停，无法保证卖出，暂停执行数量")
            else:
                liquidity_cap = int(row["adv20"] * cfg["participation"] / row["price"])
                desired = h["shares"] if signal["forced_exit"] or rec["action"] == "SELL" else reductions[code]
                # Round up a required reduction if legal; never pretend the constraint is resolved.
                if row["board"] in ("MAIN", "CHINEXT"):
                    desired = min(h["shares"], ((desired + 99) // 100) * 100)
                quantity = sell_quantity(min(desired, liquidity_cap), h["available"], row["board"])
                if code in reductions:
                    blockers.append("现有仓位超限，规则要求减仓；实际成交前仍计入全部风险")
                if not quantity:
                    blockers.append("T+1、交易单位或流动性约束导致无可卖数量")
                execution_price = max(cents(row["limit_down"]), round(price * (1 - cfg["fees"]["slippage"])))
                if quantity:
                    fees = fee(quantity * execution_price, "SELL", cfg["fees"])
                    sale_proceeds += quantity * execution_price - fees
                    sale_value += price * quantity
        elif action == "BUY":
            if not signal["eligible"]:
                blockers.append("不符合新开仓条件")
            if row["board"] not in account["permissions"]:
                blockers.append("未确认板块交易权限")
            if price >= cents(row["limit_up"]):
                blockers.append("涨停，拦截买入")
            if signal["score"] <= 0:
                blockers.append("量化证据不足以支持新增风险")
            execution_price = min(cents(row["limit_up"]), round(price * (1 + cfg["fees"]["slippage"])))
            industry = row["industry"]
            # Risk distance caps the entire holding, not just the incremental order.
            target = min(int(nav * cfg["max_stock"] / execution_price),
                         int(nav * cfg["risk_per_stock"] / (cents(row["atr14"]) * cfg["atr_multiple"])))
            headroom = min(int(nav * cfg["max_industry"]) - industries.get(industry, 0),
                           int(nav * equity_cap) - equity,
                           cash - int(nav * (1 - equity_cap)),
                           cents(row["adv20"] * cfg["participation"]))
            cap = min(target - h["shares"], max(0, headroom) // execution_price)
            quantity = buy_quantity(max(0, cap), row["board"]) if not blockers else 0
            while quantity and (quantity * execution_price + fee(quantity * execution_price, "BUY", cfg["fees"])) > min(
                    cash - int(nav * (1 - equity_cap)), headroom):
                quantity = buy_quantity(quantity - 1, row["board"])
            if quantity:
                fees = fee(quantity * execution_price, "BUY", cfg["fees"])
                amount = quantity * execution_price
                cash -= amount + fees
                spent += amount + fees
                equity += amount
                industries[industry] = industries.get(industry, 0) + amount
            elif not blockers:
                blockers.append("现金储备、仓位、ATR风险预算或交易单位限制")
        decisions.append({"symbol": code, "name": row["name"], "action": action if quantity else "HOLD",
                          "requested_action": rec["action"], "quantity": quantity,
                          "reference_price": row["price"], "budget_price": execution_price / 100,
                          "estimated_fee": fees / 100, "reason": rec["reason"],
                          "forced_exit": signal["forced_exit"], "blockers": blockers,
                          "evidence_ids": rec.get("evidence_ids", [])})
    if cash < 0:
        raise ValidationError("内部现金校验失败")
    return decisions, {"nav": nav / 100, "buy_cost_with_fees": spent / 100,
                       "conditional_sale_proceeds": sale_proceeds / 100,
                       "cash_without_sales": cash / 100,
                       "cash_if_all_filled": (cash + sale_proceeds) / 100,
                       "equity_if_all_filled": (equity - sale_value) / 100,
                       "equity_cap": equity_cap}


def report_base(account, market, cfg, as_of, slot, mode):
    payload = {"account": account, "market": market, "settings": cfg,
               "as_of": as_of.isoformat(), "slot": slot, "mode": mode}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {"id": digest[:20], "session_key": f"{as_of.date()}:{slot}:{mode}",
            "mode": mode, "account_version": account["version"], "as_of": as_of.isoformat(),
            "expires_at": (as_of + timedelta(minutes=cfg["valid_minutes"])).isoformat(),
            "input_hash": digest, "rule_version": cfg["rules"]["version"],
            "coverage": market.get("coverage", "未知"), "status": "PAUSED", "errors": [],
            "decisions": [], "summary": "", "projection": {}}

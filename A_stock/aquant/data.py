"""Strict local input contract plus optional HTTPS snapshot ingestion."""
from __future__ import annotations

import json
import math
import urllib.request
from datetime import timedelta

from .domain import ValidationError, timestamp
from .llm import NoRedirect


def fetch_snapshot(url):
    if not url.startswith("https://"):
        raise ValidationError("行情接口必须使用HTTPS")
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as response:
        raw = response.read(10_000_001)
    if len(raw) > 10_000_000:
        raise ValidationError("行情快照过大")
    return json.loads(raw)


def daily_features(bars, cutoff):
    """Compute completed daily features only. Amount is yuan, not lots/10k yuan."""
    selected = [b for b in bars if timestamp(b["available_at"]) <= cutoff]
    selected.sort(key=lambda b: b["date"])
    if len(selected) < 61 or len({b["date"] for b in selected}) != len(selected):
        raise ValidationError("至少需要61条不重复日线")
    if any(b["date"] >= cutoff.date().isoformat() for b in selected):
        raise ValidationError("日线特征不能包含当日或未来数据")
    for b in selected:
        values = [b[k] for k in ("open", "high", "low", "close", "amount")]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in values):
            raise ValidationError("日线包含无效价格或成交额")
        if not b["low"] <= min(b["open"], b["close"]) <= max(b["open"], b["close"]) <= b["high"]:
            raise ValidationError("日线高低价无效")
    closes = [b["close"] for b in selected]
    tr = [max(b["high"] - b["low"], abs(b["high"] - selected[i - 1]["close"]),
              abs(b["low"] - selected[i - 1]["close"])) for i, b in enumerate(selected) if i]
    return {"ma20": sum(closes[-20:]) / 20, "ma60": sum(closes[-60:]) / 60,
            "momentum20": closes[-1] / closes[-21] - 1,
            "atr14": sum(tr[-14:]) / 14,
            "adv20": sum(b["amount"] for b in selected[-20:]) / 20,
            "features_as_of": selected[-1]["available_at"]}


def demo_inputs():
    as_of = timestamp("2026-11-04T09:40:00+08:00")
    stocks = []
    for i in range(8):
        price = 10 + 2 * i
        weak = i == 1
        stocks.append({"symbol": f"60000{i}.SH", "name": f"演示股票{i+1}（虚构行情）",
                       "industry": "演示行业A" if i < 3 else f"演示行业{i}", "board": "MAIN",
                       "price": price, "ma20": price * (1.05 if weak else 0.97),
                       "ma60": price * (1.1 if weak else 0.92),
                       "momentum20": -0.08 if weak else 0.10 - i * 0.003,
                       "industry_return20": -0.03 if weak else 0.08,
                       "atr14": price * 0.025, "adv20": 100_000_000,
                       "st": False, "suspended": False, "listing_days": 1000,
                       "limit_up": round(price * 1.1, 2), "limit_down": round(price * 0.9, 2),
                       "features_as_of": "2026-11-03T16:00:00+08:00"})
    account = {"version": 1, "cash": 90_000, "confirmed_at": "2026-11-04T09:30:00+08:00",
               "permissions": ["MAIN"], "holdings": {
                   "600001.SH": {"shares": 500, "available": 300, "cost": 13,
                                 "board": "MAIN", "industry": "演示行业A"}}}
    market = {"demo": True, "source": "synthetic-fixture-not-live", "kind": "intraday",
              "as_of": as_of.isoformat(), "received_at": as_of.isoformat(),
              "coverage": "仅8只虚构行情，不代表全市场", "stocks": stocks,
              "trading_days": ["2026-11-04"], "rules_verified": False, "news": []}
    return account, market, as_of

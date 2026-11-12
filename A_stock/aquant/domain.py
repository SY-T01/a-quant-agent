from __future__ import annotations

import math
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


class ValidationError(ValueError):
    pass


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValidationError("时间必须包含时区")
    return result.astimezone(SHANGHAI)


def number(value, name: str, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name}必须是数字")
    if not math.isfinite(value) or value < minimum:
        raise ValidationError(f"{name}无效")
    return float(value)


def integer(value, name: str, minimum: int = 0) -> int:
    number(value, name, minimum)
    if not isinstance(value, int):
        raise ValidationError(f"{name}必须是整数")
    return value


def symbol(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", value):
        raise ValidationError("股票代码必须如600000.SH")
    return value


def cents(value) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fee(amount_cents: int, side: str, cfg: dict) -> int:
    commission = max(cents(cfg["minimum_commission"]),
                     round(amount_cents * cfg["commission_rate"]))
    return commission + round(amount_cents * cfg["transfer_rate"]) + (
        round(amount_cents * cfg["sell_tax_rate"]) if side == "SELL" else 0)


def validate_account(account: dict) -> None:
    if not isinstance(account, dict) or not isinstance(account.get("holdings"), dict):
        raise ValidationError("账户及持仓必须是JSON对象")
    number(account["cash"], "可用现金")
    timestamp(account["confirmed_at"])
    integer(account["version"], "账户版本", 1)
    for holding in account["holdings"].values():
        if not isinstance(holding, dict) or holding.get("board") not in ("MAIN", "CHINEXT", "STAR", "BSE") or not holding.get("industry"):
            raise ValidationError("持仓需要有效板块和行业")
    if not isinstance(account["permissions"], list):
        raise ValidationError("permissions必须是列表")
    if not set(account["permissions"]) <= {"MAIN", "CHINEXT", "STAR", "BSE"}:
        raise ValidationError("账户权限包含未知板块")
    for code, holding in account["holdings"].items():
        symbol(code)
        integer(holding["shares"], "持仓数量")
        integer(holding["available"], "可卖数量")
        if holding["available"] > holding["shares"]:
            raise ValidationError("可卖数量不能大于持仓")
        number(holding["cost"], "成本")


def validate_settings(cfg: dict) -> None:
    for key in ("max_equity", "max_stock", "max_industry", "risk_per_stock", "participation"):
        value = number(cfg[key], key)
        if value > 1:
            raise ValidationError(f"{key}不能大于1")
    for key in ("quote_ttl_seconds", "account_ttl_hours", "valid_minutes", "atr_multiple"):
        number(cfg[key], key, 0.000001)
    for key in ("commission_rate", "transfer_rate", "sell_tax_rate", "slippage"):
        if number(cfg["fees"][key], key) > 0.1:
            raise ValidationError(f"{key}不合理")
    number(cfg["fees"]["minimum_commission"], "最低佣金")


def validate_market(market: dict, as_of: datetime, ttl: float, demo: bool = False) -> None:
    if not market.get("source"):
        raise ValidationError("行情缺少来源")
    if market.get("demo", False) != demo:
        raise ValidationError("行情的演示标记与运行模式不一致")
    if market.get("kind") != "intraday":
        raise ValidationError("盘中建议必须使用盘中快照")
    for field in ("as_of", "received_at"):
        age = (as_of - timestamp(market[field])).total_seconds()
        if age < 0 or age > ttl:
            raise ValidationError(f"行情{field}过期或来自未来")
    rows = market["stocks"]
    if not rows:
        raise ValidationError("行情为空")
    codes = set()
    for row in rows:
        code = symbol(row["symbol"])
        if code in codes:
            raise ValidationError("行情代码重复")
        codes.add(code)
        for flag in ("st", "suspended"):
            if not isinstance(row[flag], bool):
                raise ValidationError(f"{flag}必须是布尔值")
        if not row.get("industry"):
            raise ValidationError("行业未知")
        for key in ("price", "ma20", "ma60", "atr14", "adv20", "limit_up", "limit_down"):
            number(row[key], key, 0.000001)
        for key in ("momentum20", "industry_return20"):
            number(row[key], key, -1)
        integer(row["listing_days"], "上市交易天数")
        if row["limit_down"] >= row["limit_up"]:
            raise ValidationError("涨跌停价格无效")
        if not row["limit_down"] <= row["price"] <= row["limit_up"]:
            raise ValidationError("价格超出涨跌停范围")
        if row["board"] not in ("MAIN", "CHINEXT", "STAR", "BSE"):
            raise ValidationError("板块未知")
        expected_exchange = {"MAIN": ("SH", "SZ"), "CHINEXT": ("SZ",),
                             "STAR": ("SH",), "BSE": ("BJ",)}[row["board"]]
        if code[-2:] not in expected_exchange:
            raise ValidationError("板块和交易所不匹配")
        history_time = timestamp(row["features_as_of"])
        if history_time.date() >= as_of.date():
            raise ValidationError("日频特征必须来自此前交易日，禁止使用未完成日线")
        if (as_of - history_time).days > 30:
            raise ValidationError("历史特征过期")
    news_ids = set()
    for news in market.get("news", []):
        if timestamp(news["published_at"]) > as_of or timestamp(news["received_at"]) > as_of:
            raise ValidationError("资讯在决策时刻不可获得")
        if not news.get("url", "").startswith("https://") or not news.get("id"):
            raise ValidationError("资讯需要来源链接和编号")
        if news["id"] in news_ids or news["id"].startswith("quant:"):
            raise ValidationError("资讯证据ID重复或与量化证据冲突")
        news_ids.add(news["id"])


def buy_quantity(cap: int, board: str) -> int:
    if board == "STAR":
        return cap if cap >= 200 else 0
    if board == "BSE":
        return cap if cap >= 100 else 0
    return cap // 100 * 100


def sell_quantity(desired: int, available: int, board: str) -> int:
    cap = min(desired, available)
    minimum = 200 if board == "STAR" else 100
    if cap == available:
        return cap
    if board in ("STAR", "BSE"):
        return cap if cap >= minimum else 0
    return cap // 100 * 100

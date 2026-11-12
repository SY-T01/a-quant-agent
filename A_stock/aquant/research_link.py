"""Public selector evidence and review gates for the daily stock research packet."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from .selector_bridge import engine

DEFAULT_PROJECT = Path(__file__).resolve().parents[2] / "stock_selecter"


def attach_research(packet, project=None):
    stocks = [row for row in packet["stocks"] if row["asset_type"] == "stock"]
    if not stocks:
        return
    root = Path(project) if project else DEFAULT_PROJECT
    reason = "缺少有效的本期选股研究，暂停新增买入倾向"
    try:
        load = engine(root)
        report = json.loads((root / "reports/sector_research/research.json").read_text())
        now = datetime.fromisoformat(packet["as_of"])
        generated = datetime.fromisoformat(report["asof"])
        if generated.tzinfo is None or generated > now:
            raise ValueError("研究报告时间不可用于本次日报")
        prefs = root / "configs/research_preferences.json"
        matching = (report.get("investment_horizon") == load("stock_selector.investment_horizon").load(prefs) and
                    report.get("capital_settings") == load("stock_selector.capital_allocation").load(prefs))
        pipeline = load("stock_selector.model_review").finalize(report)
        health = load("stock_selector.health").assess(report, packet["as_of"])
        current = bool(matching and pipeline["review_current"] and not health["blocked"])
        main = {row["symbol"]: row for row in report.get("stock_recommendations", {}).get("stocks", [])}
        decisions = {row["symbol"]: row for row in pipeline["decisions"]}
        packet["selector_research"] = {"report_at": report["asof"], "data_at": report.get("data_times", {}).get("last"),
            "current": current, "note": "行业初筛与逐股复核；有条件通过仍须人工核对触发条件，不代表已经满足买入前提。"}
        for stock in stocks:
            code = stock["symbol"].split(":", 1)[1]
            symbol = next((key for key in main if key.split(".")[0] == code), None)
            decision = decisions.get(symbol, {})
            review = decision.get("review") or {}
            allowed = bool(current and symbol and decision.get("verdict") == "approve_conditional")
            label = decision.get("label", "未进入本期行业内主名单")
            if decision and not current:
                label = "历史意见 / " + label
            stock["selector_research"] = {"label": label, "allow_buy_tendency": allowed,
                "reason": review.get("reason") or decision.get("pending_reason") or reason,
                "blockers": review.get("blockers", []), "entry_condition": (review.get("buy_plan") or {}).get("entry_condition"),
                "report_at": report["asof"], "data_at": report.get("data_times", {}).get("last")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        packet["selector_research"] = {"current": False, "note": reason}
        for stock in stocks:
            stock["selector_research"] = {"label": "研究不可用，待复核", "allow_buy_tendency": False, "reason": reason}


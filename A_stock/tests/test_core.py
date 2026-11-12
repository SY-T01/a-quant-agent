import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from aquant.cli import main
from aquant.data import daily_features, demo_inputs
from aquant.domain import (ValidationError, buy_quantity, sell_quantity, timestamp,
                           validate_market)
from aquant.llm import validate_synthesis
from aquant.report import render, send
from aquant.service import run
from aquant.store import Store
from aquant.strategy import allocate, baseline, check_context, evidence

ROOT = Path(__file__).resolve().parent.parent


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.sqlite")
        self.account, self.market, self.at = demo_inputs()
        self.cfg = json.loads((ROOT / "config/settings.json").read_text())
        self.store.import_account(self.account)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def report(self, **kwargs):
        return run(self.store, self.market, self.cfg, self.at, "am", demo=True, **kwargs)

    def fill(self, **kwargs):
        result = {"id": "broker-1", "symbol": "600000.SH", "board": "MAIN", "industry": "演示行业A",
                  "side": "BUY", "quantity": 200, "price": 10, "fee": 5,
                  "at": self.at.isoformat()}
        result.update(kwargs)
        return result

    def test_demo_complete_and_idempotent(self):
        first = self.report()
        second = self.report()
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "DEMO")
        self.assertTrue(any(d["action"] == "BUY" for d in first["decisions"]))
        sale = next(d for d in first["decisions"] if d["symbol"] == "600001.SH")
        self.assertEqual(sale["quantity"], 300)
        self.assertEqual(self.store.account()["cash"], 90_000)

    def test_stale_quote_pauses(self):
        self.market["as_of"] = (self.at - timedelta(minutes=10)).isoformat()
        result = self.report()
        self.assertEqual(result["status"], "PAUSED")
        self.assertEqual(result["decisions"], [])

    def test_future_quote_pauses(self):
        self.market["received_at"] = (self.at + timedelta(seconds=1)).isoformat()
        self.assertEqual(self.report()["status"], "PAUSED")

    def test_missing_holding_price_pauses(self):
        self.market["stocks"] = [r for r in self.market["stocks"] if r["symbol"] != "600001.SH"]
        self.assertEqual(self.report()["status"], "PAUSED")

    def test_unknown_board_pauses(self):
        self.market["stocks"][0]["board"] = "UNKNOWN"
        self.assertEqual(self.report()["status"], "PAUSED")

    def test_invalid_features_pauses(self):
        self.market["stocks"][0]["price"] = float("nan")
        self.assertEqual(self.report()["status"], "PAUSED")

    def test_same_day_features_rejected(self):
        self.market["stocks"][0]["features_as_of"] = self.at.isoformat()
        self.assertEqual(self.report()["status"], "PAUSED")

    def test_unknown_calendar_blocks_real_mode(self):
        self.market["demo"] = False
        self.market["trading_days"] = []
        with self.assertRaises(ValidationError):
            check_context(self.market, self.account, self.cfg, self.at)

    def test_rules_expiry(self):
        self.cfg["rules"]["reviewed_through"] = "2026-11-03"
        self.assertEqual(self.report()["status"], "PAUSED")

    def test_account_version_optimistic_lock(self):
        with self.assertRaises(ValidationError):
            self.store.fill(self.fill(), 5)
        self.assertEqual(self.store.account()["cash"], 90_000)

    def test_fill_t_plus_one_and_idempotence(self):
        fill = self.fill()
        account = self.store.fill(fill, 1)
        self.assertEqual(account["cash"], 87_995)
        self.assertEqual(account["holdings"]["600000.SH"]["available"], 0)
        self.assertEqual(self.store.fill(fill, 1), account)
        with self.assertRaises(ValidationError):
            self.store.fill(self.fill(id="sell", side="SELL"), account["version"])
        self.assertEqual(self.store.account(), account)

    def test_conflicting_fill_id(self):
        self.store.fill(self.fill(), 1)
        with self.assertRaises(ValidationError):
            self.store.fill(self.fill(price=12), 2)

    def test_fill_no_negative_cash(self):
        with self.assertRaises(ValidationError):
            self.store.fill(self.fill(quantity=10000), 1)
        self.assertEqual(self.store.account()["version"], 1)

    def test_cross_day_requires_reconciliation(self):
        with self.assertRaises(ValidationError):
            self.store.fill(self.fill(at="2026-11-05T09:40:00+08:00"), 1)

    def test_unconfirmed_execution_blocks_next_report(self):
        first = self.report()
        next_report = run(self.store, self.market, self.cfg, self.at, "pm", demo=True)
        self.assertEqual(next_report["status"], "PAUSED")
        self.store.confirm(first["id"], 1, self.at.isoformat())
        after = run(self.store, self.market, self.cfg, self.at, "pm-retry", demo=True)
        self.assertEqual(after["status"], "DEMO")

    def test_partial_fill_then_feedback(self):
        report = self.report()
        self.store.fill(self.fill(), 1)
        self.store.confirm(report["id"], 2, self.at.isoformat())
        self.assertFalse(self.store.unresolved())
        self.assertEqual(self.store.account()["holdings"]["600000.SH"]["shares"], 200)

    def test_permissions_and_st_block_new_buys(self):
        self.market["stocks"][0]["st"] = True
        self.account["permissions"] = []
        self.store.import_account(self.account)
        self.assertFalse(any(d["action"] == "BUY" for d in self.report()["decisions"]))

    def test_limit_prices_block_fictional_fills(self):
        self.market["stocks"][0]["limit_up"] = 10
        self.market["stocks"][1]["limit_down"] = 12
        result = self.report()
        for code in ("600000.SH", "600001.SH"):
            self.assertEqual(next(d for d in result["decisions"] if d["symbol"] == code)["quantity"], 0)

    def test_cash_and_industry_aggregate_caps(self):
        result = self.report()
        rows = {r["symbol"]: r for r in self.market["stocks"]}
        nav = result["projection"]["nav"]
        values = {"演示行业A": 6000}
        for d in result["decisions"]:
            if d["action"] == "BUY":
                value = d["quantity"] * d["budget_price"]
                self.assertLessEqual(value, nav * self.cfg["max_stock"])
                industry = rows[d["symbol"]]["industry"]
                values[industry] = values.get(industry, 0) + value
        self.assertLessEqual(max(values.values()), nav * self.cfg["max_industry"])
        self.assertGreaterEqual(result["projection"]["cash_without_sales"], nav * 0.15)

    def test_cannot_spend_unfilled_sale(self):
        self.account["cash"] = 0
        self.store.import_account(self.account)
        result = self.report()
        self.assertFalse(any(d["action"] == "BUY" for d in result["decisions"]))
        self.assertGreater(result["projection"]["conditional_sale_proceeds"], 0)

    def test_board_units(self):
        self.assertEqual(buy_quantity(199, "STAR"), 0)
        self.assertEqual(buy_quantity(201, "STAR"), 201)
        self.assertEqual(buy_quantity(101, "BSE"), 101)
        self.assertEqual(buy_quantity(199, "MAIN"), 100)
        self.assertEqual(sell_quantity(51, 51, "MAIN"), 51)
        self.assertEqual(sell_quantity(100, 300, "STAR"), 0)

    def test_llm_validation_and_privacy(self):
        packet = evidence(self.market, self.account, self.cfg)
        result = baseline(packet)
        self.assertEqual(validate_synthesis(result, packet), result)
        encoded = json.dumps(packet)
        self.assertNotIn('"cash"', encoded)
        self.assertNotIn('"cost"', encoded)
        for mutation in ("unknown", "quantity", "missing", "citation"):
            bad = copy.deepcopy(result)
            if mutation == "unknown":
                bad["recommendations"][0]["symbol"] = "123456.SH"
            elif mutation == "quantity":
                bad["recommendations"][0]["quantity"] = 200
            elif mutation == "missing":
                bad["recommendations"].pop()
            else:
                bad["recommendations"][0]["evidence_ids"] = ["fabricated"]
            with self.assertRaises(ValidationError):
                validate_synthesis(bad, packet)

    def test_llm_cannot_override_exit(self):
        packet = evidence(self.market, self.account, self.cfg)
        result = baseline(packet)
        for rec in result["recommendations"]:
            rec["action"] = "BUY"
        decisions, _ = allocate(packet, result, self.market, self.account, self.cfg)
        self.assertEqual(next(d for d in decisions if d["symbol"] == "600001.SH")["action"], "SELL")

    def test_api_failure_not_silently_rules(self):
        with patch("aquant.service.synthesize", side_effect=ValidationError("API超时")):
            result = self.report(mode="deepseek")
        self.assertEqual(result["status"], "PAUSED")
        self.assertEqual(result["decisions"], [])

    def test_replay_mode_validates_model_output(self):
        packet = evidence(self.market, self.account, self.cfg)
        result = self.report(mode="replay", replay=baseline(packet))
        self.assertEqual(result["status"], "DEMO")
        self.assertIn("冻结响应回放", render(result))

    def test_demo_email_blocked_and_claim_idempotent(self):
        report = self.report()
        with self.assertRaises(ValidationError):
            send(self.store, report["id"], self.at)
        self.store.claim_email(report["id"])
        with self.assertRaises(ValidationError):
            self.store.claim_email(report["id"])

    def test_real_mode_requires_explicit_api(self):
        result = self.report(mode="deepseek")
        self.assertEqual(result["status"], "PAUSED")
        self.assertIn("--allow-api", result["errors"][0])

    def test_features_do_not_read_future(self):
        bars = []
        for i in range(70):
            day = self.at.date() - timedelta(days=75-i)
            bars.append({"date": day.isoformat(), "available_at": f"{day}T16:00:00+08:00",
                         "open": 10, "high": 11, "low": 9, "close": 10, "amount": 1e8})
        original = daily_features(bars, self.at)
        bars.append({"date": "2026-11-05", "available_at": "2026-11-05T16:00:00+08:00",
                     "open": 1000, "high": 1100, "low": 900, "close": 1000, "amount": 1e12})
        self.assertEqual(original, daily_features(bars, self.at))

    def test_duplicate_market_symbol(self):
        self.market["stocks"].append(copy.deepcopy(self.market["stocks"][0]))
        self.assertEqual(self.report()["status"], "PAUSED")

    def test_zero_nav_is_paused_instead_of_crashing(self):
        self.account["cash"] = 0
        self.account["holdings"] = {}
        self.store.import_account(self.account)
        self.assertEqual(self.report()["status"], "PAUSED")

    def test_overweight_existing_position_reduced_even_if_llm_holds(self):
        self.account["holdings"]["600000.SH"] = {
            "shares": 3000, "available": 3000, "cost": 10, "board": "MAIN", "industry": "演示行业A"}
        packet = evidence(self.market, self.account, self.cfg)
        response = baseline(packet)
        for rec in response["recommendations"]:
            rec["action"] = "HOLD"
        result, _ = allocate(packet, response, self.market, self.account, self.cfg)
        action = next(d for d in result if d["symbol"] == "600000.SH")
        self.assertEqual(action["action"], "SELL")
        self.assertGreater(action["quantity"], 0)

    def test_account_import_compare_and_swap(self):
        with self.assertRaises(ValidationError):
            self.store.import_account(self.account, expected_version=0)
        self.assertEqual(self.store.account()["version"], 1)

    def test_deepseek_http_contract(self):
        from aquant.llm import synthesize
        packet = evidence(self.market, self.account, self.cfg)
        response = {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(baseline(packet))}}], "usage": {"total_tokens": 100}}
        from unittest.mock import MagicMock
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-only-key"}), patch(
                "aquant.llm.urllib.request.build_opener", return_value=opener):
            result, audit = synthesize(packet, "test-model", allow_api=True)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(json.loads(req.data)["response_format"]["type"], "json_object")
        self.assertNotIn("test-only-key", json.dumps(audit))
        self.assertEqual(result, baseline(packet))

    def test_blank_model_response_limited_retries(self):
        from aquant.llm import synthesize
        from unittest.mock import MagicMock
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": " "}}]}).encode()
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-only-key"}), patch(
                "aquant.llm.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(ValidationError):
                synthesize(evidence(self.market, self.account, self.cfg), "test-model", allow_api=True)
        self.assertEqual(opener.open.call_count, 2)

    def real_report(self):
        self.market["demo"] = False
        self.market["rules_verified"] = True
        return run(self.store, self.market, self.cfg, self.at, "real-am")

    def test_smtp_accepted_and_no_duplicate(self):
        result = self.real_report()
        self.assertEqual(result["status"], "RESEARCH")
        env = {k: "test-value" for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO")}
        with patch.dict("os.environ", env), patch("aquant.report.smtplib.SMTP_SSL") as smtp:
            smtp.return_value.__enter__.return_value.send_message.return_value = {}
            send(self.store, result["id"], self.at)
            self.assertEqual(self.store.run(result["id"])["email_status"], "accepted")
            with self.assertRaises(ValidationError):
                send(self.store, result["id"], self.at)
        self.assertEqual(smtp.call_count, 1)

    def test_smtp_timeout_is_unknown(self):
        result = self.real_report()
        env = {k: "test-value" for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO")}
        with patch.dict("os.environ", env), patch("aquant.report.smtplib.SMTP_SSL", side_effect=TimeoutError):
            with self.assertRaises(ValidationError):
                send(self.store, result["id"], self.at)
        self.assertEqual(self.store.run(result["id"])["email_status"], "unknown")

    def test_expired_or_old_account_report_not_sent(self):
        result = self.real_report()
        with self.assertRaises(ValidationError):
            send(self.store, result["id"], self.at + timedelta(hours=1))
        self.store.import_account(self.account)
        with self.assertRaises(ValidationError):
            send(self.store, result["id"], self.at)


if __name__ == "__main__":
    unittest.main()

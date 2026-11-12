import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from aquant.desktop_model import DesktopModel, PROJECT, atomic_json
from aquant.domain import SHANGHAI, ValidationError
from aquant.research import ResearchModel
from aquant.research_worker import generate, research_lock
from aquant.research_link import attach_research
from aquant.watch import review_synthesis
from aquant.selector_bridge import engine

engine(PROJECT.parent / "stock_selecter")


class ResearchTests(unittest.TestCase):
    def setUp(self):
        from datetime import datetime
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "selector"
        self.path = self.root / "watch.json"
        atomic_json(self.path, {"state_dir": str(self.root / "watch"),
            "secrets_file": str(self.root / "secrets.json"), "schedule": ["14:00"],
            "model": "test-model", "instruments": [{"query": "159567", "type": "etf"}]})
        atomic_json(self.root / "secrets.json", {"DEEPSEEK_API_KEY": "private-test"}, private=True)
        self.desktop = DesktopModel(self.path)
        self.model = ResearchModel(self.desktop, self.project)
        self.patch_engine = patch("aquant.research.engine", return_value=importlib.import_module)
        self.patch_engine.start()
        horizon = importlib.import_module("stock_selector.investment_horizon").profile(6)
        stamp = datetime.now(SHANGHAI).isoformat()
        self.report = {"asof": stamp, "data_times": {"last": stamp}, "sectors": [],
            "research_priorities": ["TEST"], "investment_horizon": horizon, "capital_settings": None,
            "enrichment_scope": {"statements": {"RPT_DMSK_FN_INCOME": 1, "RPT_DMSK_FN_CASHFLOW": 1}},
            "stock_recommendations": {"scope": {"quotes_available": 1, "quote_pool": 1}, "stocks": [
                {"symbol": "000001.SZ", "name": "测试公司", "industry_code": "TEST", "industry_name": "测试行业",
                 "evidence_id": "recommendation:000001.SZ", "recommendation_score": 80, "reasons": ["测试证据"]}],
                "supplementary_stocks": []}}
        atomic_json(self.model.report_path, self.report)

    def tearDown(self):
        self.patch_engine.stop()
        if self.model.busy:
            self.model.stop()
            self.model.child.wait(timeout=5)
            self.model.poll()
        self.tmp.cleanup()

    def test_open_is_read_only_and_never_starts_jobs(self):
        before = self.model.report_path.read_bytes()
        with patch("subprocess.Popen") as popen:
            data = self.model.snapshot()
        popen.assert_not_called()
        self.assertFalse(data["current"])
        self.assertEqual(len(data["stocks"]), 1)
        self.assertEqual(self.model.report_path.read_bytes(), before)

    def test_candidate_hash_is_rechecked_instead_of_trusting_stored_labels(self):
        self.report["decision_pipeline"] = {"review_current": True, "decisions": [
            {"symbol": "000001.SZ", "label": "有条件通过", "verdict": "approve_conditional"}]}
        self.report["ai"] = {"candidate_hash": "outdated", "result": {"stock_reviews": []}}
        atomic_json(self.model.report_path, self.report)
        result = self.model.snapshot()
        self.assertFalse(result["current"])
        self.assertNotIn("通过", result["stocks"][0]["review_label"])

    def test_settings_validate_together_preserve_other_fields_and_mark_old_report(self):
        atomic_json(self.model.preferences_path, {"custom": "keep", "holding_months": 6})
        self.model.save_preferences("12", "10万", "conservative")
        prefs = json.loads(self.model.preferences_path.read_text())
        self.assertEqual(prefs["capital_yuan"], "100000.00")
        self.assertEqual(prefs["custom"], "keep")
        self.assertIn("设置已变化", self.model.snapshot()["summary"])
        before = self.model.preferences_path.read_bytes()
        for months, budget, style in (("25", "10万", "balanced"), ("6", "nan", "balanced"),
                                      ("6", "10万", "bad"), ("True", "10", "balanced")):
            with self.assertRaises(ValidationError):
                self.model.save_preferences(months, budget, style)
            self.assertEqual(self.model.preferences_path.read_bytes(), before)
        self.model.save_preferences("6", "", "balanced")
        self.assertIsNone(self.model.preferences()["capital"])

    def test_preference_save_respects_weekly_runner_lock(self):
        with research_lock(self.project):
            with self.assertRaises(ValidationError):
                self.model.save_preferences("6", "10万", "balanced")
        self.assertFalse(self.model.preferences_path.exists())

    def test_watch_add_is_idempotent_preserves_etf_and_creates_no_position(self):
        with patch.object(self.desktop, "state", return_value={"active": None}):
            self.model.add_watch("000001.SZ")
            self.model.add_watch("000001.SZ")
            with self.assertRaises(ValidationError):
                self.model.add_watch("000002.SZ")
        self.assertEqual(len(self.desktop.config()["instruments"]), 2)
        self.assertEqual(self.desktop.config()["instruments"][0]["query"], "159567")
        self.assertFalse((self.desktop.root() / "positions.sqlite").exists())

    def test_running_daily_task_blocks_watchlist_changes(self):
        before = self.path.read_bytes()
        with patch.object(self.desktop, "state", return_value={"active": {"pid": 1}}):
            with self.assertRaises(ValidationError):
                self.model.add_watch("000001.SZ")
        self.assertEqual(self.path.read_bytes(), before)

    def test_watchlist_limit_is_checked_before_writing(self):
        cfg = self.desktop.config()
        cfg["instruments"] = [{"query": str(100000 + i), "type": "stock"} for i in range(20)]
        atomic_json(self.path, cfg)
        with patch.object(self.desktop, "state", return_value={"active": None}):
            with self.assertRaises(ValidationError):
                self.model.add_watch("000001.SZ")
        self.assertEqual(len(self.desktop.config()["instruments"]), 20)

    def test_only_paid_actions_inherit_api_credentials_and_selected_model(self):
        for action in ("rescreen", "update", "health", "lab", "validation", "pdf", "replay", "ai", "full"):
            with self.subTest(action=action):
                child = Mock(pid=999999)
                child.poll.return_value = None
                with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "inherited-test"}), \
                     patch("subprocess.Popen", return_value=child) as popen:
                    self.model.start(action)
                env = popen.call_args.kwargs["env"]
                if action in ("ai", "full"):
                    self.assertEqual(env["DEEPSEEK_API_KEY"], "inherited-test")
                    self.assertEqual(env["DEEPSEEK_MODEL"], "test-model")
                else:
                    self.assertNotIn("DEEPSEEK_API_KEY", env)
                self.assertTrue(popen.call_args.kwargs["start_new_session"])
                self.assertNotIn("inherited-test", str(popen.call_args.args))
                child.poll.return_value = 0
                self.assertTrue(self.model.poll())
                self.assertIsNone(self.model.stream)
        self.assertEqual(self.model.log_path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("inherited-test", self.model.logs())

    def test_duplicate_job_and_missing_key_are_rejected(self):
        self.model.child = Mock()
        self.model.child.poll.return_value = None
        with self.assertRaises(ValidationError):
            self.model.start("health")
        self.model.child = None
        atomic_json(self.root / "secrets.json", {}, private=True)
        with patch.dict(os.environ, {}, clear=True), patch("subprocess.Popen") as popen:
            with self.assertRaises(ValidationError):
                self.model.start("ai")
        popen.assert_not_called()

    def test_cancel_stops_only_the_owned_process_group_and_releases_logs(self):
        self.model.child = Mock(pid=12345)
        self.model.child.poll.return_value = None
        self.model.action = "update"
        with patch("os.killpg") as kill:
            self.model.stop()
            self.model.stop()
            kill.assert_called_once_with(12345, signal.SIGTERM)
            self.model.child.poll.return_value = -15
            self.assertTrue(self.model.poll())
            self.assertEqual(kill.call_args.args, (12345, signal.SIGKILL))
        self.assertIsNone(self.model.child)

    def test_failed_generation_does_not_replace_published_report(self):
        before = self.model.report_path.read_bytes()
        def fail(module, args):
            out = Path(args[args.index("--out") + 1])
            atomic_json(out / "research.json", {"ai": {"error": "test failure"}})
        with patch("aquant.research_worker.invoke", side_effect=fail):
            with self.assertRaises(ValueError):
                generate(self.project, "ai", importlib.import_module)
        self.assertEqual(self.model.report_path.read_bytes(), before)

    def test_completed_process_is_reported_as_failure_when_return_code_nonzero(self):
        self.model.child = Mock()
        self.model.child.poll.return_value = 1
        self.model.action = "ai"
        self.assertTrue(self.model.poll())
        self.assertIn("未完成", self.model.message)
        self.assertFalse(self.model.busy)

    def research_packet(self, verdict="defer"):
        from stock_selector.model_review import candidate_hash
        review = {"symbol": "000001.SZ", "verdict": verdict, "reason": "公开研究依据", "counter_case": "公开风险",
                  "blockers": [], "evidence_ids": ["recommendation:000001.SZ"]}
        if verdict == "approve_conditional":
            review["buy_plan"] = {"entry_condition": "人工核对公告与行情后", "cancel_condition": "逻辑失效", "exit_review_condition": "复查",
                                  "tranches": [{"fraction": 1, "condition": "核实后"}]}
        self.report["ai"] = {"candidate_hash": candidate_hash(self.report), "result": {"stock_reviews": [review]}}
        atomic_json(self.model.report_path, self.report)
        packet = {"as_of": self.report["asof"], "stocks": [
            {"symbol": "stock:000001", "asset_type": "stock", "trend": 1, "momentum20": .1},
            {"symbol": "etf:159567", "asset_type": "etf", "trend": 1, "momentum20": .1}]}
        with patch("aquant.research_link.engine", return_value=importlib.import_module):
            attach_research(packet, self.project)
        return packet

    def test_daily_tendency_cannot_override_deferred_research_but_sell_is_preserved(self):
        packet = self.research_packet()
        self.assertNotIn("selector_research", packet["stocks"][1])
        synthesis = {"recommendations": [{"symbol": row["symbol"], "action": "BUY", "reason": "趋势上行"} for row in packet["stocks"]]}
        final, changes = review_synthesis(packet, synthesis)
        self.assertEqual([row["action"] for row in final["recommendations"]], ["HOLD", "BUY"])
        self.assertEqual(synthesis["recommendations"][0]["action"], "BUY")
        self.assertEqual(len(changes), 1)
        synthesis["recommendations"][0]["action"] = "SELL"
        self.assertEqual(review_synthesis(packet, synthesis)[0]["recommendations"][0]["action"], "SELL")
        self.assertNotIn("capital", json.dumps(packet))
        self.assertNotIn("private-test", json.dumps(packet))

    def test_current_conditional_review_and_daily_trend_are_both_required(self):
        packet = self.research_packet("approve_conditional")
        self.assertTrue(packet["stocks"][0]["selector_research"]["allow_buy_tendency"])
        packet["stocks"][0].update(trend=-1, momentum20=-.1)
        raw = {"recommendations": [{"symbol": "stock:000001", "action": "BUY", "reason": "模型意见"}]}
        self.assertEqual(review_synthesis(packet, raw)[0]["recommendations"][0]["action"], "HOLD")

    def test_future_or_changed_research_does_not_pass_daily_review(self):
        packet = self.research_packet("approve_conditional")
        self.model.save_preferences("12", "10万", "balanced")
        with patch("aquant.research_link.engine", return_value=importlib.import_module):
            attach_research(packet, self.project)
        self.assertFalse(packet["stocks"][0]["selector_research"]["allow_buy_tendency"])
        packet["as_of"] = "2000-01-01T00:00:00+08:00"
        with patch("aquant.research_link.engine", return_value=importlib.import_module):
            attach_research(packet, self.project)
        self.assertFalse(packet["selector_research"]["current"])

    def test_missing_research_blocks_only_stock_buy_tendency(self):
        self.model.report_path.unlink()
        packet = {"as_of": self.report["asof"], "stocks": [{"symbol": "stock:000001", "asset_type": "stock"}]}
        with patch("aquant.research_link.engine", return_value=importlib.import_module):
            attach_research(packet, self.project)
        self.assertFalse(packet["stocks"][0]["selector_research"]["allow_buy_tendency"])

    def test_future_report_is_historical_in_the_desktop_too(self):
        self.report["asof"] = "2099-01-01T00:00:00+08:00"
        atomic_json(self.model.report_path, self.report)
        data = self.model.snapshot()
        self.assertFalse(data["current"])
        self.assertIn("生成时间异常", data["summary"])

    def test_exiting_window_stops_research_and_waits_for_its_exit(self):
        from aquant.desktop import DesktopApp
        app = DesktopApp.__new__(DesktopApp)
        app.root, app.model, app.research, app.note = Mock(), Mock(), Mock(), Mock()
        app.closing = False
        app.model.child = None
        app.research.model.busy = True
        with patch.object(app, "wait_close") as wait:
            app.close()
        app.research.model.stop.assert_called_once()
        app.model.stop_owned.assert_not_called()
        wait.assert_called_once()
        app.root.destroy.assert_not_called()
        app.research.model.busy = False
        with patch.object(app, "finish_close") as finish:
            app.wait_close()
        finish.assert_called_once()


if __name__ == "__main__":
    unittest.main()

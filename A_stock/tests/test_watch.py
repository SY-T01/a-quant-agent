import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

try:
    import pandas as pd
except ImportError:
    pd = None

from aquant.domain import ValidationError, timestamp
from aquant.watch import cycle, check_config, AKDaily
from aquant.etf import summarize
from datetime import datetime
from aquant.domain import SHANGHAI


@unittest.skipIf(pd is None, "需要可选pandas")
class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = {"instruments": [{"query": "159567", "type": "etf"}], "schedule": ["18:30"],
                    "start_date": "20240101", "model": "test", "state_dir": self.temp.name}
        self.now = timestamp("2026-11-04T18:31:00+08:00")
        self.provider = Mock()
        self.provider.calendar.return_value = ["2026-11-03", "2026-11-04", "2026-11-05"]
        self.provider.resolve.return_value = ("159567", "港股创新药ETF银华")
        self.frame = pd.DataFrame({"日期": pd.date_range(end="2026-11-04", periods=65).date,
                                   "开盘": [10.] * 65, "收盘": [10.] * 65, "最高": [11.] * 65,
                                   "最低": [9.] * 65, "成交额": [1e6] * 65})
        self.provider.history.return_value = self.frame
        self.sender = Mock()
        self.model = Mock(side_effect=lambda p, m, **kw: ({"summary": "量化信号分歧，观望。", "recommendations": [
            {"symbol": s["symbol"], "action": "HOLD", "reason": "均线无明显趋势", "evidence_ids": [s["id"]],
             "uncertainty": "缺少实时净值"} for s in p["stocks"]]}, {"model": "test"}))
        self.env = patch("aquant.watch.credentials", return_value={k: "test" for k in (
            "DEEPSEEK_API_KEY", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def run_cycle(self, **kwargs):
        return cycle(self.cfg, self.now, provider=self.provider, synthesizer=self.model, sender=self.sender, **kwargs)

    def test_automatic_full_pipeline_and_no_repeat(self):
        self.assertIn("accepted", self.run_cycle())
        self.assertIn("已有任务", self.run_cycle())
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(self.sender.call_count, 1)
        self.assertTrue(list(Path(self.temp.name).glob("*.txt")))

    def test_stale_data_blocks_llm_and_mail(self):
        self.provider.history.return_value = self.frame.iloc[:-1]
        with self.assertRaises(ValidationError):
            self.run_cycle()
        self.model.assert_not_called()
        self.sender.assert_not_called()

    def test_weekend_and_before_schedule(self):
        self.provider.calendar.return_value = ["2026-11-03", "2026-11-05"]
        self.assertIn("非交易日", self.run_cycle())
        self.cfg["schedule"] = ["19:00"]
        self.assertIn("未到", self.run_cycle())
        self.sender.assert_not_called()

    def test_preview_does_not_send(self):
        self.assertIn("preview", self.run_cycle(preview=True))
        self.sender.assert_not_called()

    def test_smtp_unknown_never_retries(self):
        self.sender.side_effect = TimeoutError
        with self.assertRaises(ValidationError):
            self.run_cycle()
        self.assertIn("unknown", self.run_cycle())
        self.assertEqual(self.sender.call_count, 1)

    def test_model_failure_is_not_rules_fallback(self):
        self.model.side_effect = ValidationError("模型格式无效")
        with self.assertRaises(ValidationError):
            self.run_cycle()
        self.sender.assert_not_called()
        self.assertIn("冷却", self.run_cycle())

    def test_model_fabricates_instrument(self):
        self.model.side_effect = None
        self.model.return_value = ({"summary": "错误", "recommendations": []}, {})
        with self.assertRaises(ValidationError):
            self.run_cycle()
        self.sender.assert_not_called()

    def test_invalid_schedule_rejected(self):
        self.cfg["schedule"] = ["25:30"]
        with self.assertRaises(ValidationError):
            check_config(self.cfg)

    def test_afternoon_report_uses_labelled_previous_completed_day(self):
        self.cfg["schedule"] = ["14:20"]
        self.now = timestamp("2026-11-05T14:20:10+08:00")
        self.assertIn("accepted", self.run_cycle())
        self.assertEqual(self.model.call_args.args[0]["data_date"], "2026-11-04")
        self.assertIn("不动/观望", self.sender.call_args.args[0])
        self.assertIn("行情截至2026-11-04", self.sender.call_args.args[1])

    def test_late_report_is_not_sent(self):
        self.cfg["schedule"] = ["14:20"]
        self.now = timestamp("2026-11-05T15:00:00+08:00")
        self.assertIn("错过", self.run_cycle())
        self.sender.assert_not_called()

    def test_missing_key_stops_before_data_fetch(self):
        with patch("aquant.watch.credentials", return_value={}):
            with self.assertRaises(ValidationError):
                self.run_cycle()
        self.provider.calendar.assert_not_called()

    def cache_provider(self, last_date):
        provider = AKDaily.__new__(AKDaily)
        provider.root = Path(self.temp.name)
        provider.ak = Mock()
        provider.ak.fund_etf_hist_em.return_value = self.frame
        self.frame.to_csv(provider.root / "etf-999999.qfq.csv", index=False)
        meta = {**summarize(self.frame), "symbol": "999999", "adjust": "qfq",
                "last_date": last_date, "fetched_at": datetime.now(SHANGHAI).isoformat(),
                "request_start": "20240101"}
        (provider.root / "etf-999999.qfq.meta.json").write_text(json.dumps(meta))
        return provider

    def test_same_completed_session_cache_avoids_rate_limited_source(self):
        provider = self.cache_provider("2026-11-04")
        frame = provider.history("999999", "etf", "20240101", "2026-11-04")
        provider.ak.fund_etf_hist_em.assert_not_called()
        self.assertTrue(frame.attrs["provenance"]["cache"])

    def test_old_session_cache_is_not_used_for_new_report(self):
        provider = self.cache_provider("2026-11-03")
        frame = provider.history("999999", "etf", "20240101", "2026-11-04")
        provider.ak.fund_etf_hist_em.assert_called_once()
        self.assertFalse(frame.attrs["provenance"]["cache"])

    def test_local_holding_in_email_not_in_deepseek_payload(self):
        from aquant.positions import Positions
        store = Positions(self.temp.name)
        try:
            store.save("159567", "etf", 4500, "0.8723")
        finally:
            store.close()
        self.run_cycle()
        store = Positions(self.temp.name)
        try:
            self.assertEqual(store.all()[0]["name"], "港股创新药ETF银华")
        finally:
            store.close()
        payload = json.dumps(self.model.call_args.args[0])
        self.assertNotIn("0.8723", payload)
        self.assertNotIn("4500", payload)
        email = self.sender.call_args.args[0]
        self.assertIn("4500", email)
        self.assertIn("0.8723", email)
        self.assertIn("3925.35", email)
        self.assertIn("不将前复权研究价格用于持仓盈亏", email)

    def test_cancel_after_model_prevents_email(self):
        stopped = [False]
        original = self.model.side_effect
        def model(*args, **kwargs):
            result = original(*args, **kwargs)
            stopped[0] = True
            return result
        self.model.side_effect = model
        with self.assertRaises(ValidationError):
            self.run_cycle(should_stop=lambda: stopped[0])
        self.sender.assert_not_called()

    def test_processing_past_window_prevents_email(self):
        self.cfg["schedule"] = ["18:30"]
        clock = iter([0, 0, 0, 1000])
        with self.assertRaises(ValidationError):
            self.run_cycle(monotonic=lambda: next(clock))
        self.sender.assert_not_called()

    def test_ctrl_c_before_send_records_retryable_failure(self):
        self.model.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.run_cycle()
        with sqlite3.connect(Path(self.temp.name) / "watch.sqlite") as db:
            self.assertEqual(db.execute("SELECT status FROM jobs").fetchone()[0], "failed")
        self.sender.assert_not_called()

    def test_ctrl_c_during_send_records_unknown(self):
        self.sender.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.run_cycle()
        with sqlite3.connect(Path(self.temp.name) / "watch.sqlite") as db:
            self.assertEqual(db.execute("SELECT status FROM jobs").fetchone()[0], "unknown")

    def test_downtrend_negative_momentum_blocks_model_buy(self):
        from aquant.watch import review_synthesis
        packet = {"stocks": [{"symbol": "etf:159567", "trend": -1, "momentum20": -0.1}]}
        original = {"recommendations": [{"symbol": "etf:159567", "action": "BUY", "reason": "测试"}]}
        result, changes = review_synthesis(packet, original)
        self.assertEqual(result["recommendations"][0]["action"], "HOLD")
        self.assertEqual(original["recommendations"][0]["action"], "BUY")
        self.assertEqual(len(changes), 1)

    def test_data_rate_limit_does_not_call_model_or_email(self):
        from aquant.watch import SourceRateLimited
        self.provider.history.side_effect = SourceRateLimited("行情源疑似限流，暂停下载")
        with self.assertRaises(SourceRateLimited):
            self.run_cycle()
        self.model.assert_not_called()
        self.sender.assert_not_called()
        with sqlite3.connect(Path(self.temp.name) / "watch.sqlite") as db:
            error = db.execute("SELECT error FROM jobs").fetchone()[0]
        self.assertIn("数据采集", error)
        self.assertIn("疑似限流", error)

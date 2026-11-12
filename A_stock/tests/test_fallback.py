import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

try:
    import pandas as pd
except ImportError:
    pd = None

from aquant.domain import SHANGHAI, ValidationError, timestamp
from aquant.watch import AKDaily, cycle


class Push2RateLimitError(Exception):
    pass


@unittest.skipIf(pd is None, "需要可选pandas")
class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.provider = AKDaily.__new__(AKDaily)
        self.provider.root = self.root / "data"
        self.provider.root.mkdir()
        self.provider.ak = Mock()
        self.provider._baostock_history = Mock(side_effect=OSError("offline test fixture"))
        self.frame = pd.DataFrame({"日期": pd.date_range(end="2026-11-10", periods=65).date,
                                   "开盘": [10.] * 65, "收盘": [10.] * 65,
                                   "最高": [11.] * 65, "最低": [9.] * 65, "成交额": [1e7] * 65})
        self.tx = self.frame.rename(columns={"日期": "date", "开盘": "open", "收盘": "close",
                                             "最高": "high", "最低": "low", "成交额": "amount"})
        self.provider.ak.fund_etf_hist_em.side_effect = Push2RateLimitError()
        self.provider.ak.stock_zh_a_hist_tx.return_value = self.tx

    def tearDown(self):
        self.tmp.cleanup()

    def history(self):
        return self.provider.history("159567", "etf", "20240101", "2026-11-10")

    def test_rate_limit_falls_back_and_preserves_source_and_qfq(self):
        frame = self.history()
        self.assertEqual(frame.attrs["provenance"]["source"], "AKShare/Tencent")
        self.assertEqual(frame.attrs["provenance"]["fallback_reason"], "SourceRateLimited")
        self.provider.ak.stock_zh_a_hist_tx.assert_called_once_with(
            symbol="sz159567", start_date="20240101", end_date="20261110", adjust="qfq")
        self.assertTrue((self.provider.root / "eastmoney-cooldown.json").exists())
        saved = json.loads((self.provider.root / "etf-159567.qfq.meta.json").read_text())
        self.assertEqual(saved["source"], "AKShare/Tencent")
        self.assertEqual(saved["last_date"], "2026-11-10")
        cached = self.history()
        self.assertEqual(cached.attrs["provenance"]["source"], "AKShare/Tencent")
        self.assertEqual(cached.attrs["provenance"]["fallback_reason"], "SourceRateLimited")
        self.assertEqual(self.provider.ak.stock_zh_a_hist_tx.call_count, 1)
        self.assertEqual(self.provider.ak.fund_etf_hist_em.call_count, 1)

    def test_existing_cooldown_not_bypassed(self):
        (self.provider.root / "eastmoney-cooldown.json").write_text(json.dumps({
            "retry_after": (datetime.now(SHANGHAI) + timedelta(minutes=40)).isoformat()}))
        self.history()
        self.provider.ak.fund_etf_hist_em.assert_not_called()
        self.provider.ak.stock_zh_a_hist_tx.assert_called_once()

    def test_primary_network_failure_uses_backup(self):
        self.provider.ak.fund_etf_hist_em.side_effect = OSError("network")
        self.assertEqual(self.history().attrs["provenance"]["source"], "AKShare/Tencent")

    def test_primary_stale_data_uses_backup(self):
        self.provider.ak.fund_etf_hist_em.side_effect = None
        self.provider.ak.fund_etf_hist_em.return_value = self.frame.iloc[:-1]
        self.assertEqual(len(self.history()), 65)
        self.provider.ak.stock_zh_a_hist_tx.assert_called_once()

    def test_backup_missing_amount_not_fabricated(self):
        self.provider.ak.stock_zh_a_hist_tx.return_value = self.tx.drop(columns="amount")
        with self.assertRaisesRegex(ValidationError, "历史数据不可用"):
            self.history()
        self.assertFalse((self.provider.root / "etf-159567.qfq.csv").exists())

    def test_stale_backup_does_not_replace_cache(self):
        target = self.provider.root / "etf-159567.qfq.csv"
        target.write_text("original-cache")
        self.provider.ak.stock_zh_a_hist_tx.return_value = self.tx.iloc[:-1]
        with self.assertRaises(ValidationError):
            self.history()
        self.assertEqual(target.read_text(), "original-cache")

    def test_exact_code_reuses_reference_catalog_not_old_prices(self):
        path = self.provider.root / "catalog-etf.json"
        path.write_text(json.dumps({"date": (datetime.now(SHANGHAI).date() - timedelta(days=1)).isoformat(),
                                    "names": {"159567": "港股创新药ETF银华"}}))
        self.assertEqual(self.provider.resolve("159567", "etf"), ("159567", "港股创新药ETF银华"))
        self.provider.ak.fund_name_em.assert_not_called()

    def test_entire_pipeline_can_proceed_using_backup(self):
        self.provider.calendar = Mock(return_value=["2026-11-10", "2026-11-11"])
        self.provider.resolve = Mock(return_value=("159567", "港股创新药ETF银华"))
        cfg = {"state_dir": str(self.root), "instruments": [{"query": "159567", "type": "etf"}],
               "schedule": ["14:00"], "start_date": "20240101", "model": "test"}
        model = Mock(side_effect=lambda p, *a, **kw: ({"summary": "测试", "recommendations": [
            {"symbol": s["symbol"], "action": "HOLD", "reason": "信号不明确", "uncertainty": "缺少盘中行情",
             "evidence_ids": [s["id"]]} for s in p["stocks"]]}, {}))
        sender = Mock()
        values = {k: "test" for k in ("DEEPSEEK_API_KEY", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO")}
        with patch("aquant.watch.credentials", return_value=values):
            result = cycle(cfg, timestamp("2026-11-11T14:00:00+08:00"), provider=self.provider,
                           synthesizer=model, sender=sender)
        self.assertIn("accepted", result)
        self.assertIn("AKShare/Tencent", sender.call_args.args[0])
        self.assertIn("切换备用行情", sender.call_args.args[0])
        self.assertIn("数据获取顺序", sender.call_args.args[0])

    def test_third_provider_works_when_first_two_invalid(self):
        self.provider.ak.stock_zh_a_hist_tx.side_effect = OSError("Tencent unavailable")
        self.provider._baostock_history.side_effect = None
        self.provider._baostock_history.return_value = self.frame
        result = self.history()
        self.assertEqual(result.attrs["provenance"]["source"], "BaoStock")
        self.assertEqual([a["provider"] for a in result.attrs["provenance"]["attempts"]],
                         ["eastmoney", "tencent", "baostock"])

    def test_catalog_failure_does_not_block_known_code_history_routing(self):
        path = self.provider.root / "catalog-etf.json"
        path.write_text(json.dumps({"date": (datetime.now(SHANGHAI).date() - timedelta(days=10)).isoformat(),
                                    "names": {"159567": "港股创新药ETF银华"}}))
        self.provider.ak.fund_name_em.side_effect = Push2RateLimitError()
        code, name = self.provider.resolve("159567", "etf")
        self.assertEqual(code, "159567")
        self.assertIn("名称沿用缓存", name)
        self.assertEqual(self.history().attrs["provenance"]["source"], "AKShare/Tencent")

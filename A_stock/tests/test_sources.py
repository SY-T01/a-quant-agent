import fcntl
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from aquant.domain import ValidationError, timestamp
from aquant.sources import SourcePool, UnsupportedSource, pool_status
from aquant.watch import SourceRateLimited, AKDaily


class SourcePoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = timestamp("2026-11-11T14:00:00+08:00")
        self.pool = SourcePool(self.tmp.name, clock=lambda: self.now)
        self.calls = {n: Mock(return_value=n) for n in ("eastmoney", "tencent", "baostock")}

    def tearDown(self):
        self.pool.close()
        self.tmp.cleanup()

    def fetch(self, scope="etf:159567:qfq"):
        return self.pool.fetch(scope, self.calls, lambda value: (value, {"valid": True}))

    def test_round_robin_success_and_cursor_survives_restart(self):
        self.assertEqual(self.fetch()[2], "eastmoney")
        self.pool.close()
        self.pool = SourcePool(self.tmp.name, clock=lambda: self.now)
        self.assertEqual(self.fetch()[2], "tencent")
        self.assertEqual(self.fetch()[2], "baostock")
        self.assertEqual(self.fetch()[2], "eastmoney")

    def test_failure_skips_to_next_and_does_not_block_third(self):
        self.calls["eastmoney"].side_effect = ValueError("private remote body")
        frame, _, name, trace = self.fetch()
        self.assertEqual(name, "tencent")
        self.assertEqual([t["status"] for t in trace], ["failed", "success"])
        self.assertEqual(self.fetch()[2], "baostock")
        self.assertEqual(self.fetch()[2], "tencent")
        self.calls["eastmoney"].assert_called_once()
        errors = str([tuple(r) for r in self.pool.db.execute("SELECT * FROM health")])
        self.assertNotIn("private remote body", errors)

    def test_two_failures_then_third_source_succeeds(self):
        self.calls["eastmoney"].side_effect = TimeoutError
        self.calls["tencent"].side_effect = ValueError("stale bars")
        _, _, name, trace = self.fetch()
        self.assertEqual(name, "baostock")
        self.assertEqual(len(trace), 3)

    def test_all_fail_once_then_cooling_without_more_requests(self):
        for call in self.calls.values():
            call.side_effect = OSError("network")
        with self.assertRaises(ValidationError):
            self.fetch()
        with self.assertRaises(ValidationError):
            self.fetch()
        self.assertTrue(all(call.call_count == 1 for call in self.calls.values()))

    def test_bad_data_uses_same_failure_path_as_network(self):
        def validate(value):
            if value == "eastmoney":
                raise ValidationError("last date is stale")
            return value, {}
        self.assertEqual(self.pool.fetch("test", self.calls, validate)[2], "tencent")

    def test_rate_cooldown_shared_across_symbols(self):
        until = self.now + timedelta(minutes=40)
        self.calls["eastmoney"].side_effect = SourceRateLimited("limited", until)
        self.fetch()
        state = self.pool.db.execute("SELECT retry_after FROM health WHERE provider='eastmoney'").fetchone()
        self.assertEqual(state[0], until.isoformat())
        self.fetch("etf:510300:qfq")
        self.calls["eastmoney"].assert_called_once()

    def test_expired_cooldown_rejoins_and_resets_failure_count(self):
        self.calls["eastmoney"].side_effect = OSError("temporary")
        self.fetch()
        self.calls["eastmoney"].side_effect = None
        self.now += timedelta(minutes=6)
        self.assertEqual(self.fetch()[2], "baostock")
        self.assertEqual(self.fetch()[2], "eastmoney")
        row = self.pool.db.execute("SELECT failures,retry_after FROM health WHERE provider='eastmoney'").fetchone()
        self.assertEqual(tuple(row), (0, None))

    def test_unsupported_does_not_poison_provider_for_other_symbols(self):
        self.calls["eastmoney"].side_effect = UnsupportedSource("unsupported symbol")
        self.assertEqual(self.fetch()[2], "tencent")
        self.assertIsNone(self.pool.db.execute("SELECT * FROM health WHERE provider='eastmoney'").fetchone())

    def test_download_lock_avoids_concurrent_duplicate_requests(self):
        with (Path(self.tmp.name) / "sources.lock").open("a+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValidationError, "另一项行情下载"):
                self.fetch()
        self.assertTrue(all(not call.called for call in self.calls.values()))

    def test_status_does_not_require_any_network(self):
        self.fetch()
        text = "\n".join(pool_status(self.tmp.name))
        self.assertIn("Eastmoney", text)
        self.assertIn("BaoStock", text)

    def test_user_cancellation_not_swallowed_as_source_failure(self):
        self.calls["eastmoney"].side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.fetch()
        self.calls["tencent"].assert_not_called()

    def test_baostock_adapter_worker_has_bounded_lifetime(self):
        try:
            import pandas
        except ImportError:
            self.skipTest("需要可选pandas")
        provider = AKDaily.__new__(AKDaily)
        result = Mock(returncode=0, stdout=json.dumps({"columns": ["日期", "收盘"], "data": [["2026-11-10", .638]]}))
        with patch("subprocess.run", return_value=result) as worker, patch("importlib.util.find_spec", return_value=Mock()):
            frame = provider._baostock_history("159567", "etf", "20240101", "2026-11-10")
        self.assertEqual(worker.call_args.kwargs["timeout"], 25)
        self.assertEqual(frame.iloc[0]["收盘"], .638)

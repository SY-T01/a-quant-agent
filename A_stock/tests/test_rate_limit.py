import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from aquant.domain import SHANGHAI
from aquant.watch import AKDaily, SourceRateLimited


class Push2RateLimitError(Exception):
    pass


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.provider = AKDaily.__new__(AKDaily)
        self.provider.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_detects_and_persists_cooldown(self):
        source = Mock(side_effect=Push2RateLimitError("remote body should not be stored"))
        with self.assertRaises(SourceRateLimited) as ctx:
            self.provider._source(source)
        self.assertIn("不是DeepSeek或邮箱错误", str(ctx.exception))
        saved = (self.provider.root / "eastmoney-cooldown.json").read_text()
        self.assertNotIn("remote body", saved)
        other = AKDaily.__new__(AKDaily)
        other.root = self.provider.root
        with self.assertRaises(SourceRateLimited):
            other._source(source)
        self.assertEqual(source.call_count, 1)

    def test_expired_cooldown_allows_request(self):
        (self.provider.root / "eastmoney-cooldown.json").write_text(json.dumps({
            "retry_after": (datetime.now(SHANGHAI) - timedelta(minutes=1)).isoformat()}))
        source = Mock(return_value="new-data")
        self.assertEqual(self.provider._source(source), "new-data")
        source.assert_called_once()

    def test_unrelated_error_not_mislabelled_as_rate_limit(self):
        with self.assertRaises(OSError):
            self.provider._source(Mock(side_effect=OSError("network")))
        self.assertFalse((self.provider.root / "eastmoney-cooldown.json").exists())

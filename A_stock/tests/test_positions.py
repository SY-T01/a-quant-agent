import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import Mock

from aquant.domain import ValidationError
from aquant.positions import Positions, local_summary, complete_names


class PositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Positions(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_cost_four_decimals_and_total(self):
        record = self.store.save("159567", "etf", 4500, "0.8723")
        self.assertEqual(self.store.all()[0]["cost"], "0.8723")
        self.assertIn("3925.35", local_summary(record))

    def test_edit_replaces_snapshot_instead_of_adding_shares(self):
        self.store.save("159567", "etf", 4500, "0.8723")
        self.store.save("159567", "etf", 3000, "0.8723")
        self.assertEqual(self.store.all()[0]["shares"], 3000)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM position_history").fetchone()[0], 2)

    def test_invalid_values_do_not_write(self):
        for shares, cost in [(-1, "1"), (1.5, "1"), (True, "1"), (1, "NaN"),
                             (1, "0"), (1, "-1"), (1, "0.12345")]:
            with self.assertRaises(ValidationError):
                self.store.save("159567", "etf", shares, cost)
        self.assertEqual(self.store.all(), [])

    def test_clear_position_keeps_history(self):
        self.store.save("159567", "etf", 4500, "0.8723")
        self.store.save("159567", "etf", 0, "0")
        self.assertEqual(self.store.all()[0]["shares"], 0)

    def test_identified_name_survives_reopen_and_edit_without_extra_holding_history(self):
        self.store.save("000001", "stock", 100, "10.1234")
        self.store.remember_name("000001", "stock", "测试公司")
        self.store.close()
        self.store = Positions(self.tmp.name)
        self.store.save("000001", "stock", 200, "10.5678")
        row = self.store.all()[0]
        self.assertEqual((row["name"], row["shares"], row["cost"]), ("测试公司", 200, "10.5678"))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM position_history").fetchone()[0], 2)

    def test_existing_holdings_backfill_from_catalog_and_keep_name_after_cache_loss(self):
        self.store.save("000001", "stock", 100, "10")
        folder = Path(self.tmp.name) / "data"
        folder.mkdir()
        catalog = folder / "catalog-stock.json"
        catalog.write_text(json.dumps({"names": {"000001": "测试公司"}}))
        self.assertEqual(self.store.all()[0]["name"], "测试公司")
        catalog.write_text('invalid JSON')
        self.assertEqual(self.store.all()[0]["name"], "测试公司")
        catalog.unlink()
        self.assertEqual(self.store.all()[0]["name"], "测试公司")

    def test_old_reference_cannot_replace_a_more_recent_verified_name(self):
        self.store.save("000001", "stock", 100, "10")
        self.store.remember_name("000001", "stock", "新名称", observed_at="2026-11-12T12:00:00+08:00")
        self.store.remember_name("000001", "stock", "旧名称", observed_at="2026-11-11T12:00:00+08:00")
        self.assertEqual(self.store.all()[0]["name"], "新名称")

    def test_stock_and_fund_names_are_separate(self):
        self.store.save("000001", "stock", 100, "10")
        self.store.save("000001", "etf", 100, "1")
        self.store.remember_name("000001", "stock", "测试股票")
        self.store.remember_name("000001", "etf", "测试ETF")
        self.assertEqual({row["kind"]: row["name"] for row in self.store.all()}, {"stock": "测试股票", "etf": "测试ETF"})

    def test_partial_name_update_keeps_holdings_and_reports_failed_lookup(self):
        self.store.save("000001", "stock", 100, "10")
        self.store.save("000002", "stock", 200, "20")
        before = self.store.db.execute("SELECT * FROM positions ORDER BY code").fetchall()
        history = self.store.db.execute("SELECT COUNT(*) FROM position_history").fetchone()[0]
        provider = Mock()
        provider.resolve.side_effect = [("000001", "测试公司"), OSError("offline")]
        message = complete_names(self.tmp.name, provider)
        self.assertIn("已记住 1 个", message)
        self.assertIn("另有 1 个", message)
        self.assertEqual([row["name"] for row in self.store.all()], ["测试公司", ""])
        self.assertEqual(self.store.db.execute("SELECT * FROM positions ORDER BY code").fetchall(), before)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM position_history").fetchone()[0], history)

    def test_mismatched_or_failed_lookup_preserves_verified_name(self):
        self.store.save("000001", "stock", 100, "10")
        self.store.remember_name("000001", "stock", "测试公司")
        provider = Mock()
        for response in [("000002", "其他公司"), ("000001", ""), ("000001", "000001")]:
            provider.resolve.return_value = response
            self.assertIn("暂未取得新名称", complete_names(self.tmp.name, provider))
            self.assertEqual(self.store.all()[0]["name"], "测试公司")

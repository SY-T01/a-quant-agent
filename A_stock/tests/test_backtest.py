import copy
import json
import unittest
from datetime import timedelta
from pathlib import Path

from aquant.backtest import replay_events
from aquant.data import demo_inputs
from aquant.domain import ValidationError


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.account, first, at = demo_inputs()
        self.cfg = json.loads((Path(__file__).resolve().parent.parent / "config/settings.json").read_text())
        second = copy.deepcopy(first)
        second["as_of"] = second["received_at"] = (at + timedelta(minutes=1)).isoformat()
        for row in second["stocks"]:
            row["execution_volume"] = 1_000_000
        self.events = [first, second]

    def test_next_event_only_and_costs(self):
        result = replay_events(self.account, self.events, self.cfg)
        self.assertGreater(result["trade_count"], 0)
        self.assertGreater(result["total_fees"], 0)
        self.assertTrue(all(t["filled_at"] > t["signal_at"] for t in result["trades"]))
        for code, holding in result["final_account"]["holdings"].items():
            if code != "600001.SH":
                self.assertEqual(holding["available"], 0)

    def test_missing_volume_never_fills(self):
        for row in self.events[1]["stocks"]:
            row.pop("execution_volume")
        result = replay_events(self.account, self.events, self.cfg)
        self.assertEqual(result["trade_count"], 0)
        self.assertTrue(result["unfilled"])

    def test_no_same_event_or_unsorted_input(self):
        with self.assertRaises(ValidationError):
            replay_events(self.account, [self.events[0]], self.cfg)
        with self.assertRaises(ValidationError):
            replay_events(self.account, list(reversed(self.events)), self.cfg)

    def test_future_price_jump_cannot_get_old_price(self):
        for row in self.events[1]["stocks"]:
            row["price"] *= 1.01
        result = replay_events(self.account, self.events, self.cfg)
        self.assertFalse(any(t["side"] == "BUY" for t in result["trades"]))


if __name__ == "__main__":
    unittest.main()

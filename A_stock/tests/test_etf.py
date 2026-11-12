import unittest

try:
    import pandas as pd
except ImportError:
    pd = None

from scripts.inspect_etf import summarize


@unittest.skipIf(pd is None, "ETF数据检验需要可选依赖pandas")
class ETFTests(unittest.TestCase):
    def sample(self):
        return pd.DataFrame({"日期": pd.date_range("2025-01-01", periods=65).date,
                             "开盘": [10.] * 65, "收盘": [10.] * 65,
                             "最高": [11.] * 65, "最低": [9.] * 65, "成交额": [1e6] * 65})

    def test_flat_series(self):
        result = summarize(self.sample())
        self.assertEqual(result["ma20"], 10)
        self.assertEqual(result["return20"], 0)
        self.assertEqual(result["max_drawdown_in_sample"], 0)

    def test_bad_data_rejected(self):
        frame = self.sample()
        frame.loc[1, "收盘"] = float("nan")
        with self.assertRaises(ValueError):
            summarize(frame)
        frame = self.sample()
        frame.loc[1, "日期"] = frame.loc[0, "日期"]
        with self.assertRaises(ValueError):
            summarize(frame)

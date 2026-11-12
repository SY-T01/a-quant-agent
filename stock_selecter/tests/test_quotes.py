import tempfile
import unittest
from pathlib import Path
from stock_selector.quotes import normalize,crosscheck,merge_flow,fetch
from stock_selector.storage import Store

T={'code':0,'data':{'sz000001':{'day':[['2026-06-01','10','10.1','10.2','9.9','100']]}}}
S=[dict(day='2026-06-01',open='10',close='10.1',high='10.2',low='9.9',volume='10001')]

class QuoteTests(unittest.TestCase):
    def rows(self): return normalize(T,'tencent','000001.SZ','2026-06-01','2026-06-02')
    def test_volume_units_and_comparison(self):
        a=self.rows(); b=normalize(S,'sina','000001.SZ','2026-06-01','2026-06-02')
        self.assertEqual(a[0]['volume_shares'],10000)
        self.assertEqual(crosscheck(a,b)['matched_days'],1)
        b[0]['close']=10.5
        self.assertEqual(crosscheck(a,b)['mismatches'][0]['fields'],['close'])
    def test_reject_adjusted_only(self):
        with self.assertRaises(ValueError): normalize({'code':0,'data':{'sz000001':{'qfqday':T['data']['sz000001']['day']}}},'tencent','000001.SZ','2026-01-01','2026-12-31')
    def test_invalid_data(self):
        for value in ('nan','0','-1'):
            s=[dict(S[0],open=value)]
            with self.assertRaises(ValueError): normalize(s,'sina','000001.SZ','2026-01-01','2026-12-31')
        with self.assertRaises(ValueError): normalize(S*2,'sina','000001.SZ','2026-01-01','2026-12-31')
    def test_filter_and_no_overlap(self):
        with self.assertRaises(ValueError): normalize(S,'sina','000001.SZ','2026-07-01','2026-12-31')
        self.assertEqual(crosscheck(self.rows(),[])['compared_days'],0)
    def test_persist_and_join_no_fabricated_amount(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp)
            rows,snapshot=fetch('tencent','000001.SZ','2026-06-01','2026-06-02',store,lambda u:T)
            p=Path(tmp)/'flow.csv'; p.write_text('date,symbol,main_net_yuan\n2026-06-01,000001.SZ,100\n')
            joined,q=merge_flow(rows,p,snapshot)
            self.assertEqual(q['overlap_days'],1)
            self.assertNotIn('amount_yuan',joined[0]); self.assertFalse(joined[0]['backtest_ready'])
            self.assertEqual(store.status()['records'],1)

if __name__=='__main__': unittest.main()

import json
import os
import tempfile
import unittest
from pathlib import Path
from stock_selector.ingestion import parse, fetch, run, summarize
from stock_selector.storage import Store, project_path, PROJECT

class IngestionTests(unittest.TestCase):
    def test_parser_units_and_missing(self):
        r=parse({'data':{'klines':['2024-01-02,100,-10,-90,40,60']}},'moneyflow','000001.SZ')[0]
        self.assertEqual(r['main_net_yuan'],100)
        for line in ['2024-01-02,-,0,0,0,0','2024-01-02,nan,0,0,0,0']:
            with self.assertRaises(ValueError): parse({'data':{'klines':[line]}},'moneyflow','000001.SZ')
    def test_snapshot_versioning_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp); rec=[{'date':'2024-01-02','value':1}]
            a=store.save('test','flow','A',{'v':1},rec,'2024-01-03T00:00:00+08:00')
            b=store.save('test','flow','A',{'v':2},rec,'2024-01-04T00:00:00+08:00')
            self.assertNotEqual(a['sha256'],b['sha256'])
            self.assertEqual(json.loads(Path(a['path']).read_text()),{'v':1})
            store.save('test','flow','A',{'v':1},rec,'2024-01-03T00:00:00+08:00')
            self.assertEqual(store.status()['snapshots'],2)
            with self.assertRaises(Exception): store.save('test','flow','B',{},rec*2)
            self.assertEqual(store.status()['snapshots'],2)
    def test_fetch_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows,s=fetch('000001.SZ','moneyflow',Store(tmp),lambda url:{'data':{'klines':['2024-01-02,1,2,3,4,5']}})
            self.assertEqual(len(rows),1); self.assertEqual(s['records'],1)
    def test_empty_or_repeated_response_rejected(self):
        for payload in ({'data':None},{'data':{'klines':['2024-01-02,1,2,3,4,5']*2}}):
            with self.assertRaises(ValueError): parse(payload,'moneyflow','000001.SZ')
    def test_offline_summary_is_descriptive(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp); folder=store.root/'normalized'/'eastmoney'; folder.mkdir(parents=True)
            (folder/'A.csv').write_text('date,symbol,main_net_yuan\n'+''.join(f'2024-01-{i:02},A,10\n' for i in range(1,11)))
            report=summarize(store,Path(tmp)/'reports')
            self.assertFalse(report['backtest_ready'])
            self.assertEqual(report['summaries'][0]['net_5d_yuan'],50)
            self.assertEqual(report['summaries'][0]['positive_fraction_10d'],1)

    def test_paths_independent_of_cwd(self):
        before=Path.cwd()
        try:
            os.chdir('/private/tmp')
            self.assertEqual(project_path('data'),PROJECT/'data')
        finally: os.chdir(before)
    def test_previews_cannot_be_backtested(self):
        from stock_selector.core import load
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'preview.csv'; p.write_text('date,symbol,main_net_yuan\n2024-01-02,000001.SZ,1\n')
            with self.assertRaises(ValueError): load(p)

if __name__=='__main__': unittest.main()

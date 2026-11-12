import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from stock_selector.health import assess,inspect_project
from stock_selector.publication import publish
from stock_selector.weekly import compare

class RoundTwoTests(unittest.TestCase):
    def report(self):
        return dict(asof='2026-11-07T12:00:00+08:00',sectors=[dict(code='BK1',name='样本行业')],data_times={'last':'2026-11-06T15:00:00+08:00'},enrichment_scope={'statements':{'RPT_DMSK_FN_INCOME':100,'RPT_DMSK_FN_CASHFLOW':100},'history_sectors':0},stock_recommendations={'scope':{'quote_pool':5,'quotes_available':5}})
    def test_health_does_not_call_unvalidated_strategy_reliable(self):
        result=assess(self.report())
        self.assertFalse(result['blocked'])
        self.assertIn('策略待验证',result['status'])
        self.assertEqual(next(c['status'] for c in result['checks'] if c['name']=='中期行业历史'),'warning')
    def test_stale_source_blocks_new_recommendations(self):
        report=self.report();report['data_times']['last']='2026-10-01T15:00:00+08:00'
        self.assertTrue(assess(report)['blocked'])
    def test_doctor_detects_corrupt_original_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'data/raw/test.json';raw.parent.mkdir(parents=True);raw.write_text('{}')
            report=self.report();report['snapshots']=[dict(path=str(raw),sha256='incorrect')]
            p=root/'reports/sector_research/research.json';p.parent.mkdir(parents=True);p.write_text(json.dumps(report))
            result=inspect_project(root,report['asof'])
            self.assertTrue(result['health']['blocked'])
            self.assertEqual(result['source_integrity']['verified'],0)
    def stage(self,root,name):
        p=root/name;p.mkdir();(p/'research.json').write_text(json.dumps(self.report()));(p/'index.html').write_text(name);(p/'研究周报.md').write_text(name);return p
    def test_publication_swaps_complete_report_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=self.stage(root,'old');new=self.stage(root,'new');backup=root/'backup'
            publish(new,old,backup)
            self.assertEqual((old/'index.html').read_text(),'new')
            self.assertEqual((backup/'index.html').read_text(),'old')
    def test_failed_publication_restores_original_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=self.stage(root,'old');new=self.stage(root,'new');backup=root/'backup'
            replace=os.replace
            def fail_new(a,b):
                if Path(a)==new:raise OSError('publish interrupted')
                return replace(a,b)
            with patch('os.replace',side_effect=fail_new):
                with self.assertRaises(OSError):publish(new,old,backup)
            self.assertEqual((old/'index.html').read_text(),'old')
    def test_incomplete_report_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=self.stage(root,'old');new=self.stage(root,'new');(new/'研究周报.md').unlink()
            with self.assertRaises(ValueError):publish(new,old,root/'backup')
            self.assertEqual((old/'index.html').read_text(),'old')
    def test_source_time_regression_is_not_new_data(self):
        old=self.report();new=self.report();new['data_times']={'last':'2026-11-05T15:00:00+08:00'}
        diff=compare(old,new)
        self.assertFalse(diff['new_source_data']);self.assertTrue(diff['source_regressed'])
    def test_leftover_lock_file_does_not_block_next_run(self):
        from stock_selector.weekly import acquire_lock
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'weekly.lock';path.write_text('stale pid')
            lock=acquire_lock(path);lock.close()
            lock=acquire_lock(path);lock.close()
    def test_active_lock_blocks_duplicate_job(self):
        from stock_selector.weekly import acquire_lock
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'weekly.lock';lock=acquire_lock(path)
            try:
                with self.assertRaises(RuntimeError):acquire_lock(path)
            finally:lock.close()
    def test_view_launcher_does_not_force_network_update(self):
        path=Path(__file__).resolve().parents[1]/'.launcher/legacy/打开最新报告.command'
        self.assertNotIn('--refresh',path.read_text())
        self.assertNotIn('--enrich',path.read_text())

if __name__=='__main__':unittest.main()

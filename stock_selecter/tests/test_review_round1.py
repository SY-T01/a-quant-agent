import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from stock_selector.storage import atomic_json
from stock_selector.recommendations import pool,select
import test_recommendations

class RoundOneTests(unittest.TestCase):
    def fixture(self):
        source=test_recommendations.RecommendationsTests()
        return source.report(),source.quotes()
    def test_pool_is_order_independent_and_diversified(self):
        r,_=self.fixture();base=r['fundamental_candidates'][0]
        r['fundamental_candidates']=[]
        for industry in ('BK1','BK2','BK3'):
            if industry!='BK1':r['sectors'].append(dict(r['sectors'][0],code=industry))
            for i in range(40):
                r['fundamental_candidates'].append(dict(base,symbol=f'{len(r["fundamental_candidates"])+1:06d}.SZ',industry_code=industry))
        r['research_priorities']=['BK1','BK2','BK3']
        selected=pool(r,limit=9)
        r['fundamental_candidates'].reverse()
        self.assertEqual([s['symbol'] for s in selected],[s['symbol'] for s in pool(r,limit=9)])
        self.assertEqual({k:sum(s['industry_code']==k for s in selected) for k in ('BK1','BK2','BK3')},{'BK1':3,'BK2':3,'BK3':3})
    def test_duplicate_security_conflict_stops_pool(self):
        r,_=self.fixture();r['fundamental_candidates'].append(dict(r['fundamental_candidates'][0],profit_yuan=4e8))
        with self.assertRaises(ValueError):pool(r)
    def test_future_or_missing_financial_dates_block_recommendation(self):
        for changes in ({'report_period':'2026-12-31'},{'notice_date':'2026-11-07'},{'notice_date':''}):
            r,q=self.fixture();r['fundamental_candidates'][0].update(changes)
            self.assertFalse(select(r,q)['stocks'])
    def test_invalid_timezone_is_rejected_not_crash(self):
        r,q=self.fixture();q['000001.SZ']['quote_at']='2026-11-06T15:00:00'
        self.assertFalse(select(r,q)['stocks'])
    def test_score_components_add_up(self):
        r,q=self.fixture();s=select(r,q)['stocks'][0]
        self.assertAlmostEqual(sum(s['score_components'].values()),s['recommendation_score'],delta=.05)
    def test_atomic_cache_preserves_previous_file_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'cache.json';atomic_json(p,{'old':1})
            with patch('os.replace',side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):atomic_json(p,{'new':2})
            self.assertEqual(json.loads(p.read_text()),{'old':1})
            self.assertEqual(list(Path(tmp).glob('*.tmp')),[])
    def test_stricter_cash_scenario_refills_from_fixed_pool(self):
        r,q=self.fixture();r['fundamental_candidates'][0]['cash_profit_ratio']=.9
        self.assertTrue(select(r,q)['stocks'])
        self.assertFalse(select(r,q,policy={'min_cash_cover':1})['stocks'])

if __name__=='__main__':unittest.main()

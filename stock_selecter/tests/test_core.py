import unittest
from dataclasses import replace
from stock_selector.core import Config, ranks, select, features, backtest

def bar(d, **kw):
    return dict(date=d,symbol='A',industry='I',open=10.,close=10.,adj_factor=1.,amount=1e8,moneyflow=1e6,available_at=d+'T20:00:00',listed_on='2020-01-01',is_st=False,suspended=False,limit_up=11.,limit_down=9.,**kw)

class Tests(unittest.TestCase):
    def setUp(self): self.cfg=Config(top_n=1,industry_cap=1,min_listed_days=0)
    def test_ties(self): self.assertEqual(ranks({'a':1,'b':1}),{'a':.5,'b':.5})
    def test_sector_cap_cash(self):
        c=Config(top_n=10,industry_cap=.3)
        self.assertEqual(len(select([dict(industry='I',symbol=str(i)) for i in range(10)],c)),3)
    def test_next_day_and_cost(self):
        rows=[bar('2024-01-02'),bar('2024-01-03')]
        result=backtest(rows,{'2024-01-02':rows[:1]},self.cfg)
        self.assertEqual(result['orders'][0]['date'],'2024-01-03')
        self.assertLess(result['nav'][-1]['nav'],self.cfg.initial_cash)
    def test_limit_and_suspension(self):
        for field,value,status in [('limit_up',10,'limit_up'),('suspended',True,'suspended')]:
            rows=[bar('2024-01-02'),bar('2024-01-03')]; rows[1][field]=value
            r=backtest(rows,{rows[0]['date']:rows[:1]},self.cfg)
            self.assertEqual(r['orders'][0]['status'],status)
            self.assertEqual(r['orders'][0]['quantity'],0)
    def test_sell_down_limit_and_t1(self):
        rows=[bar(f'2024-01-0{i}') for i in (2,3,4)]
        rows[2]['limit_down']=10
        r=backtest(rows,{rows[0]['date']:rows[:1]},replace(self.cfg,rebalance_days=1))
        self.assertEqual([o['side'] for o in r['orders']],['buy','sell'])
        self.assertEqual(r['orders'][-1]['status'],'limit_down')
    def test_no_future_or_late_data(self):
        from datetime import date,timedelta
        rows=[bar(str(date(2024,1,1)+timedelta(days=i))) for i in range(25)]
        base=features(rows,self.cfg)
        changed=[dict(r) for r in rows]; changed[-1]['moneyflow']=9e7
        self.assertEqual(base[rows[21]['date']],features(changed,self.cfg)[rows[21]['date']])
        changed[20]['available_at']='2025-01-01T20:00:00'
        self.assertEqual(features(changed,self.cfg)[rows[20]['date']],[])
    def test_adjustment_preserves_nav(self):
        rows=[bar(f'2024-01-0{i}') for i in (2,3,4)]
        rows[2].update(open=5,close=5,adj_factor=2,limit_up=5.5,limit_down=4.5)
        r=backtest(rows,{rows[0]['date']:rows[:1]},self.cfg)
        self.assertAlmostEqual(r['nav'][1]['nav'],r['nav'][2]['nav'])

if __name__=='__main__': unittest.main()

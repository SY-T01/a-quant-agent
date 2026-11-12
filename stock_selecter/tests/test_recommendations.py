import unittest
from stock_selector.recommendations import select,parse_quotes

class RecommendationsTests(unittest.TestCase):
    def report(self):
        return dict(asof='2026-11-07T12:00:00+08:00',research_priorities=['BK1'],sectors=[dict(code='BK1',flow_state='5/10日均净流入',flow_watch_score=90,net_10d_yuan=1e8,data_time='2026-11-06T15:00:00+08:00')],fundamental_candidates=[dict(symbol='000001.SZ',name='样本',industry_code='BK1',industry_name='工业',cash_profit_ratio=1.2,profit_yuan=2e8,revenue_yuan=2e9,revenue_growth_pct=10,profit_growth_pct=20,growth_score=50,report_period='2026-09-30',notice_date='2026-10-30')])
    def quotes(self):return {'000001.SZ':dict(name='样本',quote_at='2026-11-06T15:00:00+08:00',price=10,amount_yuan=1e8,pe_vendor=20,pb=2)}
    def test_conditions_and_missing_quotes(self):
        self.assertEqual(len(select(self.report(),self.quotes())['stocks']),1)
        self.assertFalse(select(self.report(),{})['stocks'])
    def test_expensive_or_stale_no_recommendation(self):
        for field,value in [('pe_vendor',100),('pb',20),('quote_at','2026-10-01T15:00:00+08:00'),('amount_yuan',0),('name','ST样本')]:
            q=self.quotes();q['000001.SZ'][field]=value
            self.assertFalse(select(self.report(),q)['stocks'])
        r=self.report();r['sectors'][0]['data_time']='2026-10-01T15:00:00+08:00'
        self.assertFalse(select(r,self.quotes())['stocks'])
    def test_concentration_limit(self):
        r=self.report();q=self.quotes()
        for i in range(2,6):
            symbol=f'{i:06d}.SZ';r['fundamental_candidates'].append(dict(r['fundamental_candidates'][0],symbol=symbol));q[symbol]=dict(q['000001.SZ'])
        self.assertEqual(len(select(r,q)['stocks']),2)
    def test_quote_parsing_units(self):
        v=['']*54
        for i,x in {1:'样本',2:'000001',3:'10',30:'20261106150000',37:'10000',39:'20',46:'2'}.items():v[i]=x
        text='v_sz000001="'+'~'.join(v)+'";'
        self.assertEqual(parse_quotes(text)['000001.SZ']['amount_yuan'],1e8)
        with self.assertRaises(ValueError):parse_quotes('invalid')

if __name__=='__main__':unittest.main()

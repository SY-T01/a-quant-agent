import json
import tempfile
import unittest
from pathlib import Path
from stock_selector.enrichment import fiscal_period,history_metrics,financial_candidates,sector_financials,fetch_statements
from stock_selector.research_report import prepare,render_report
from stock_selector.storage import Store
from stock_selector.weekly import compare

class EnrichmentTests(unittest.TestCase):
    def sector(self):
        return dict(code='BK0475',name='银行Ⅱ',evidence_id='sector:BK0475',flow_watch_score=90,flow_state='5/10日均净流入',net_5d_yuan=10.,net_10d_yuan=15.)
    def income(self):
        return {'000001.SZ':dict(INDUSTRY_CODE='475',INDUSTRY_NAME='银行Ⅱ',SECURITY_NAME_ABBR='样本银行',NOTICE_DATE='2026-10-30',REPORT_DATE='2026-09-30',TOTAL_OPERATE_INCOME=1000,PARENT_NETPROFIT=100,TOI_RATIO=10,PARENT_NETPROFIT_RATIO=20)}
    def test_period(self):
        self.assertEqual(fiscal_period('2026-11-07'),'2026-09-30')
        self.assertEqual(fiscal_period('2026-01-01'),'2025-12-31')
    def test_history_returns_align_dates(self):
        from datetime import date,timedelta
        series=[dict(date=str(date(2026,1,1)+timedelta(days=i)),close=100+i,net_yuan=1) for i in range(61)]
        bm=[dict(date=r['date'],close=100) for r in series]
        m=history_metrics(series,bm)
        self.assertEqual(m['net_60d_yuan'],60)
        self.assertAlmostEqual(m['excess_60d'],.6)
        self.assertNotIn('return_120d',m)
        self.assertNotIn('excess_60d',history_metrics(series,[]) )
    def test_finance_dates_and_mapping(self):
        income=self.income();sectors=[self.sector()]
        rows,_=financial_candidates(income,{},sectors,'2026-11-07')
        self.assertEqual(len(rows),1);self.assertIsNone(rows[0]['cash_profit_ratio'])
        self.assertEqual(financial_candidates(income,{},sectors,'2026-10-30')[0],[])
        sectors[0]['name']='另一行业'
        self.assertEqual(financial_candidates(income,{},sectors,'2026-11-07')[0],[])
    def test_cumulative_cashflow_not_imputed(self):
        income=self.income();income['000001.SZ']['INDUSTRY_NAME']='测试工业'
        sector=self.sector();sector['name']='测试工业'
        cash={'000001.SZ':dict(NETCASH_OPERATE=-50,NOTICE_DATE='2026-10-30')}
        rows,_=financial_candidates(income,cash,[sector],'2026-11-07')
        self.assertEqual(rows[0]['cash_profit_ratio'],-.5)
        self.assertEqual(rows[0]['cash_quality_score'],0)
        self.assertIn('待跟踪',rows[0]['classification'])
    def test_sector_growth_breadth(self):
        result=sector_financials(self.income(),[self.sector()],'2026-11-07')
        self.assertEqual(result['BK0475']['growth_breadth'],1)
    def test_partial_statement_pages_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload={'success':True,'result':{'pages':1,'count':2,'data':[dict(SECUCODE='000001.SZ',REPORT_DATE='2026-09-30')]}}
            with self.assertRaises(ValueError): fetch_statements(Store(tmp),'income','2026-09-30',lambda u:payload)
    def test_report_uses_prose_and_keeps_missing_centralized(self):
        report=dict(asof='2026-11-07',data_times={},sectors=[self.sector()],companies=[],limitations=['统一限制'],watchlists={'rows':[]},ai={'result':dict(summary='已披露利润改善。',findings=[dict(claim='近期净流入。',evidence_ids=['sector:BK0475'])],missing_evidence=['跟踪估值'])})
        enrichment={'financial_candidates':[],'sector_financials':{'BK0475':{'disclosed_companies':4,'growth_breadth':.75}}}
        prepare(report,enrichment)
        self.assertEqual(report['sectors'][0]['research_state'],'资金与盈利共振')
        self.assertEqual(report['sectors'][0]['net_change_5d_yuan'],5)
        with tempfile.TemporaryDirectory() as tmp:
            render_report(report,Path(tmp));html=(Path(tmp)/'index.html').read_text()
            self.assertIn('已披露利润改善。',html)
            self.assertNotIn('<pre>',html)
            self.assertNotIn('"findings"',html)
            self.assertNotIn('证据不足',html)
    def test_weekly_same_date_is_not_new_data(self):
        old={'data_times':{'last':'2026-11-06'},'sectors':[self.sector()]}
        self.assertFalse(compare(old,old)['new_source_data'])

if __name__=='__main__':unittest.main()

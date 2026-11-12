import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from stock_selector.context_research import parse_page,publication,pmi_series,credit_point,trend,validate_analysis,retrieve_excerpts,attach
from stock_selector.storage import Store,atomic_json

class ContextTests(unittest.TestCase):
    def test_pmi_table_has_correct_columns_not_other_tables(self):
        rows=''.join(f'<tr><td>2026年{i}月</td><td>{47+i/10}</td><td>51</td><td>49</td><td>48</td><td>47</td><td>50</td></tr>' for i in range(1,5))
        page=parse_page('<table><tr><td>PMI</td><td>生产</td><td>新订单</td></tr>'+rows+'</table><table><tr><td>2026年5月</td><td>999</td></tr></table>')
        result=pmi_series(page)
        self.assertEqual(len(result),4);self.assertEqual(result[-1]['new_orders'],49)
        self.assertEqual(result[-1]['period'],'2026-04')
    def test_date_only_publication_is_conservatively_next_day(self):
        p=parse_page('<meta name="PubDate" content="2026-11-07">')
        with self.assertRaises(ValueError):publication(p,datetime.fromisoformat('2026-11-07T20:00:00+08:00'))
        self.assertIn('2026-11-08',publication(p,datetime.fromisoformat('2026-11-08T01:00:00+08:00'))[1])
    def test_credit_level_not_confused_with_growth(self):
        page=parse_page('<p>2026年9月末社会融资规模存量为468.17万亿元，同比增长7.1%。广义货币（M2）余额359.63万亿元，同比增长7.2%。</p>')
        result=credit_point(page);self.assertEqual(result['m2_yoy'],7.2);self.assertEqual(result['afre_yoy'],7.1)
    def test_trend_does_not_invent_three_month_change(self):
        self.assertIsNone(trend([7.2])['change'])
        self.assertIsNone(trend([7.3,7.2])['change_3m'])
        self.assertEqual(trend([1,2,3,4])['change_3m'],3)
    def test_bm25_prefers_management_risk_over_accounting(self):
        pages=[dict(page=1,text='财务附注 主要风险和报酬的转移 会计政策 金融工具的分类 '+('收入确认 '*60)),dict(page=2,text='公司面临的风险和应对措施 原材料价格波动风险 '+('市场竞争激烈，采购价格波动可能影响公司利润。'*15))]
        result=retrieve_excerpts(pages)
        self.assertEqual(next(e for e in result if e['topic']=='主要风险')['pdf_page'],2)
    def context(self):
        return dict(macro={'sources':[dict(evidence_id='macro:pmi')]},companies=[dict(symbol='000001.SZ',profile={'evidence_id':'profile:000001.SZ'},documents=[dict(evidence_id='disclosure:000001.SZ:1')])])
    def test_outlook_requires_real_company_citation_and_three_cases(self):
        c=self.context();item=dict(symbol='000001.SZ',business_summary='已披露业务',drivers=['订单'],risks=['竞争'],evidence_ids=['disclosure:000001.SZ:1'],scenarios=[dict(case=k,assumptions=['收入兑现'],watch_items=['现金流']) for k in ('optimistic','base','pessimistic')])
        good=validate_analysis({'company_outlooks':[item]},c)
        self.assertEqual(len(good['company_outlooks']),1)
        item['evidence_ids']=['macro:pmi']
        self.assertEqual(validate_analysis({'company_outlooks':[item]},c)['company_outlooks'],[])
    def test_macro_view_requires_official_reference(self):
        c=self.context()
        self.assertIsNotNone(validate_analysis({'macro_review':{'assessment':'需求偏弱','evidence_ids':['macro:pmi']}},c)['macro_review'])
        self.assertIsNone(validate_analysis({'macro_review':{'assessment':'需求偏弱','evidence_ids':['invented']}},c)['macro_review'])
    def test_future_cached_sources_cannot_enter_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp);p=s.root/'normalized/context_research/context.json'
            atomic_json(p,dict(macro={'sources':[{'evidence_id':'macro:pmi','available_at':'2026-12-01T00:00:00+08:00'}], 'indicators':[{'evidence_id':'macro:pmi','code':'pmi','value':51}]},companies=[]))
            report={'asof':'2026-11-07T12:00:00+08:00','stock_recommendations':{'stocks':[]}}
            self.assertEqual(attach(report,s)['macro']['indicators'],[])

if __name__=='__main__':unittest.main()

class ContextSafetyTests(unittest.TestCase):
    def test_unknown_scenario_shape_does_not_break_stock_reviews(self):
        context={'macro':{'sources':[]},'companies':[{'symbol':'000001.SZ','profile':{'evidence_id':'profile:000001.SZ'},'documents':[]}]}
        item={'symbol':'000001.SZ','business_summary':'业务','drivers':[],'risks':[],'evidence_ids':['profile:000001.SZ'],'scenarios':[{'case':[]}]*3}
        result=validate_analysis({'company_outlooks':[item]},context)
        self.assertEqual(result['company_outlooks'],[])
        self.assertTrue(result['issues'])

class ContextIntegrationTests(unittest.TestCase):
    def fixture(self):
        import test_model_review
        t=test_model_review.ReviewTests();report=t.report()
        report['research_context']={'macro':{'indicators':[{'code':'pmi','value':49.3}], 'sources':[{'evidence_id':'macro:pmi','title':'PMI原文'}]},'companies':[{'symbol':'000001.SZ','name':'样本','industry_name':'工业','profile':{'evidence_id':'profile:000001.SZ','business':'制造产品'},'documents':[]}]}
        return t,report
    def test_macro_change_invalidates_previous_model_approval(self):
        from stock_selector.model_review import finalize
        t,r=self.fixture();t.attach(r,t.review(r));self.assertEqual(finalize(r)['counts']['approve_conditional'],1)
        r['research_context']['macro']['indicators'][0]['value']=48
        self.assertEqual(finalize(r)['counts']['pending'],1)
    def test_adapter_passes_context_and_validates_cited_outlooks(self):
        from stock_selector.sector_research import ai_analyze
        t,r=self.fixture()
        def transport(payload,key):
            self.assertEqual(payload['max_tokens'],12288)
            evidence=json.loads(payload['messages'][1]['content'])
            self.assertEqual(evidence['research_context']['macro']['indicators'][0]['value'],49.3)
            outlook=dict(symbol='000001.SZ',business_summary='公司制造产品',drivers=['订单兑现'],risks=['竞争'],evidence_ids=['profile:000001.SZ'],scenarios=[dict(case=k,assumptions=['需求变化'],watch_items=['收入和现金流']) for k in ('optimistic','base','pessimistic')])
            result=dict(summary='宏观与公司复核',findings=[],missing_evidence=[],stock_reviews=[t.review(r)],macro_review={'assessment':'制造业需求需观察','evidence_ids':['macro:pmi']},company_outlooks=[outlook])
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}
        result=ai_analyze(r,key='test',transport=transport)
        self.assertEqual(len(result['context_analysis']['company_outlooks']),1)
        self.assertTrue(result['context_analysis']['macro_review'])


class LargeDisclosureTests(unittest.TestCase):
    def test_oversize_declared_pdf_is_rejected_before_body_read(self):
        from unittest.mock import Mock,patch
        from stock_selector.context_research import fetch_bytes
        response=Mock();response.headers={'Content-Length':'37487272'};response.geturl.return_value='https://static.cninfo.com.cn/test.PDF'
        manager=Mock();manager.__enter__=Mock(return_value=response);manager.__exit__=Mock(return_value=False)
        with patch('stock_selector.context_research.urlopen',return_value=manager):
            with self.assertRaisesRegex(ValueError,'25MB'):fetch_bytes('https://static.cninfo.com.cn/test.PDF')
        response.read.assert_not_called()

    def test_failed_pdf_has_specific_reason_and_link_but_no_body_evidence(self):
        import contextlib,io
        from unittest.mock import patch
        from stock_selector.context_research import refresh_context,evidence_ids
        from stock_selector.context_report import sections
        doc=dict(title='半年度报告',published_at='2026-08-18T00:00:00+08:00',source_url='https://static.cninfo.com.cn/finalpage/2026-08-18/123.PDF',evidence_id='disclosure:000001.SZ:test')
        report={'asof':'2026-11-09T18:00:00+08:00','stock_recommendations':{'stocks':[dict(symbol='000001.SZ',name='样本',industry_name='工业')]}}
        with tempfile.TemporaryDirectory() as directory:
            with patch('stock_selector.context_research.fetch_macro',return_value={'sources':[],'indicators':[]}),patch('stock_selector.context_research.profile',return_value={}),patch('stock_selector.context_research.announcement_list',return_value=[doc]),patch('stock_selector.context_research.download_disclosure',side_effect=ValueError('公开文档超过25MB上限')),patch('stock_selector.context_research.time.sleep'),contextlib.redirect_stdout(io.StringIO()):
                context=refresh_context(report,Store(directory))
        company=context['companies'][0]
        self.assertIn('25MB',company['issues'][0]);self.assertFalse(company['failed_documents'][0]['body_read']);self.assertEqual(company['documents'],[])
        self.assertNotIn(doc['evidence_id'],evidence_ids(context))
        blocks=sections({'research_context':context})
        self.assertEqual(blocks[-1]['sources'][0]['url'],doc['source_url'])

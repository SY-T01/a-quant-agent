import copy
import json
import tempfile
import unittest
from pathlib import Path
import test_recommendations
from stock_selector.recommendations import select
from stock_selector.model_review import candidate_hash,validate_reviews,finalize,html_section,markdown_section
from stock_selector.sector_research import ai_analyze,AIAnalysisError

class ReviewTests(unittest.TestCase):
    def report(self):
        t=test_recommendations.RecommendationsTests();r=t.report();r['stock_recommendations']=select(r,t.quotes());r.update(companies=[],limitations=[],watchlists={'rows':[],'selection':'观察'},model_version='test')
        return r
    def review(self,r,verdict='approve_conditional'):
        c=r['stock_recommendations']['stocks'][0]
        return dict(symbol=c['symbol'],verdict=verdict,reason='盈利与现金流支持初筛。',counter_case='估值口径及周期回落风险需核对。',evidence_ids=[c['evidence_id']],blockers=[] if verdict=='approve_conditional' else ['待核对公告'],buy_plan=dict(entry_condition='公告、交易状态及估值核对通过后',cancel_condition='新公告否定增长逻辑',exit_review_condition='下次财报或行业资金反转时复核',tranches=[dict(fraction=.4,condition='第一项条件达成'),dict(fraction=.6,condition='后续财报验证')] if verdict=='approve_conditional' else []))
    def attach(self,r,review):
        r['ai']=dict(model='test',candidate_hash=candidate_hash(r),result=dict(summary='复核摘要',findings=[],missing_evidence=[],stock_reviews=[review]))
        return r
    def test_no_model_review_means_no_final_buy_list(self):
        p=finalize(self.report());self.assertEqual(p['counts']['pending'],1);self.assertEqual(p['final_buy_candidates'],[])
    def test_approve_defer_and_veto_change_final_plan(self):
        for verdict in ('approve_conditional','defer','reject'):
            r=self.report();self.attach(r,self.review(r,verdict));p=finalize(r)
            self.assertEqual(len(p['final_buy_candidates']),int(verdict=='approve_conditional'))
            self.assertEqual(p['decisions'][0]['verdict'],verdict)
    def test_changed_price_invalidates_old_review(self):
        r=self.report();self.attach(r,self.review(r));r['stock_recommendations']['stocks'][0]['quote']['price']+=1
        self.assertEqual(finalize(r)['counts']['pending'],1)
    def test_changed_sector_evidence_invalidates_old_review(self):
        r=self.report();self.attach(r,self.review(r));r['sectors'][0]['net_10d_yuan']+=100
        self.assertEqual(finalize(r)['counts']['pending'],1)
    def test_unknown_missing_duplicate_reviews_fail(self):
        r=self.report();v=self.review(r);c=r['stock_recommendations']['stocks'];ids={c[0]['evidence_id']}
        for items in ([],[dict(v,symbol='999999.SH')],[v,v]):
            with self.assertRaises(ValueError):validate_reviews({'stock_reviews':items},c,ids)
    def test_unknown_citations_or_approved_blockers_fail(self):
        r=self.report();v=self.review(r);c=r['stock_recommendations']['stocks'];ids={c[0]['evidence_id']}
        for item in (dict(v,evidence_ids=['unknown']),dict(v,blockers=['未解决关键问题'])):
            with self.assertRaises(ValueError):validate_reviews({'stock_reviews':[item]},c,ids)
    def test_fraction_validation_and_reject_has_no_buy_steps(self):
        r=self.report();c=r['stock_recommendations']['stocks'];ids={c[0]['evidence_id']}
        for fraction in (1.5,-.5,float('nan'),True):
            v=self.review(r);v['buy_plan']['tranches']=[dict(fraction=fraction,condition='条件')]
            with self.assertRaises(ValueError):validate_reviews({'stock_reviews':[v]},c,ids)
        v=self.review(r);v['verdict']='reject'
        with self.assertRaises(ValueError):validate_reviews({'stock_reviews':[v]},c,ids)
    def test_ai_adapter_requires_stock_reviews_and_binds_hash(self):
        r=self.report()
        def response(payload,key):
            self.assertEqual(payload['max_tokens'],8192)
            result=dict(summary='复核摘要',findings=[],missing_evidence=[],stock_reviews=[self.review(r)])
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}
        r['ai']=ai_analyze(r,key='test',transport=response)
        self.assertTrue(finalize(r)['review_current'])
        def incomplete(p,k):return {'choices':[{'finish_reason':'stop','message':{'content':'{"summary":"a","findings":[],"missing_evidence":[]}'}}]}
        reply=ai_analyze(r,key='test',transport=incomplete)
        self.assertEqual(reply['result']['summary'],'a')
        self.assertTrue(reply['review_validation_issues'])
    def test_html_and_markdown_include_both_roles_and_plan(self):
        r=self.report();self.attach(r,self.review(r));finalize(r)
        page=html_section(r);md='\n'.join(markdown_section(r))
        self.assertIn('反方论证',page);self.assertIn('40%',page);self.assertIn('60%',page)
        self.assertIn('初筛依据',md);self.assertIn('取消条件',md);self.assertNotIn('"stock_reviews"',page)

class ResponseRecoveryTests(unittest.TestCase):
    report=ReviewTests.report
    review=ReviewTests.review
    def test_fenced_json_is_parsed_and_raw_saved_before_validation(self):
        from stock_selector.sector_research import parse_model_content
        self.assertEqual(parse_model_content('报告如下：\n```json\n{"summary":"说明"}\n```')['summary'],'说明')
        for text in ('', '不是JSON', '{"x":', '[]', '```json\n{}\n```\n```json\n{}\n```'):
            with self.assertRaises(ValueError):parse_model_content(text)
        r=self.report()
        with tempfile.TemporaryDirectory() as directory:
            def response(p,k):return {'choices':[{'finish_reason':'stop','message':{'content':'not json'}}]}
            with self.assertRaises(AIAnalysisError):ai_analyze(r,key='test',transport=response,archive_dir=Path(directory))
            saved=list((Path(directory)/'raw').glob('*.json'))
            self.assertEqual(len(saved),1)
            self.assertEqual(json.loads(saved[0].read_text())['content'],'not json')
    def test_failed_response_is_not_reported_as_not_called(self):
        r=self.report();r['ai']={'error':'正文无法解析','diagnostics':{'http_status':200,'stream_complete':True}}
        p=finalize(r)
        self.assertEqual(p['availability'],'response_unusable')
        self.assertEqual(p['decisions'][0]['label'],'本次返回未能解析')
        self.assertIn('正文无法解析',html_section(r))
    def test_history_is_visible_but_never_becomes_current_approval(self):
        from stock_selector.model_review import attach_history
        r=self.report()
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'old.json').write_text(json.dumps({'candidate_hash':'old','result':{'stock_reviews':[self.review(r)]}}))
            attach_history(r,directory)
        p=finalize(r)
        self.assertEqual(p['final_buy_candidates'],[])
        self.assertIn('历史参考',html_section(r))
        self.assertIn('历史参考','\n'.join(markdown_section(r)))

if __name__=='__main__':unittest.main()

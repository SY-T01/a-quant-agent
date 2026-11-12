import copy
import json
import tempfile
import unittest
from pathlib import Path
import test_model_review
from stock_selector.model_review import finalize,replay_response,candidate_hash
from stock_selector.sector_research import ai_analyze

class PartialReviewTests(unittest.TestCase):
    def fixture(self):
        t=test_model_review.ReviewTests();return t,t.report()
    def attach(self,r,rows):
        r['ai']={'model':'test','candidate_hash':candidate_hash(r),'result':{'stock_reviews':rows}}
        return finalize(r)
    def test_defer_and_reject_do_not_need_a_buy_plan(self):
        for verdict in ('defer','reject'):
            t,r=self.fixture();v=t.review(r,verdict);v.pop('buy_plan')
            p=self.attach(r,[v]);self.assertEqual(p['counts'][verdict],1);self.assertEqual(p['counts']['pending'],0)
    def test_one_broken_plan_keeps_other_valid_stock(self):
        t,r=self.fixture();v=t.review(r)
        second=copy.deepcopy(r['stock_recommendations']['stocks'][0]);second.update(symbol='000002.SZ',name='第二样本',evidence_id='recommendation:000002.SZ');r['stock_recommendations']['stocks'].append(second)
        broken=copy.deepcopy(v);broken.update(symbol=second['symbol'],evidence_ids=[second['evidence_id']]);broken['buy_plan'].pop('exit_review_condition')
        p=self.attach(r,[v,broken])
        self.assertEqual(p['final_buy_candidates'],['000001.SZ'])
        self.assertEqual(p['counts']['plan_incomplete'],1);self.assertEqual(p['counts']['pending'],0)
    def test_common_field_aliases_are_mapped_not_invented(self):
        t,r=self.fixture();v=t.review(r);v['buy_plan']['exit_condition']=v['buy_plan'].pop('exit_review_condition')
        self.assertEqual(self.attach(r,[v])['counts']['approve_conditional'],1)
    def test_invalid_evidence_is_visible_but_not_actionable(self):
        t,r=self.fixture();v=t.review(r);v['evidence_ids']=[{}]
        p=self.attach(r,[v]);self.assertEqual(p['counts']['invalid'],1);self.assertEqual(p['final_buy_candidates'],[])
    def test_response_saved_before_plan_checks_and_can_replay(self):
        t,r=self.fixture();v=t.review(r);v['buy_plan'].pop('cancel_condition')
        response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(dict(summary='模型已审查',findings=[],missing_evidence=[],stock_reviews=[v]))}}]}
        with tempfile.TemporaryDirectory() as tmp:
            a=ai_analyze(r,key='secret-test',transport=lambda p,k:response,archive_dir=Path(tmp))
            files=list(Path(tmp).glob('*.json'));self.assertEqual(len(files),1)
            text=files[0].read_text();self.assertNotIn('secret-test',text)
            r['ai']=replay_response(r,json.loads(text))
            self.assertEqual(finalize(r)['counts']['plan_incomplete'],1)
            self.assertEqual(a['result']['summary'],'模型已审查')
    def test_bad_generic_findings_do_not_erase_stock_opinion(self):
        t,r=self.fixture();v=t.review(r,'reject')
        response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(dict(summary='否决',findings=[None],missing_evidence=[],stock_reviews=[v]))}}]}
        r['ai']=ai_analyze(r,key='test',transport=lambda p,k:response)
        self.assertEqual(finalize(r)['counts']['reject'],1)
        self.assertEqual(r['ai']['diagnostics']['summary_findings_omitted'],1)

if __name__=='__main__':unittest.main()

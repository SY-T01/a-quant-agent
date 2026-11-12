import json
import unittest
import test_model_review
from stock_selector.model_review import finalize,html_section,markdown_section
from stock_selector.scenario_analysis import FIELDS
from stock_selector.sector_research import ai_analyze


class ScenarioTests(unittest.TestCase):
    def test_all_verdicts_receive_research_without_approval(self):
        t=test_model_review.ReviewTests()
        for verdict in ('defer','reject','approve_conditional'):
            r=t.report();t.attach(r,t.review(r,verdict));p=finalize(r)
            analysis=p['decisions'][0]['scenario_analysis']
            self.assertTrue(all(analysis[k] for k in FIELDS))
            self.assertIn('程序条件研究框架',analysis['source'])
            self.assertEqual(bool(p['final_buy_candidates']),verdict=='approve_conditional')
            self.assertIn('假设买入后的盈亏测算',html_section(r))
            self.assertIn('取消投资假设','\n'.join(markdown_section(r)))
            self.assertNotIn('模型未提供此项',html_section(r))
            self.assertEqual([x['pnl_per_10000_yuan'] for x in analysis['sensitivity']],[-2000,-1000,1000,2000])

    def test_model_scenario_is_used_only_when_complete_and_cited(self):
        t=test_model_review.ReviewTests();r=t.report();v=t.review(r,'defer')
        v['scenario_analysis']={k:'公司独立条件 '+k for k in FIELDS}
        v['scenario_analysis']['evidence_ids']=v['evidence_ids']
        t.attach(r,v);d=finalize(r)['decisions'][0]
        self.assertIn('大模型条件情景',d['scenario_analysis']['source'])
        self.assertEqual(d['verdict'],'defer')
        v['scenario_analysis']['evidence_ids']=['unknown'];t.attach(r,v)
        d=finalize(r)['decisions'][0]
        self.assertIn('程序条件研究框架',d['scenario_analysis']['source'])
        self.assertEqual(d['verdict'],'defer')

    def test_prompt_covers_candidate_industry_outside_initial_sectors(self):
        t=test_model_review.ReviewTests();r=t.report();c=r['stock_recommendations']['stocks'][0]
        extra=dict(r['sectors'][0],code='MISSING_IN_TOP15')
        r['sectors']=r['sectors']*20+[extra];c['industry_code']=extra['code']
        def transport(payload,key):
            evidence=json.loads(payload['messages'][1]['content'])
            self.assertIn(extra['code'],[s['code'] for s in evidence['sectors']])
            self.assertIn('scenario_analysis',payload['messages'][0]['content'])
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'stock_reviews':[t.review(r,'defer')]})}}]}
        r['ai']=ai_analyze(r,key='test',transport=transport)
        self.assertEqual(finalize(r)['counts']['defer'],1)

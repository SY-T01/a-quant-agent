import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import test_recommendations
from stock_selector import launcher
from stock_selector.investment_horizon import profile,save as save_horizon
from stock_selector.recommendations import select,pool,update,acquisition_pool
from stock_selector.industry_selection import audit,html_section,supplementary_html
from stock_selector.research_report import prepare
from stock_selector.storage import Store
from stock_selector.publication import publish


class IndustryFirstTests(unittest.TestCase):
    def fixture(self):
        t=test_recommendations.RecommendationsTests();r=t.report();q=t.quotes()
        r['sectors'].append(dict(r['sectors'][0],code='BK2',name='外围行业'))
        other=dict(r['fundamental_candidates'][0],symbol='000002.SZ',name='外围高分公司',industry_code='BK2',industry_name='外围行业',growth_score=100,cash_profit_ratio=2.)
        r['fundamental_candidates'].append(other);q[other['symbol']]=dict(q['000001.SZ'],name=other['name'],pe_vendor=5)
        return r,q

    def test_higher_scoring_outsider_never_fills_primary(self):
        r,q=self.fixture()
        self.assertEqual([c['symbol'] for c in select(r,q)['stocks']],['000001.SZ'])
        self.assertEqual([c['symbol'] for c in select(r,q,scope='supplementary')['stocks']],['000002.SZ'])
        del q['000001.SZ']
        self.assertFalse(select(r,q)['stocks'])
        self.assertTrue(select(r,q,scope='supplementary')['stocks'])

    def test_no_priority_industry_means_no_primary_fallback(self):
        r,q=self.fixture();r['research_priorities']=[]
        self.assertFalse(select(r,q)['stocks']);self.assertFalse(pool(r))

    def test_quote_acquisition_prioritizes_main_industries(self):
        r,q=self.fixture();self.assertEqual(acquisition_pool(r)[0]['industry_code'],'BK1')
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory);path=store.root/'normalized/sector_research/recommendation_quotes.json';path.parent.mkdir(parents=True);path.write_text(json.dumps({'quotes':q}))
            result=update(r,store)
        self.assertEqual([c['symbol'] for c in result['stocks']],['000001.SZ'])
        self.assertEqual([c['symbol'] for c in result['supplementary_stocks']],['000002.SZ'])
        self.assertIn('主名单',html_section(r));self.assertIn('外围高分公司',supplementary_html(r))

    def test_audit_explains_quote_and_base_filter_failures(self):
        r,q=self.fixture();r['research_priorities']=['BK1','BK2'];q={}
        result=select(r,q);rows=audit(r,result)
        self.assertIn('未取得有效行情',rows[0]['explanation'])
        r['fundamental_candidates'][0]['cash_profit_ratio']=.1
        rows=audit(r,select(r,q));self.assertIn('现金流覆盖',rows[0]['explanation'])
        r['health']={'blocked':True};self.assertEqual(audit(r,select(r,q))[0]['status'],'暂停选股')

    def test_no_inferred_parent_child_industry_membership(self):
        r,q=self.fixture();r['research_priorities']=['PARENT']
        self.assertFalse(select(r,q)['stocks'])

    def test_only_covered_industries_become_actionable_priorities(self):
        r,q=self.fixture();r['investment_horizon']=profile(1)
        for s in r['sectors']:s.update(name=s.get('name','有财报行业'),net_5d_yuan=1e8)
        e={'financial_candidates':r['fundamental_candidates'],'sector_financials':{'BK1':{'disclosed_companies':3,'growth_breadth':.5}}}
        prepare(r,e)
        self.assertEqual(r['research_priorities'],['BK1'])
        self.assertEqual(r['market_observation_priorities'],['BK2'])

    def test_publication_stops_scope_violation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);stage=root/'stage';stage.mkdir()
            (stage/'research.json').write_text(json.dumps({'research_priorities':['BK1'],'stock_recommendations':{'selection_policy':'industry-first-v1','stocks':[{'industry_code':'BK2'}]}}))
            with self.assertRaisesRegex(ValueError,'优先行业以外'):publish(stage,root/'current',root/'backup')

    def test_complete_action_dispatches_one_weekly_run_with_key_only_in_child_env(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);report=root/'reports/sector_research/index.html';report.parent.mkdir(parents=True);report.write_text('report');runner=Mock(return_value=Mock(returncode=0))
            with patch.object(launcher,'PROJECT',root),patch.dict('os.environ',{},clear=True),contextlib.redirect_stdout(io.StringIO()) as output:
                launcher.dispatch('full',runner=runner,prompt=lambda _: 'private-test-key')
            self.assertEqual(runner.call_args_list[0].args[0][-1],'stock_selector.weekly')
            self.assertEqual(runner.call_args_list[0].kwargs['env']['DEEPSEEK_API_KEY'],'private-test-key')
            self.assertEqual(runner.call_count,2);self.assertNotIn('env',runner.call_args_list[1].kwargs)
            self.assertNotIn('private-test-key',output.getvalue())

    def test_homepage_explains_settings_mismatch_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'reports/sector_research/research.json';path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'investment_horizon':profile(1),'research_priorities':[]}));save_horizon(12,root/'configs/research_preferences.json')
            self.assertIn('设置已变化','\n'.join(launcher.report_status(root)))

    def test_changing_budget_keeps_existing_style_on_enter(self):
        from stock_selector.capital_allocation import save,load
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'configs/research_preferences.json';save('100000','conservative',path)
            answers=iter(('200000',''));runner=Mock(return_value=Mock(returncode=0))
            with patch.object(launcher,'PROJECT',root),contextlib.redirect_stdout(io.StringIO()):
                launcher.dispatch('capital',runner=runner,input_fn=lambda _:next(answers))
            self.assertEqual(load(path)['style'],'conservative')

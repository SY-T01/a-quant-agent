import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import test_recommendations
import test_model_review
from stock_selector import launcher
from stock_selector.investment_horizon import profile,load,save
from stock_selector.recommendations import select
from stock_selector.model_review import finalize,candidate_hash,replay_response
from stock_selector.sector_research import ai_analyze


class HorizonTests(unittest.TestCase):
    def test_preferences_persist_and_invalid_values_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'preferences.json'
            self.assertEqual(load(path)['months'],6)
            save(12,path);self.assertEqual(load(path)['months'],12)
            for value in (0,25,True,1.5,'3'):
                with self.assertRaises(ValueError):save(value,path)
            self.assertEqual(load(path)['months'],12)

    def test_horizon_changes_gates_and_ranking(self):
        t=test_recommendations.RecommendationsTests();r=t.report();q=t.quotes()
        r['fundamental_candidates'][0].update(cash_profit_ratio=1.2,growth_score=82)
        second=dict(r['fundamental_candidates'][0],symbol='000002.SZ',name='第二样本',cash_profit_ratio=1.8,growth_score=30)
        r['fundamental_candidates'].append(second);q[second['symbol']]=dict(q['000001.SZ'],name=second['name'])
        r['investment_horizon']=profile(1);short=select(r,q)
        r['investment_horizon']=profile(24);long=select(r,q)
        self.assertEqual(short['stocks'][0]['symbol'],'000001.SZ')
        self.assertEqual(long['stocks'][0]['symbol'],'000002.SZ')
        q['000001.SZ']['pe_vendor']=40
        r['investment_horizon']=profile(6)
        self.assertIn('000001.SZ',[c['symbol'] for c in select(r,q)['stocks']])
        r['investment_horizon']=profile(12)
        self.assertNotIn('000001.SZ',[c['symbol'] for c in select(r,q)['stocks']])

    def test_longer_horizon_uses_ten_day_gate_without_requiring_five_day_inflow(self):
        t=test_recommendations.RecommendationsTests();r=t.report();q=t.quotes()
        r['sectors'][0]['flow_state']='资金分歧'
        r['investment_horizon']=profile(1);self.assertFalse(select(r,q)['stocks'])
        r['investment_horizon']=profile(12);self.assertTrue(select(r,q)['stocks'])
        r['sectors'][0]['net_10d_yuan']=-1
        self.assertFalse(select(r,q)['stocks'])

    def test_changing_only_horizon_invalidates_model_approval(self):
        t=test_model_review.ReviewTests();r=t.report();r['investment_horizon']=profile(6)
        t.attach(r,t.review(r));saved=copy.deepcopy(r['ai'])
        self.assertEqual(len(finalize(r)['final_buy_candidates']),1)
        r['investment_horizon']=profile(12)
        self.assertNotEqual(candidate_hash(r),saved['candidate_hash'])
        self.assertFalse(finalize(r)['final_buy_candidates'])
        with self.assertRaises(ValueError):replay_response(r,saved)

    def test_ai_input_and_scenarios_use_selected_horizon(self):
        t=test_model_review.ReviewTests();r=t.report();r['investment_horizon']=profile(12)
        def transport(payload,key):
            self.assertEqual(json.loads(payload['messages'][1]['content'])['investment_horizon']['months'],12)
            self.assertNotIn('3至6个月',payload['messages'][0]['content'])
            self.assertIn('12个月',payload['messages'][0]['content'])
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'stock_reviews':[t.review(r,'defer')]})}}]}
        r['ai']=ai_analyze(r,key='test',transport=transport)
        analysis=finalize(r)['decisions'][0]['scenario_analysis']
        self.assertEqual(analysis['horizon_label'],'12个月')

    def test_menu_saves_horizon_and_regenerates_without_api_or_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);runner=Mock(return_value=Mock(returncode=0));secret_prompt=Mock()
            with patch.object(launcher,'PROJECT',root),contextlib.redirect_stdout(io.StringIO()):
                result=launcher.dispatch('horizon',runner=runner,prompt=secret_prompt,input_fn=lambda _: '12')
            self.assertEqual(result,0);self.assertEqual(load(root/'configs/research_preferences.json')['months'],12)
            self.assertEqual(runner.call_args.args[0][-1],'stock_selector.sector_research')
            secret_prompt.assert_not_called()
            self.assertNotIn('--ai',runner.call_args.args[0]);self.assertNotIn('--refresh',runner.call_args.args[0])

    def test_menu_cancel_or_invalid_input_keeps_preferences(self):
        for answer in ('','abc','0','25'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);runner=Mock();path=root/'configs/research_preferences.json';save(3,path)
                with patch.object(launcher,'PROJECT',root),contextlib.redirect_stdout(io.StringIO()):
                    launcher.dispatch('horizon',runner=runner,input_fn=lambda _:answer)
                self.assertEqual(load(path)['months'],3);runner.assert_not_called()

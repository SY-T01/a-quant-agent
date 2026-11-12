import contextlib
import copy
from decimal import Decimal
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import test_model_review
from stock_selector import launcher
from stock_selector.capital_allocation import settings,save,load,parse_amount,input_hash,validate_portfolio,sections,trading_constraints
from stock_selector.investment_horizon import save as save_horizon,load as load_horizon
from stock_selector.model_review import finalize,candidate_hash,replay_response
from stock_selector.sector_research import ai_analyze


class CapitalTests(unittest.TestCase):
    def report(self,verdict='approve_conditional'):
        t=test_model_review.ReviewTests();r=t.report();r['capital_settings']=settings('100001.01')
        t.attach(r,t.review(r,verdict));r['ai']['allocation_input_hash']=input_hash(r)
        return r

    def portfolio(self,r,stock_weight=.2):
        c=r['stock_recommendations']['stocks'][0]
        return {'cash_weight':1-stock_weight,'allocations':[dict(symbol=c['symbol'],weight=stock_weight,reason='按照现金流与行业风险控制预算',condition='公告与计划条件验证后',evidence_ids=[c['evidence_id']])]}

    def test_settings_preserve_horizon_and_amount(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'preferences.json'
            save('100000','conservative',p)
            self.assertEqual(load_horizon(p)['months'],6)
            save_horizon(12,p)
            self.assertEqual(load(p)['budget_yuan'],'100000.00')
            save('50000','active',p)
            self.assertEqual(load_horizon(p)['months'],12)

    def test_amount_validation_and_chinese_input(self):
        self.assertEqual(parse_amount('10万'),Decimal('100000.00'))
        self.assertEqual(parse_amount('12,345.67'),Decimal('12345.67'))
        for value in ('0','-2','nan','inf','1.234','999999999999999999999','说明'):
            with self.assertRaises(ValueError):parse_amount(value)

    def test_cents_and_tranches_reconcile(self):
        r=self.report();p=finalize(r)
        plan=validate_portfolio(self.portfolio(r),'current',r,p['decisions'],r['capital_settings'])
        row=plan['allocations'][0]
        self.assertEqual(Decimal(row['amount_yuan'])+Decimal(plan['cash_yuan']),Decimal('100001.01'))
        self.assertEqual(sum(Decimal(t['amount_yuan']) for t in row['tranches']),Decimal(row['amount_yuan']))
        self.assertAlmostEqual(plan['stress'][0]['budget_change_pct'],-4.,places=5)

    def test_minimum_order_budget_and_science_board_rule(self):
        r=self.report();r['capital_settings']=settings('5000');p=finalize(r)
        # 100 shares at the fixture reference price of 10 fit a 1000-yuan reservation.
        self.assertEqual(trading_constraints(r)[0]['min_buy_shares'],100)
        validate_portfolio(self.portfolio(r),'current',r,p['decisions'],r['capital_settings'])
        c=r['stock_recommendations']['stocks'][0];c['quote']['price']=20
        with self.assertRaisesRegex(ValueError,'最低申报量'):validate_portfolio(self.portfolio(r),'current',r,p['decisions'],r['capital_settings'])
        c['symbol']='688001.SH'
        self.assertEqual(trading_constraints(r)[0]['min_buy_shares'],200)

    def test_invalid_allocations_do_not_erase_stock_review(self):
        for mutate in ('overspend','single','unknown','duplicate','unknown_citation','nan','boolean'):
            r=self.report();raw=self.portfolio(r)
            if mutate=='overspend':raw['cash_weight']=.9
            if mutate=='single':raw['allocations'][0]['weight']=.3;raw['cash_weight']=.7
            if mutate=='unknown':raw['allocations'][0]['symbol']='999999.SH'
            if mutate=='duplicate':raw['allocations']*=2
            if mutate=='unknown_citation':raw['allocations'][0]['evidence_ids']=['unknown']
            if mutate=='nan':raw['allocations'][0]['weight']=float('nan')
            if mutate=='boolean':raw['allocations'][0]['weight']=True
            r['ai']['result']['portfolio_allocation']={'current':raw,'conditional':self.portfolio(r)}
            p=finalize(r)
            self.assertEqual(p['counts']['approve_conditional'],1)
            self.assertTrue(r['capital_allocation']['issues'])
            self.assertEqual(len(r['capital_allocation']['portfolios']),1)

    def test_industry_cap_and_cash_floor(self):
        r=self.report();c=copy.deepcopy(r['stock_recommendations']['stocks'][0]);c.update(symbol='000002.SZ',evidence_id='recommendation:000002.SZ')
        r['stock_recommendations']['stocks'].append(c)
        t=test_model_review.ReviewTests();v=t.review(r);v2=copy.deepcopy(v);v2.update(symbol=c['symbol'],evidence_ids=[c['evidence_id']])
        r['ai'].update(candidate_hash=candidate_hash(r),result={'stock_reviews':[v,v2]})
        p=finalize(r);raw=self.portfolio(r);raw['allocations'].append(dict(raw['allocations'][0],symbol=c['symbol'],evidence_ids=[c['evidence_id']]));raw['cash_weight']=.6
        with self.assertRaisesRegex(ValueError,'单行业'):validate_portfolio(raw,'current',r,p['decisions'],r['capital_settings'])
        config=dict(r['capital_settings'],min_cash_weight=.9)
        with self.assertRaisesRegex(ValueError,'现金'):validate_portfolio(self.portfolio(r),'current',r,p['decisions'],config)

    def test_defer_has_only_cash_current_and_conditional_budget(self):
        r=self.report('defer');r['ai']['result']['portfolio_allocation']={'current':self.portfolio(r),'conditional':self.portfolio(r)}
        p=finalize(r);portfolios=r['capital_allocation']['portfolios']
        self.assertEqual(p['counts']['defer'],1)
        self.assertFalse(portfolios[0]['allocations']);self.assertEqual(portfolios[0]['cash_yuan'],'100001.01')
        self.assertTrue(portfolios[1]['allocations']);self.assertFalse(portfolios[1]['allocations'][0]['tranches'])
        self.assertIn('不能相加投入','\n'.join(sections(r)))

    def test_rejected_stock_cannot_be_in_conditional_portfolio(self):
        r=self.report('reject');p=finalize(r)
        with self.assertRaises(ValueError):validate_portfolio(self.portfolio(r),'conditional',r,p['decisions'],r['capital_settings'])

    def test_new_budget_invalidates_only_portfolio_not_stock_opinion(self):
        r=self.report();r['ai']['result']['portfolio_allocation']={'current':self.portfolio(r),'conditional':self.portfolio(r)}
        finalize(r);self.assertFalse(r['capital_allocation']['issues'])
        r['capital_settings']=settings('200000')
        self.assertEqual(finalize(r)['counts']['approve_conditional'],1)
        self.assertEqual(r['capital_allocation']['status'],'待大模型分配')
        self.assertFalse(r['capital_allocation']['portfolios'])

    def test_paid_adapter_passes_budget_and_archive_replays_allocation(self):
        r=self.report()
        def transport(payload,key):
            self.assertEqual(json.loads(payload['messages'][1]['content'])['capital_settings']['budget_yuan'],'100001.01')
            self.assertIn('portfolio_allocation',payload['messages'][0]['content'])
            result=copy.deepcopy(r['ai']['result']);result['portfolio_allocation']={'current':self.portfolio(r),'conditional':self.portfolio(r)}
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}
        with tempfile.TemporaryDirectory() as directory:
            r['ai']=ai_analyze(r,key='secret-test',transport=transport,archive_dir=Path(directory))
            archive=json.loads(next(Path(directory).glob('*.json')).read_text())
            self.assertEqual(archive['allocation_input_hash'],input_hash(r))
            r['ai']=replay_response(r,archive);finalize(r)
            self.assertEqual(r['capital_allocation']['status'],'已收到大模型分配')

    def test_menu_saves_without_paid_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);runner=Mock(return_value=Mock(returncode=0));answers=iter(('10万','1'));prompt=Mock()
            with patch.object(launcher,'PROJECT',root),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(launcher.dispatch('capital',runner=runner,prompt=prompt,input_fn=lambda _:next(answers)),0)
            self.assertEqual(load(root/'configs/research_preferences.json')['budget_yuan'],'100000.00')
            self.assertEqual(load(root/'configs/research_preferences.json')['style'],'conservative')
            self.assertNotIn('--ai',runner.call_args.args[0]);prompt.assert_not_called()

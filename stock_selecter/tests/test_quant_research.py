import copy
from datetime import date,timedelta
import json
import tempfile
import unittest
from pathlib import Path
from stock_selector.quant_factors import time_factors,breadth,grouped_ranks,weighted_score
from stock_selector.research_lab import construct
from stock_selector.forward_validation import record,trade_trial,walk_forward_windows,evaluate
from stock_selector.enrichment import fetch_price_history
from stock_selector.storage import Store


def series(count=35,start=date(2026,9,1),trend=.001):
    days=[];day=start
    while len(days)<count:
        if day.weekday()<5:days.append(day)
        day+=timedelta(days=1)
    return [dict(date=str(d),available_at=str(d)+'T20:00:00+08:00',close=10*(1+trend)**i,open=10*(1+trend)**i,
        amount_yuan=1e8,net_yuan=1e6,volume_shares=1e7,adj_factor=1.,limit_up=50.,limit_down=1.,is_st=False,suspended=False,
        buy_min_shares=100,buy_step_shares=100,sell_min_shares=100,sell_step_shares=100,trade_rules_source='SYNTHETIC_TEST_RULES') for i,d in enumerate(days)]


class QuantResearchTests(unittest.TestCase):
    def test_strength_uses_aggregate_turnover_and_correct_prior_window(self):
        rows=series(25);bm=series(25,trend=0);asof=rows[-1]['date']+'T23:00:00+08:00'
        for r in rows[-10:-5]:r.update(net_yuan=10,amount_yuan=100)
        for r in rows[-5:]:r.update(net_yuan=100,amount_yuan=2000)
        f=time_factors(rows,bm,asof,True)
        self.assertAlmostEqual(f['flow_5d'],.05);self.assertAlmostEqual(f['acceleration'],-.05)
        self.assertAlmostEqual(f['flow_10d'],550/10500)
        self.assertGreater(f['excess_20d'],0)

    def test_future_and_not_yet_available_values_cannot_change_current_factors(self):
        rows=series();bm=series(trend=0);asof=rows[24]['date']+'T23:00:00+08:00'
        before=time_factors(rows,bm,asof,True)
        for r in rows[25:]:r.update(close=999999,net_yuan=999999999)
        self.assertEqual(before,time_factors(rows,bm,asof,True))
        rows[24]['available_at']='2027-01-01T00:00:00+08:00'
        self.assertIsNone(time_factors(rows,bm,asof,True)['excess_20d'])

    def test_missing_session_does_not_become_a_twenty_day_return(self):
        rows=series(25);bm=series(25,trend=0);asof=rows[-1]['date']+'T23:00:00+08:00'
        del rows[-11]
        self.assertIsNone(time_factors(rows,bm,asof,True)['excess_20d'])
        self.assertIsNotNone(time_factors(rows,bm,asof,True)['excess_5d'])

    def test_missing_turnover_and_adjustment_are_not_imputed(self):
        rows=series(25);bm=series(25,trend=0);asof=rows[-1]['date']+'T23:00:00+08:00'
        for r in rows:r.pop('amount_yuan')
        f=time_factors(rows,bm,asof,True);self.assertIsNone(f['flow_5d']);self.assertEqual(f['positive_10d'],1)
        rows[0].pop('adj_factor')
        with self.assertRaises(ValueError):time_factors(rows,bm,asof,True,True)

    def test_sample_watchlist_is_not_market_breadth(self):
        rows=series();asof=rows[-1]['date']+'T23:00:00+08:00';bm=series(trend=0)
        membership={'complete':False,'source':'fixture','available_at':rows[0]['available_at'],'members':[{'symbol':'A','valid_from':'2020-01-01'}]}
        self.assertIsNone(breadth(membership,{'A':rows},bm,asof)['above_ma20'])
        membership['complete']=True;membership['members'].append({'symbol':'B','valid_from':'2020-01-01'})
        b=breadth(membership,{'A':rows},bm,asof);self.assertIsNone(b['above_ma20']);self.assertEqual(b['coverage'],.5)
        b=breadth(membership,{'A':rows,'B':rows},bm,asof);self.assertEqual(b['above_ma20'],1.)

    def test_percentiles_do_not_cross_levels_and_unknown_levels_are_unranked(self):
        rows=[{'key':str(i),'level':'L1','x':i} for i in range(3)]
        before=grouped_ranks(rows,'x','level')
        rows += [{'key':'ext'+str(i),'level':'L2','x':999999+i} for i in range(3)]+[{'key':'unknown','level':None,'x':-9999}]
        after=grouped_ranks(rows,'x','level')
        self.assertEqual(before,{key:after[key] for key in before});self.assertNotIn('unknown',after)
        self.assertEqual(grouped_ranks([dict(key=str(i),level='L',x=4) for i in range(3)],'x','level'),{'0':50.,'1':50.,'2':50.})

    def test_missing_component_never_becomes_zero_or_reweighted_score(self):
        self.assertIsNone(weighted_score({'a':100,'b':None},{'a':90,'b':10})['score'])
        self.assertEqual(weighted_score({'a':100,'b':0},{'a':90,'b':10})['score'],90.)

    def test_research_cannot_be_promoted_by_editing_a_flag(self):
        from stock_selector.research_lab import CONFIG
        config=json.loads(CONFIG.read_text());config['production_promotion']=True
        with self.assertRaises(ValueError):construct({'asof':'2026-11-07T23:00:00+08:00'},config=config)
        with self.assertRaises(ValueError):construct({'asof':'2026-11-07T23:00:00+08:00'},config={})

    def test_lab_preserves_baseline_and_exposes_missingness(self):
        report=dict(asof='2026-11-07T23:00:00+08:00',sectors=[dict(code='BK1',name='样本',ratio_5d_pct=2,ratio_10d_pct=1,net_today_yuan=2,amount_today_yuan=100,financials={'disclosed_companies':3,'growth_breadth':.5})],stock_recommendations={'stocks':[]})
        original=copy.deepcopy(report);lab=construct(report)
        self.assertEqual(report,original);self.assertTrue(lab['baseline_unchanged']);self.assertIsNone(lab['industry_factors'][0]['score'])
        self.assertEqual(lab['study_risk']['effective_min_cash_weight'],.5)
        self.assertFalse(lab['validation']['real_out_of_sample_complete'])

    def test_complete_verified_fixture_can_produce_both_layer_scores(self):
        prices=series(25);asof=prices[-1]['date']+'T23:00:00+08:00';meta=dict(verified=True,source='SYNTHETIC_FIXTURE',available_at='2020-01-01T00:00:00+08:00')
        evidence=dict(benchmark=series(25,trend=0),industry_taxonomy={},industry_history={},memberships={},verified_events={},stock_history={},financial_supplements={})
        report=dict(asof=asof,sectors=[],stock_recommendations={'eligible_stocks':[]})
        for i in range(3):
            code='BK'+str(i);symbol=f'{i+1:06d}.SZ';bars=series(25,trend=.001*(i+1))
            for bar in bars:bar['net_yuan']*=i+1
            evidence['industry_taxonomy'][code]=dict(meta,level='verified_fixture_level')
            evidence['industry_history'][code]=dict(meta,series=bars)
            evidence['stock_history'][symbol]=dict(meta,series=bars)
            evidence['memberships'][code]=dict(meta,complete=True,members=[dict(symbol=symbol,valid_from='2020-01-01')])
            evidence['verified_events'][code]=dict(meta,research_score=50,fact='SYNTHETIC_EVENT_FOR_TEST',expires_at='2027-01-01T00:00:00+08:00')
            evidence['financial_supplements'][symbol]=dict(meta,roe=10+i)
            report['sectors'].append(dict(code=code,name=code,financials={'disclosed_companies':3,'growth_breadth':.3+.1*i}))
            report['stock_recommendations']['eligible_stocks'].append(dict(symbol=symbol,name=symbol,industry_code='BK0',cash_profit_ratio=1.2+.1*i,growth_score=40+i,quote={'pe_vendor':10+i}))
        lab=construct(report,evidence=evidence)
        self.assertTrue(all(r['score'] is not None for r in lab['industry_factors']))
        self.assertTrue(all(r['score'] is not None for r in lab['stock_factors']))
        self.assertTrue(all(r['industry_stock_joint_score'] is not None for r in lab['stock_factors']))
        self.assertTrue(lab['baseline_unchanged']);self.assertFalse(lab['validation']['real_out_of_sample_complete'])

    def test_invalid_optional_research_does_not_stop_existing_baseline(self):
        from unittest.mock import patch
        from stock_selector.research_lab import attach
        report=dict(asof='2026-11-07T23:00:00+08:00',stock_recommendations={'stocks':[{'symbol':'KEEP'}]})
        with patch('stock_selector.research_lab.construct',side_effect=ValueError('无效研究输入')):lab=attach(report)
        self.assertEqual(report['stock_recommendations']['stocks'][0]['symbol'],'KEEP')
        self.assertTrue(lab['baseline_unchanged']);self.assertIn('无效研究输入',lab['interpretation'])

    def test_independent_price_parser_checks_dates_and_turnover(self):
        response={'data':{'klines':['2026-11-05,10,11,12,9,1000,1000000,0,0,0,0','2026-11-06,11,12,13,10,2000,2000000,0,0,0,0','2026-11-09,11,12,13,10,2000,2000000,0,0,0,0']}}
        with tempfile.TemporaryDirectory() as directory:
            rows,ref=fetch_price_history(Store(directory),{'code':'BK1','data_time':'2026-11-06T15:00:00+08:00'},transport=lambda _:response)
            self.assertEqual(len(rows),2);self.assertEqual(rows[-1]['amount_yuan'],2000000)
            self.assertTrue(Path(ref['path']).exists())


class ForwardValidationTests(unittest.TestCase):
    def fixture(self):
        rows=series();return rows,dict(id='test',decision_at=rows[0]['date']+'T23:00:00+08:00',budget_yuan='100000'),dict(symbol='000001.SZ',score=70),rows[-1]['date']+'T23:00:00+08:00'

    def test_forward_entry_is_next_session_with_costs_and_t_plus_one(self):
        rows,signal,c,asof=self.fixture();result=trade_trial(signal,c,rows,rows,asof,5)
        self.assertEqual(result['status'],'measured');self.assertEqual(result['entry_day'],rows[1]['date']);self.assertEqual(result['exit_day'],rows[5]['date'])
        self.assertGreater(result['fees_yuan'],0)
        gross=rows[5]['close']/rows[1]['open']-1;self.assertLess(result['net_return'],gross)

    def test_missing_entry_is_not_replaced_with_a_later_better_price(self):
        rows,signal,c,asof=self.fixture();calendar=copy.deepcopy(rows);del rows[1]
        self.assertEqual(trade_trial(signal,c,rows,calendar,asof)['status'],'blocked')

    def test_limits_suspension_and_corporate_actions_do_not_create_fake_fills(self):
        for change in ('limit','halt','action','rules'):
            rows,signal,c,asof=self.fixture()
            if change=='limit':rows[1]['limit_up']=rows[1]['open']
            if change=='halt':rows[5]['suspended']=True
            if change=='action':rows[5]['adj_factor']=2
            if change=='rules':rows[1].pop('buy_min_shares')
            r=trade_trial(signal,c,rows,rows,asof);self.assertEqual(r['status'],'blocked');self.assertIsNone(r['net_return'])

    def test_not_matured_is_not_zero_return(self):
        rows,signal,c,asof=self.fixture();asof=rows[2]['date']+'T23:00:00+08:00'
        r=trade_trial(signal,c,rows,rows,asof);self.assertEqual(r['status'],'pending');self.assertIsNone(r['net_return'])

    def test_actual_market_buy_units_are_not_always_one_hundred(self):
        rows,signal,c,asof=self.fixture()
        for r in rows:r.update(buy_min_shares=200,buy_step_shares=1,sell_min_shares=200,sell_step_shares=1)
        r=trade_trial(signal,c,rows,rows,asof);self.assertEqual(r['status'],'measured');self.assertGreaterEqual(r['quantity'],200)
        self.assertNotEqual(r['quantity']%100,0)

    def test_walk_forward_has_nonoverlapping_tests_and_embargo(self):
        calendar=[str(date(2020,1,1)+timedelta(days=i)) for i in range(350)]
        folds=walk_forward_windows(calendar)
        self.assertGreater(len(folds),1);seen=set()
        for f in folds:
            self.assertLess(f['train'][-1],f['validation'][0]);self.assertLess(f['validation'][-1],f['test'][0])
            self.assertEqual(calendar.index(f['test'][0])-calendar.index(f['validation'][-1])-1,20)
            self.assertFalse(seen & set(f['test']));seen.update(f['test'])

    def test_same_evidence_retry_does_not_create_another_observation(self):
        r=dict(asof='2026-11-07T19:00:00+08:00',sectors=[],stock_recommendations={'stocks':[]},data_times={'last':'2026-11-06T15:00:00+08:00'})
        with tempfile.TemporaryDirectory() as directory:
            a=record(r,directory);r['asof']='2026-11-07T20:00:00+08:00';b=record(r,directory)
            self.assertEqual(a['id'],b['id']);self.assertEqual(a['decision_at'],b['decision_at'])
            result=evaluate(directory,{},r['asof']);self.assertEqual(result['signal_count'],1);self.assertFalse(result['real_strategy_validated'])

    def test_new_research_menus_do_not_request_credentials_or_call_ai(self):
        from unittest.mock import Mock
        from stock_selector.launcher import dispatch
        for action,module in (('lab','stock_selector.research_lab'),('validation','stock_selector.forward_validation')):
            runner=Mock(return_value=Mock(returncode=0));prompt=Mock()
            self.assertEqual(dispatch(action,runner=runner,prompt=prompt),0)
            prompt.assert_not_called();self.assertEqual(runner.call_args_list[0].args[0][-1],module)
            self.assertNotIn('--ai',runner.call_args_list[0].args[0])
            self.assertNotIn('env',runner.call_args_list[0].kwargs)

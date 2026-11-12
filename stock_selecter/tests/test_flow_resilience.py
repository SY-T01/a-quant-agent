import json
import tempfile
import unittest
from http.client import RemoteDisconnected
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from stock_selector.independent_flow import parse_page,fetch,attach,html_section,markdown_section
from stock_selector.storage import Store,atomic_json
from stock_selector.quotes import get_json,DataFetchError,_HOST_BACKOFF
from stock_selector.sector_research import fetch_sectors


def page(code='881001',current=1,pages=1):
    return '<script>broken<table class="m-table J-ajax-table"><tr>'+''.join('<th>'+s+'</th>' for s in ['序号','行业','流入资金(亿)','流出资金(亿)','净额(亿)','公司家数'])+'</tr><tr><td>1</td><td><a href="http://q.10jqka.com.cn/thshy/detail/code/'+code+'/">样本</a></td><td>1.20</td><td>1.00</td><td>0.20</td><td>12</td></tr></table><span class="page_info">'+str(current)+'/'+str(pages)+'</span>'

class IndependentFlowTests(unittest.TestCase):
    def test_fragment_handles_unclosed_script_and_explicit_units(self):
        rows,current,pages=parse_page(page(),'today')
        self.assertEqual((current,pages),(1,1));self.assertEqual(rows[0]['net_yuan'],20000000)
        self.assertIsNone(rows[0]['trade_date']);self.assertEqual(rows[0]['provider'],'ths')
    def test_challenge_missing_field_nonfinite_and_wrong_net_rejected(self):
        for text in ('<html>请验证</html>',page().replace('流入资金(亿)','流入资金(万)'),page().replace('1.20','nan'),page().replace('0.20','0.50')):
            with self.assertRaises(ValueError):parse_page(text,'5d')
    def test_duplicate_page_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('stock_selector.independent_flow.time.sleep'):
                with self.assertRaisesRegex(ValueError,'重复'):
                    fetch(Store(tmp),lambda u:dict(text=page(current=2 if 'page/2/' in u else 1,pages=2),observed_at='2026-11-10T12:00:00+08:00'))
    def test_period_urls_and_equal_universe(self):
        urls=[]
        def transport(url):
            urls.append(url);return dict(text=page(),observed_at='2026-11-10T12:00:00+08:00')
        with tempfile.TemporaryDirectory() as tmp,patch('stock_selector.independent_flow.time.sleep'):
            result=fetch(Store(tmp),transport)
        self.assertEqual(len(result['rows']),3);self.assertEqual(result['industry_count'],1)
        self.assertIn('/board/5/',urls[1]);self.assertIn('/board/10/',urls[2])
    def test_mismatched_period_universes_rejected(self):
        with tempfile.TemporaryDirectory() as tmp,patch('stock_selector.independent_flow.time.sleep'):
            with self.assertRaisesRegex(ValueError,'范围不一致'):
                fetch(Store(tmp),lambda u:dict(text=page(code='881002' if '/board/5/' in u else '881001'),observed_at='2026-11-10T12:00:00+08:00'))
    def test_failed_refresh_preserves_cache_and_does_not_leak_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);cache=Path(tmp)/'normalized/sector_research/ths_observation.json'
            atomic_json(cache,dict(rows=[],notice='cached',observed_at='2026-11-06'))
            before=cache.read_bytes();report={}
            with patch('stock_selector.independent_flow.fetch',side_effect=ValueError('private-query')):
                attach(report,store,refresh=True)
            self.assertEqual(before,cache.read_bytes());self.assertEqual(report['independent_industry_flow']['status'],'cached')
            self.assertNotIn('private-query',json.dumps(report))
    def test_readable_formats_escape_names(self):
        report={'independent_industry_flow':dict(status='observed',rows=[dict(period='today',name='<script>',net_yuan=1e8)],industry_count=1)}
        self.assertIn('&lt;script&gt;',html_section(report));self.assertIn('即时净流入',str(markdown_section(report)))

class ConnectionTests(unittest.TestCase):
    def setUp(self):_HOST_BACKOFF.clear()
    def tearDown(self):_HOST_BACKOFF.clear()
    def test_disconnect_stops_retries_and_same_host_requests(self):
        with patch('stock_selector.quotes.urlopen',side_effect=RemoteDisconnected('private')),patch('stock_selector.quotes.time.sleep') as sleep:
            with self.assertRaises(DataFetchError) as first:get_json('https://push2.eastmoney.com/api/qt/clist/get?token=private')
            self.assertEqual(first.exception.diagnostics['attempts'],1)
            with self.assertRaises(DataFetchError) as second:get_json('https://push2.eastmoney.com/other')
            self.assertEqual(second.exception.diagnostics['attempts'],0);sleep.assert_not_called()
            self.assertNotIn('private',json.dumps(first.exception.diagnostics))
            self.assertNotIn('push2his.eastmoney.com',_HOST_BACKOFF)
    def test_wrapped_disconnect_and_expired_cooldown(self):
        with patch('stock_selector.quotes.urlopen',side_effect=URLError(RemoteDisconnected('private'))):
            with self.assertRaises(DataFetchError):get_json('https://push2.eastmoney.com/api')
        _HOST_BACKOFF['push2.eastmoney.com']=-1
        with patch('stock_selector.quotes.urlopen',side_effect=RemoteDisconnected()) as call:
            with self.assertRaises(DataFetchError):get_json('https://push2.eastmoney.com/api')
            self.assertEqual(call.call_count,1)
    def test_sector_error_contains_page_and_received_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DataFetchError) as caught:
                fetch_sectors(Store(tmp),lambda url:(_ for _ in ()).throw(DataFetchError('断连',dict(attempts=1))))
        self.assertEqual(caught.exception.diagnostics['page'],1);self.assertEqual(caught.exception.diagnostics['received_sectors'],0)
        self.assertIn('第1页',str(caught.exception))

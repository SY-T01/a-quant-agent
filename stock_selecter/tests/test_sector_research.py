import csv
import json
import tempfile
import unittest
from pathlib import Path
from stock_selector.sector_research import rank_sectors,companies,ai_analyze,fetch_sectors,render,COMPANY_FIELDS
from stock_selector.storage import Store


def sector(code='BK1',name='行业'):
    return dict(code=code,name=name,net_today_yuan=1,net_5d_yuan=2,net_10d_yuan=3,ratio_today_pct=1,ratio_5d_pct=2,ratio_10d_pct=3)

class SectorTests(unittest.TestCase):
    def test_missing_and_no_investment_claim(self):
        a=sector(); b=sector('BK2'); b['ratio_5d_pct']=None
        r=rank_sectors([a,b])
        self.assertEqual(r[0]['flow_watch_score'],50)
        self.assertIsNone(r[1]['flow_watch_score'])
        self.assertIn('证据不足',r[0]['investment_status'])
    def test_pagination_and_saved_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp)
            payload={'data':{'total':1,'diff':[{'f12':'BK1','f14':'行业','f62':1,'f164':2,'f174':3,'f184':1,'f165':1,'f175':1}]}}
            rows,refs=fetch_sectors(store,lambda u:payload)
            self.assertEqual(len(rows),1); self.assertEqual(store.status()['records'],1)
            payload['data']['total']=2
            with self.assertRaises(ValueError): fetch_sectors(store,lambda u:payload)
    def test_company_disclosure_and_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'c.csv'
            row=dict(zip(COMPANY_FIELDS,['000001.SZ','样本','BK1','2026-10-01T20:00:00+08:00','https://example.com',.1,.2,.12,1.1,10,.4,.3,.1,'0','1']))
            def write():
                with p.open('w',newline='') as f:
                    w=csv.DictWriter(f,fieldnames=COMPANY_FIELDS);w.writeheader();w.writerow(row)
            write(); valid,_=companies(p,rank_sectors([sector()]),'2026-11-06T20:00:00+08:00')
            self.assertEqual(len(valid),1); self.assertEqual(valid[0]['peer_count'],1)
            row['disclosed_at']='2026-12-01T20:00:00+08:00';write()
            self.assertEqual(companies(p,rank_sectors([sector()]),'2026-11-06T20:00:00+08:00')[0],[])
    def test_ai_citations_and_secret_not_saved(self):
        report=dict(asof='2026-11-06',limitations=[],sectors=rank_sectors([sector()]),companies=[],watchlists={'rows':[],'selection':'观察'})
        def response(payload,key):
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(dict(summary='仅供研究',findings=[dict(claim='资金流入',evidence_ids=['sector:BK1'])],missing_evidence=['盈利']))}}]}
        result=ai_analyze(report,key='secret-test',transport=response)
        self.assertNotIn('secret-test',json.dumps(result))
        report['sectors']=[]
        with self.assertRaises(ValueError): ai_analyze(report,key='secret-test',transport=response)
    def ai_report(self):
        return dict(asof='2026-11-07',limitations=[],sectors=rank_sectors([sector()]),companies=[],watchlists={'rows':[],'selection':'观察'})

    def test_ai_length_has_safe_diagnostics(self):
        from stock_selector.sector_research import AIAnalysisError
        def response(payload,key):
            self.assertEqual(payload['thinking'],{'type':'disabled'})
            self.assertEqual(payload['max_tokens'],4096)
            return {'usage':{'prompt_tokens':10,'completion_tokens':4096,'total_tokens':4106},'choices':[{'finish_reason':'length','message':{'content':'{"summary":','reasoning_content':'private text'}}]}
        with self.assertRaises(AIAnalysisError) as caught: ai_analyze(self.ai_report(),key='secret-test',transport=response)
        self.assertEqual(caught.exception.diagnostics['finish_reason'],'length')
        self.assertEqual(caught.exception.diagnostics['usage']['completion_tokens'],4096)
        self.assertIn('截断',str(caught.exception))
        self.assertNotIn('private text',json.dumps(caught.exception.diagnostics))

    def test_ai_empty_or_malformed_body(self):
        from stock_selector.sector_research import AIAnalysisError
        for content in (None,'','[]','{"summary":"a","findings":[null],"missing_evidence":[]}'):
            def response(payload,key):
                return {'choices':[{'finish_reason':'stop','message':{'content':content}}]}
            with self.assertRaises(AIAnalysisError): ai_analyze(self.ai_report(),key='test',transport=response)
        with self.assertRaises(AIAnalysisError): ai_analyze(self.ai_report(),key='test',transport=lambda p,k:{'choices':[]})

    def test_ai_http_errors_do_not_leak_response(self):
        from urllib.error import HTTPError
        from unittest.mock import patch
        from stock_selector.sector_research import AIAnalysisError
        for status,expected in ((401,'认证失败'),(402,'余额不足'),(429,'过于频繁')):
            with patch('stock_selector.sector_research.urlopen',side_effect=HTTPError('https://api.deepseek.com',status,'secret-test',{},None)):
                with self.assertRaises(AIAnalysisError) as caught: ai_analyze(self.ai_report(),key='secret-test')
                self.assertIn(expected,str(caught.exception))
                self.assertNotIn('secret-test',str(caught.exception))

    def test_ai_network_failure_no_automatic_retry(self):
        from unittest.mock import patch
        from stock_selector.sector_research import AIAnalysisError
        with patch('stock_selector.sector_research.urlopen',side_effect=TimeoutError('secret-test')) as call:
            with self.assertRaises(AIAnalysisError) as caught: ai_analyze(self.ai_report(),key='secret-test')
            self.assertEqual(call.call_count,1)
            self.assertNotIn('secret-test',str(caught.exception))

    def test_server_error_is_explicit_and_never_retried(self):
        from urllib.error import HTTPError
        from unittest.mock import patch
        from stock_selector.sector_research import AIAnalysisError
        for status in (500,502,503,504):
            with patch('stock_selector.sector_research.urlopen',side_effect=HTTPError('https://api.deepseek.com',status,'private response',{},None)) as call:
                with self.assertRaises(AIAnalysisError) as caught:ai_analyze(self.ai_report(),key='secret-test')
                self.assertEqual(call.call_count,1)
                self.assertEqual(caught.exception.diagnostics['automatic_retries'],0)
                self.assertEqual(caught.exception.diagnostics['phase'],'http_error')
                self.assertIn('菜单4',str(caught.exception))
                self.assertNotIn('private response',str(caught.exception));self.assertNotIn('secret-test',str(caught.exception))

    def test_ai_missing_evidence_must_be_strings(self):
        from stock_selector.sector_research import AIAnalysisError
        response={'choices':[{'finish_reason':'stop','message':{'content':'{"summary":"a","findings":[],"missing_evidence":[null]}'}}]}
        with self.assertRaises(AIAnalysisError): ai_analyze(self.ai_report(),key='test',transport=lambda p,k:response)

    def sse_response(self,events,done=True):
        import io
        raw=': keepalive\n\n'+''.join('data: '+json.dumps(e,ensure_ascii=False)+'\n\n' for e in events)
        if done: raw+='data: [DONE]\n\n'
        response=io.BytesIO(raw.encode())
        response.headers={'Content-Type':'text/event-stream; charset=utf-8'}
        response.status=200
        return response

    def test_stream_fragments_usage_and_heartbeats(self):
        from stock_selector.sector_research import read_ai_response
        content='{"summary":"中文证据","findings":[],"missing_evidence":[]}'
        events=[{'choices':[{'delta':{'content':content[:12]},'finish_reason':None}]},
                {'choices':[{'delta':{'content':content[12:]},'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':'stop'}],'usage':{'total_tokens':100}}]
        diagnostics={}; result=read_ai_response(self.sse_response(events),diagnostics)
        self.assertEqual(json.loads(result['choices'][0]['message']['content'])['summary'],'中文证据')
        self.assertEqual(result['usage']['total_tokens'],100)
        self.assertTrue(diagnostics['stream_complete'])

    def test_stream_disconnect_rejected(self):
        from stock_selector.sector_research import read_ai_response
        with self.assertRaises(EOFError):
            read_ai_response(self.sse_response([{'choices':[{'delta':{'content':'partial'}}]}],done=False),{})

    def test_stream_http_path_end_to_end(self):
        from unittest.mock import patch
        events=[{'choices':[{'delta':{'content':'{"summary":"证据不足","findings":[],"missing_evidence":["财务"]}'},'finish_reason':'stop'}],'usage':{'total_tokens':100}}]
        with patch('stock_selector.sector_research.urlopen',return_value=self.sse_response(events)) as call:
            result=ai_analyze(self.ai_report(),key='secret-test')
        self.assertEqual(call.call_count,1)
        self.assertEqual(result['diagnostics']['http_status'],200)
        self.assertTrue(result['diagnostics']['stream_complete'])
        request=call.call_args.args[0]
        self.assertTrue(json.loads(request.data)['stream'])
        self.assertNotIn('secret-test',json.dumps(result))

    def test_transport_errors_are_distinguished(self):
        import socket,ssl
        from urllib.error import URLError
        from unittest.mock import patch
        from stock_selector.sector_research import AIAnalysisError
        cases=[(TimeoutError('secret-test'),'超时'),(URLError(socket.gaierror('secret-test')),'域名'),(ssl.SSLCertVerificationError('secret-test'),'证书')]
        for error,expected in cases:
            with patch('stock_selector.sector_research.urlopen',side_effect=error):
                with self.assertRaises(AIAnalysisError) as caught: ai_analyze(self.ai_report(),key='secret-test')
            self.assertIn(expected,str(caught.exception))
            self.assertIn('error_type',caught.exception.diagnostics)
            self.assertNotIn('secret-test',str(caught.exception))

    def test_non_stream_json_fallback(self):
        import io
        from stock_selector.sector_research import read_ai_response
        response=io.BytesIO(b'{"choices": []}')
        response.headers={'Content-Type':'application/json'}
        self.assertEqual(read_ai_response(response,{}),{'choices':[]})

    def test_html_escapes_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            report=dict(asof='2026-11-06',data_times={},limitations=[],sectors=rank_sectors([sector(name='<script>alert(1)</script>')]),companies=[],company_data_issues=[],watchlists={'rows':[]})
            render(report,Path(tmp)); page=(Path(tmp)/'index.html').read_text()
            self.assertNotIn('<script>alert(1)</script>',page)
            self.assertIn('&lt;script&gt;',page)

if __name__=='__main__': unittest.main()

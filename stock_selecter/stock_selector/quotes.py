"""Independent daily OHLCV sources. Missing turnover is never price * volume."""
import argparse
import csv
import json
import math
import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlencode, urlsplit
from urllib.error import HTTPError, URLError
from http.client import RemoteDisconnected
from urllib.request import Request, urlopen
from .storage import Store, project_path, now

SOURCES = {
    'tencent':'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
    'sina':'https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData',
}

class DataFetchError(ValueError):
    def __init__(self,message,diagnostics):
        super().__init__(message);self.diagnostics=diagnostics

_HOST_BACKOFF={}

def get_json(url):
    target=urlsplit(url);host=target.hostname or ''
    eastmoney=host.endswith('.eastmoney.com') and 'push2' in host
    remaining=_HOST_BACKOFF.get(host,0)-time.monotonic()
    if eastmoney and remaining>0:
        raise DataFetchError('东财连接冷却中，暂不重复请求；请稍后更新',dict(host=host,path=target.path,attempts=0,cooldown_seconds=round(remaining)))
    for attempt in range(3):
        try:
            with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://data.eastmoney.com/' if 'eastmoney.com/' in url else 'https://finance.sina.com.cn/'}),timeout=15) as r:
                return json.load(r)
        except (OSError,ValueError) as e:
            reason=e.reason if isinstance(e,URLError) else e
            status=e.code if isinstance(e,HTTPError) else None
            disconnected=isinstance(reason,RemoteDisconnected)
            details=dict(host=host,path=target.path,attempts=attempt+1,error_type=type(reason).__name__,http_status=status)
            if eastmoney and (disconnected or status==429):
                _HOST_BACKOFF[host]=time.monotonic()+600
                details['cooldown_seconds']=600
                raise DataFetchError('东财服务器断连或限流，已停止重试并冷却10分钟；可能是限流或网络问题，未确认具体根因',details) from None
            if attempt==2 or status is not None and 400<=status<500:
                raise DataFetchError(f'网络或响应异常: {type(reason).__name__}',details) from None
            time.sleep(attempt+1)

def market_code(symbol):
    if not re.fullmatch(r'\d{6}\.(SH|SZ)',symbol): raise ValueError('代码必须形如000001.SZ或600000.SH')
    return symbol[-2:].lower()+symbol[:6]

def normalize(payload,provider,symbol,start,end):
    code=market_code(symbol)
    if provider=='tencent':
        if not isinstance(payload,dict) or payload.get('code')!=0: raise ValueError('腾讯接口失败')
        data=payload.get('data',{}).get(code,{})
        # Only unadjusted day is accepted. Never silently take qfqday instead.
        raw=data.get('day')
        if not raw: raise ValueError('腾讯缺少未复权日线')
        mapped=[]
        for v in raw:
            if len(v)<6: raise ValueError('腾讯字段结构变化')
            mapped.append(dict(date=v[0],open=v[1],close=v[2],high=v[3],low=v[4],volume_shares=float(v[5])*100))
    elif provider=='sina':
        if not isinstance(payload,list) or not payload: raise ValueError('新浪返回空值或非列表')
        mapped=[dict(date=v['day'][:10],open=v['open'],close=v['close'],high=v['high'],low=v['low'],volume_shares=v['volume']) for v in payload]
    else: raise ValueError('未知供应商')
    result=[]; seen=set()
    for v in mapped:
        d=date.fromisoformat(v['date']).isoformat()
        if d in seen: raise ValueError('重复行情日期')
        seen.add(d)
        r=dict(date=d,symbol=symbol,provider=provider,price_basis='unadjusted')
        for k in ('open','close','high','low','volume_shares'):
            r[k]=float(v[k])
            if not math.isfinite(r[k]): raise ValueError('行情非有限值')
        if min(r[k] for k in ('open','close','high','low'))<=0 or r['volume_shares']<0:
            raise ValueError('行情价格或数量非法')
        if r['low']>min(r['open'],r['close'])+1e-8 or r['high']<max(r['open'],r['close'])-1e-8:
            raise ValueError('OHLC价格范围不一致')
        if start<=d<=end: result.append(r)
    if not result: raise ValueError('请求区间无行情')
    return sorted(result,key=lambda r:r['date'])

def fetch(provider,symbol,start,end,store,transport=get_json):
    code=market_code(symbol)
    if provider=='tencent': params={'param':f'{code},day,{start},{end},640,'}
    elif provider=='sina': params=dict(symbol=code,scale=240,ma='no',datalen=1023)
    else: raise ValueError('未知供应商')
    url=SOURCES[provider]+'?'+urlencode(params)
    payload=transport(url)
    rows=normalize(payload,provider,symbol,start,end)
    snapshot=store.save(provider,'daily',symbol,dict(request=dict(url=url,start=start,end=end),response=payload),rows)
    return rows,snapshot

def write_csv(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    fields=sorted(set().union(*(r.keys() for r in rows)))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

def crosscheck(a,b):
    left={r['date']:r for r in a}; right={r['date']:r for r in b}
    shared=sorted(left.keys() & right.keys()); mismatches=[]
    for d in shared:
        fields=[k for k in ('open','close','high','low') if abs(left[d][k]-right[d][k])>.005+1e-8]
        # Tencent reports whole lots, so allow one lot of rounding versus Sina shares.
        if abs(left[d]['volume_shares']-right[d]['volume_shares'])>100: fields.append('volume_shares')
        if fields: mismatches.append(dict(date=d,fields=fields,left=left[d],right=right[d]))
    return dict(compared_days=len(shared),matched_days=len(shared)-len(mismatches),mismatches=mismatches,
                only_primary_dates=sorted(left.keys()-right.keys()),only_secondary_dates=sorted(right.keys()-left.keys()),
                tolerances=dict(price_absolute=.005,volume_shares=100))

def merge_flow(quotes,flow_path,snapshot):
    if not flow_path.exists(): return [],dict(status='missing_moneyflow',overlap_days=0)
    with flow_path.open(encoding='utf-8-sig',newline='') as f:
        flow={r['date']:r for r in csv.DictReader(f) if r.get('main_net_yuan')}
    joined=[]
    for r in quotes:
        if r['date'] not in flow: continue
        f=flow[r['date']]
        if f['symbol']!=r['symbol']: raise ValueError('证券代码不匹配')
        row=dict(r,main_net_yuan=float(f['main_net_yuan']),moneyflow_provider='eastmoney',quote_snapshot_id=snapshot['snapshot_id'],observed_at=snapshot['observed_at'])
        # These two public K-line endpoints do not return daily traded amount.
        # Do not estimate it from OHLC or multiply closing price by volume.
        row['amount_status']='missing'; row['backtest_ready']=False
        joined.append(row)
    return joined,dict(status='preview_only',overlap_days=len(joined),moneyflow_only_dates=sorted(set(flow)-{r['date'] for r in quotes}))

def run(symbols,start,end,store,out):
    if date.fromisoformat(start)>date.fromisoformat(end): raise ValueError('开始日期晚于结束日期')
    out.mkdir(parents=True,exist_ok=True)
    report=dict(generated_at=now(),requested_start=start,requested_end=end,storage_root=str(store.root),symbols=[],backtest_ready=False,
                blockers=['当前两个直接日线接口均无日成交额，不计算F5等资金强度','缺少完整历史行业、证券状态、复权因子和涨跌停价','观察时间不代表历史首次公开时间'],status='ok')
    for symbol in symbols:
        item=dict(symbol=symbol,sources={},errors=[]); datasets={}; snapshots={}
        for provider in ('tencent','sina'):
            try:
                rows,snapshot=fetch(provider,symbol,start,end,store)
                dest=store.root/'normalized'/provider/f'{symbol}.csv'; write_csv(dest,rows)
                datasets[provider]=rows; snapshots[provider]=snapshot
                item['sources'][provider]=dict(snapshot=snapshot,start=rows[0]['date'],end=rows[-1]['date'],days=len(rows),normalized=str(dest),amount_available=False)
            except (ValueError,KeyError,TypeError,IndexError) as e:
                item['errors'].append(dict(provider=provider,error=str(e)))
            time.sleep(.3)
        if len(datasets)==2: item['crosscheck']=crosscheck(datasets['tencent'],datasets['sina'])
        if datasets:
            provider='tencent' if 'tencent' in datasets else 'sina'
            joined,quality=merge_flow(datasets[provider],store.root/'normalized'/'eastmoney'/f'{symbol}.csv',snapshots[provider])
            item['join']=quality; item['primary_quote_provider']=provider
            if joined:
                dest=store.root/'normalized'/'joined'/f'{symbol}.csv'; write_csv(dest,joined); item['joined_preview']=str(dest)
        else: item['join']=dict(status='no_quotes',overlap_days=0)
        if item['errors'] or item.get('crosscheck',{}).get('mismatches'): report['status']='needs_review'
        report['symbols'].append(item)
    report['storage']=store.status()
    (out/'quote_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

def main():
    today=datetime.fromisoformat(now()).date()
    p=argparse.ArgumentParser(description='腾讯主源及新浪交叉验证（不生成正式回测数据）')
    p.add_argument('--symbols',nargs='+',default=['000001.SZ','600000.SH'])
    p.add_argument('--start',default='2026-06-01'); p.add_argument('--end',default=str(today-timedelta(days=1)))
    p.add_argument('--root',default='data'); p.add_argument('--out',default='reports/quotes')
    a=p.parse_args()
    if date.fromisoformat(a.end)>=today: raise ValueError('只接受昨日及更早日期，排除未收盘行情')
    result=run(a.symbols,a.start,a.end,Store(a.root),project_path(a.out))
    print(json.dumps(dict(status=result['status'],report=str(project_path(a.out)/'quote_report.json'),symbols=[dict(symbol=i['symbol'],sources=list(i['sources']),join=i['join']) for i in result['symbols']]),ensure_ascii=False))
    if result['status']!='ok': raise SystemExit(2)

if __name__=='__main__': main()

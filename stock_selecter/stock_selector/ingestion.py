"""Account-free Eastmoney reconnaissance. Never fabricates historical availability."""
import argparse
import csv
import json
import math
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from .storage import Store, project_path, now

BASE='https://push2his.eastmoney.com/api/qt/stock/'

def request_json(url):
    for attempt in range(3):
        try:
            req=Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://data.eastmoney.com/'})
            with urlopen(req,timeout=15) as response: return json.load(response)
        except (OSError,ValueError):
            if attempt==2: raise RuntimeError('公开数据接口不可达或响应无效；未生成替代数据') from None
            time.sleep(attempt+1)

def parse(payload, kind, symbol):
    data=payload.get('data')
    if not isinstance(data,dict) or not data.get('klines'): raise ValueError('接口未返回有效历史数据')
    records=[]; seen=set()
    for line in data['klines']:
        v=line.split(','); d=date.fromisoformat(v[0]).isoformat()
        if d in seen: raise ValueError('接口历史日期重复')
        seen.add(d)
        if kind=='moneyflow':
            if len(v)<6: raise ValueError('资金接口字段结构变化')
            r=dict(date=d,symbol=symbol,main_net_yuan=float(v[1]),small_net_yuan=float(v[2]),medium_net_yuan=float(v[3]),large_net_yuan=float(v[4]),extra_large_net_yuan=float(v[5]))
        else:
            if len(v)<7: raise ValueError('行情接口字段结构变化')
            r=dict(date=d,symbol=symbol,open=float(v[1]),close=float(v[2]),high=float(v[3]),low=float(v[4]),volume_lots=float(v[5]),amount_yuan=float(v[6]))
            if min(r[k] for k in ('open','close','high','low'))<=0 or r['amount_yuan']<0: raise ValueError('行情数值非法')
        if any(isinstance(x,float) and not math.isfinite(x) for x in r.values()): raise ValueError('缺失或非有限值')
        records.append(r)
    return sorted(records,key=lambda r:r['date'])

def fetch(symbol,kind,store,transport=request_json):
    if not re.fullmatch(r'\d{6}\.(SH|SZ)',symbol): raise ValueError('只接受000001.SZ或600000.SH格式')
    secid=('1.' if symbol.endswith('.SH') else '0.')+symbol[:6]
    if kind=='moneyflow':
        endpoint='fflow/daykline/get'; params=dict(secid=secid,klt=101,lmt=100,fields1='f1,f2,f3,f7',fields2='f51,f52,f53,f54,f55,f56')
    else:
        endpoint='kline/get'; params=dict(secid=secid,klt=101,fqt=0,lmt=150,beg='0',end='20500101',fields1='f1,f2,f3,f4,f5,f6',fields2='f51,f52,f53,f54,f55,f56,f57')
    payload=transport(BASE+endpoint+'?'+urlencode(params))
    records=parse(payload,kind,symbol)
    saved=store.save('eastmoney',kind,symbol,payload,records)
    return records,saved

def run(symbols,store,out):
    out.mkdir(parents=True,exist_ok=True)
    report=dict(observed_at=now(),storage_root=str(store.root),status='ok',symbols=[],backtest_ready=False,
                blocking=['缺少PIT历史行业、ST/停牌、上市退市主表、复权因子和准确涨跌停价','抓取日期不等于历史公开日期，禁止将今日抓取回填为历史已知数据'],source='https://akshare.akfamily.xyz/data/stock/stock_flow.html')
    for symbol in symbols:
        item=dict(symbol=symbol,snapshots=[],errors=[]); rows={}
        for kind in ('moneyflow','daily'):
            try:
                records,saved=fetch(symbol,kind,store); item['snapshots'].append(saved)
                for r in records: rows.setdefault(r['date'],dict(date=r['date'],symbol=symbol)).update(r)
            except (ValueError,RuntimeError) as e: item['errors'].append(str(e))
            time.sleep(.3)
        item['coverage']={kind:dict(start=min((r['date'] for r in rows.values() if ('main_net_yuan' if kind=='moneyflow' else 'amount_yuan') in r),default=None),end=max((r['date'] for r in rows.values() if ('main_net_yuan' if kind=='moneyflow' else 'amount_yuan') in r),default=None)) for kind in ('moneyflow','daily')}
        item['overlap_days']=sum('main_net_yuan' in r and 'amount_yuan' in r for r in rows.values())
        # Preview is deliberately outside the strategy schema, so it cannot enter backtests.
        for r in rows.values():
            if 'main_net_yuan' in r and r.get('amount_yuan',0)>0: r['flow_ratio']=r['main_net_yuan']/r['amount_yuan']
        if rows:
            dest=store.root/'normalized'/'eastmoney'/f'{symbol}.csv'; dest.parent.mkdir(parents=True,exist_ok=True)
            keys=sorted(set().union(*(r.keys() for r in rows.values())))
            with dest.open('w',encoding='utf-8-sig',newline='') as f:
                w=csv.DictWriter(f,fieldnames=keys); w.writeheader(); w.writerows(rows[d] for d in sorted(rows))
            item['preview']=str(dest)
        if item['errors']: report['status']='partial_or_failed'
        report['symbols'].append(item)
    report['storage']=store.status()
    (out/'ingestion_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

def summarize(store,out):
    summaries=[]
    for path in sorted((store.root/'normalized'/'eastmoney').glob('*.csv')):
        with path.open(encoding='utf-8-sig',newline='') as f:
            rows=sorted([r for r in csv.DictReader(f) if r.get('main_net_yuan')],key=lambda r:r['date'])
        if not rows: continue
        values=[float(r['main_net_yuan']) for r in rows]
        item=dict(symbol=rows[-1]['symbol'],start=rows[0]['date'],end=rows[-1]['date'],records=len(rows))
        for n in (5,10,20):
            if len(values)>=n:
                item[f'net_{n}d_yuan']=sum(values[-n:])
                item[f'positive_fraction_{n}d']=sum(v>0 for v in values[-n:])/n
        summaries.append(item)
    out.mkdir(parents=True,exist_ok=True)
    result=dict(kind='资金净额描述统计，不是选股榜单',generated_at=now(),backtest_ready=False,summaries=summaries,
                limitations=['未除以成交额，绝对净额不可跨股票直接比较','只有两只指定样本，不代表全市场','无历史首次公开时间，仅记录本次观察时间'])
    (out/'flow_summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

def main():
    p=argparse.ArgumentParser(description='免费公开数据验证；不会直接产生回测数据')
    p.add_argument('command',choices=['fetch','status','summary']); p.add_argument('--symbols',nargs='+',default=['000001.SZ','600000.SH'])
    p.add_argument('--root',default='data'); p.add_argument('--out',default='reports/ingestion')
    a=p.parse_args(); store=Store(a.root)
    if a.command=='summary':
        summarize(store,project_path(a.out)); print(str(project_path(a.out)/'flow_summary.json')); return
    if a.command=='status': print(json.dumps(store.status(),ensure_ascii=False,indent=2)); return
    report=run(a.symbols,store,project_path(a.out))
    print(json.dumps({'status':report['status'],'root':str(store.root),'report':str(project_path(a.out)/'ingestion_report.json')},ensure_ascii=False))
    if report['status']!='ok': raise SystemExit(2)

if __name__=='__main__': main()

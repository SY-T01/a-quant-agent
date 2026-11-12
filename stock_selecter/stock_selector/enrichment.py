"""Public financial statements and industry flow history; no credentials required."""
import json
import time
from datetime import date,timedelta
from urllib.parse import urlencode
from .storage import now,atomic_json
from .quotes import get_json

FINANCE_URL='https://datacenter-web.eastmoney.com/api/data/v1/get'

def fiscal_period(asof):
    d=date.fromisoformat(asof[:10])
    return str(date(d.year,((d.month-1)//3)*3+1,1)-timedelta(days=1))

def finite(value):
    import math
    if value in (None,'','-'): return None
    v=float(value)
    if not math.isfinite(v): raise ValueError('非有限数据')
    return v

def fetch_statements(store,report,period,transport=get_json):
    all_rows={}; refs=[]; expected=None; pages=None
    for page in range(1,101):
        params=dict(reportName=report,columns='ALL',pageSize=500,pageNumber=page,sortColumns='SECURITY_CODE',sortTypes='1',filter=f"(REPORT_DATE='{period}')")
        url=FINANCE_URL+'?'+urlencode(params)
        payload=transport(url); result=payload.get('result')
        if not payload.get('success') or not isinstance(result,dict) or not result.get('data'):
            raise ValueError('财报接口返回空值或错误')
        if expected is None: expected=int(result['count']); pages=int(result['pages'])
        if expected!=int(result['count']) or pages!=int(result['pages']): raise ValueError('财报分页总数变化')
        if pages>100: raise ValueError('财报页数超过安全上限')
        for r in result['data']:
            key=r['SECUCODE']
            if key in all_rows: raise ValueError('财报分页出现重复公司')
            if r.get('REPORT_DATE','')[:10]!=period: raise ValueError('财报报告期不一致')
            all_rows[key]=r
        refs.append(store.save('eastmoney',report,f'page_{page}',dict(url=url,response=payload),[dict(date=period,rows=result['data'])]))
        if page>=pages: break
        time.sleep(.2)
    if len(all_rows)!=expected: raise ValueError('财报分页不完整')
    return all_rows,refs

def fetch_history(store,sector,transport=get_json):
    params=dict(secid='90.'+sector['code'],klt=101,lmt=120,fields1='f1,f2,f3,f7',fields2=','.join('f'+str(i) for i in range(51,64)))
    url='https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?'+urlencode(params)
    payload=transport(url); raw=(payload.get('data') or {}).get('klines')
    if not raw: raise ValueError('行业历史序列为空')
    rows=[]; seen=set()
    for line in raw:
        v=line.split(',')
        if len(v)<13: raise ValueError('行业历史字段改变')
        d=str(date.fromisoformat(v[0]))
        if d in seen: raise ValueError('行业历史日期重复')
        seen.add(d)
        r=dict(date=d,net_yuan=finite(v[1]),main_ratio_pct=finite(v[6]),close=finite(v[11]))
        if r['close'] is None or r['close']<=0 or r['net_yuan'] is None: raise ValueError('行业价格或资金缺失')
        rows.append(r)
    rows.sort(key=lambda r:r['date'])
    ref=store.save('eastmoney','sector_history',sector['code'],dict(url=url,response=payload),rows)
    # Tie history to the sector snapshot date; newer rows cannot leak into an older snapshot.
    cutoff=(sector.get('data_time') or sector['observed_at'])[:10]
    return [r for r in rows if r['date']<=cutoff],ref

def fetch_benchmark(store,transport=get_json):
    url='https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'+urlencode({'param':'sh000300,day,,,320,'})
    payload=transport(url); raw=((payload.get('data') or {}).get('sh000300') or {}).get('day')
    if not raw: raise ValueError('沪深300基准序列为空')
    observed=now()
    rows=[dict(date=v[0],open=finite(v[1]),close=finite(v[2]),available_at=observed,availability_kind='本次实际观察时间，非历史首次披露') for v in raw]
    if any(r['close'] is None or r['close']<=0 for r in rows): raise ValueError('基准价格无效')
    ref=store.save('tencent','benchmark','000300.SH',dict(url=url,response=payload),rows)
    return rows,ref

def fetch_price_history(store,sector,transport=get_json):
    """Independent index OHLC/turnover path; failure of money-flow API need not hide RS."""
    params=dict(secid='90.'+sector['code'],klt=101,fqt=0,beg='20200101',end=(sector.get('data_time') or now())[:10].replace('-',''),lmt=320,
                fields1='f1,f2,f3,f4,f5,f6',fields2='f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61')
    url='https://push2his.eastmoney.com/api/qt/stock/kline/get?'+urlencode(params)
    payload=transport(url);raw=(payload.get('data') or {}).get('klines')
    if not raw:raise ValueError('行业价格成交额序列为空')
    rows=[];seen=set();cutoff=(sector.get('data_time') or now())[:10]
    for line in raw:
        v=line.split(',')
        if len(v)<7:raise ValueError('行业日线字段改变')
        day=date.fromisoformat(v[0]).isoformat()
        if day in seen:raise ValueError('行业日线日期重复')
        seen.add(day)
        values=[finite(x) for x in v[1:7]]
        if any(x is None for x in values) or min(values[:4])<=0 or values[4]<0 or values[5]<0:raise ValueError('行业日线价格或成交额非法')
        op,cl,high,low,volume,amount=values
        if high<max(op,cl) or low>min(op,cl):raise ValueError('行业日线高低价不一致')
        if day<=cutoff:rows.append(dict(date=day,open=op,close=cl,high=high,low=low,volume_shares=volume*100,amount_yuan=amount,price_basis='unadjusted_index'))
    if not rows:raise ValueError('行业日线没有适用于快照时点的记录')
    rows.sort(key=lambda x:x['date'])
    ref=store.save('eastmoney','sector_price_history',sector['code'],dict(url=url,response=payload),rows)
    return rows,ref

def history_metrics(history,benchmark):
    result={'observations':len(history),'history_start':history[0]['date'] if history else None,'history_end':history[-1]['date'] if history else None}
    bm={r['date']:r['close'] for r in benchmark}
    for n in (5,10,20,60,120):
        if len(history)>=n:
            window=history[-n:]
            result[f'net_{n}d_yuan']=sum(r['net_yuan'] for r in window)
            result[f'positive_{n}d']=sum(r['net_yuan']>0 for r in window)/n
        if len(history)>=n+1:
            start,end=history[-n-1],history[-1]
            ret=end['close']/start['close']-1
            result[f'return_{n}d']=ret
            if start['date'] in bm and end['date'] in bm:
                result[f'excess_{n}d']=ret-(bm[end['date']]/bm[start['date']]-1)
            peak=history[-n-1]['close']; dd=0
            for r in history[-n-1:]:
                peak=max(peak,r['close']);dd=min(dd,r['close']/peak-1)
            result[f'drawdown_{n}d']=dd
    return result

def financial_candidates(income,cash,sectors,asof):
    """Same-period cumulative statements. No invented valuation or industry mappings."""
    mapping={s['code']:s for s in sectors}; rows=[]; discarded=0
    for symbol,r in income.items():
        if not symbol.endswith(('.SH','.SZ')): continue
        industry='BK'+str(r.get('INDUSTRY_CODE','')).zfill(4)
        sector=mapping.get(industry)
        # Verify code AND name rather than guessing taxonomy from similar labels.
        if not sector or sector['name']!=r.get('INDUSTRY_NAME'): continue
        notice=r.get('NOTICE_DATE','')[:10]
        c=cash.get(symbol,{})
        # Dates-only disclosures are conservatively usable the next calendar day.
        if not notice or str(date.fromisoformat(notice)+timedelta(days=1))>asof[:10]: continue
        if c.get('NOTICE_DATE','')[:10]>=asof[:10]: c={}
        revenue=finite(r.get('TOTAL_OPERATE_INCOME'));profit=finite(r.get('PARENT_NETPROFIT'))
        growth=finite(r.get('TOI_RATIO'));pg=finite(r.get('PARENT_NETPROFIT_RATIO'));ocf=finite(c.get('NETCASH_OPERATE'))
        if None in (revenue,profit,growth,pg) or revenue<=0 or profit<=0 or growth<=0 or pg<=0:
            discarded+=1;continue
        # Banks/insurance cash flow is economically distinct; do not compare it to industrial cash cover.
        financial=any(k in sector['name'] for k in ('银行','保险','证券','多元金融'))
        cover=ocf/profit if ocf is not None and not financial else None
        status='收入利润增长，现金流支持' if cover is not None and cover>=.8 else '收入利润增长，现金流待跟踪'
        if financial: status='收入利润增长，金融业专用指标另评'
        row=dict(symbol=symbol,name=r['SECURITY_NAME_ABBR'],industry_code=industry,industry_name=sector['name'],revenue_yuan=revenue,profit_yuan=profit,
                 revenue_growth_pct=growth,profit_growth_pct=pg,operating_cashflow_yuan=ocf,cash_profit_ratio=cover,report_period=r['REPORT_DATE'][:10],notice_date=notice,
                 classification=status,source_url='https://data.eastmoney.com/bbsj/lrb.html',evidence_id='fundamental:'+symbol,
                 basis='报告期累计同比，非单季/非TTM；当前供应商修订版',company_research_score=None)
        rows.append(row)
    # Cap extraordinary rebound growth, score within industry; cashflow absence remains explicit.
    for industry in {r['industry_code'] for r in rows}:
        group=[r for r in rows if r['industry_code']==industry]
        for r in group:
            g=min(max(r['revenue_growth_pct'],0),50)/50
            p=min(max(r['profit_growth_pct'],0),100)/100
            quality=min(max(r['cash_profit_ratio'],0),1.5)/1.5 if r['cash_profit_ratio'] is not None else None
            # When cash evidence is absent don't impute neutral credit: score growth alone on its own scale.
            r['growth_score']=round(100*(.5*g+.5*p),1)
            r['cash_quality_score']=round(quality*100,1) if quality is not None else None
            r['peer_count']=len(group)
    return sorted(rows,key=lambda r:(r['cash_profit_ratio'] is None,-(r['cash_quality_score'] or 0),-r['growth_score'],r['symbol'])),discarded

def sector_financials(income,sectors,asof):
    mapping={s['code']:s for s in sectors};output={}
    for symbol,r in income.items():
        code='BK'+str(r.get('INDUSTRY_CODE','')).zfill(4)
        if not symbol.endswith(('.SH','.SZ')) or code not in mapping or mapping[code]['name']!=r.get('INDUSTRY_NAME'): continue
        notice=r.get('NOTICE_DATE','')[:10]
        if not notice or str(date.fromisoformat(notice)+timedelta(days=1))>asof[:10]: continue
        g,p=finite(r.get('TOI_RATIO')),finite(r.get('PARENT_NETPROFIT_RATIO'))
        if g is None or p is None: continue
        item=output.setdefault(code,dict(disclosed_companies=0,revenue_growing=0,profit_growing=0,both_growing=0))
        item['disclosed_companies']+=1;item['revenue_growing']+=g>0;item['profit_growing']+=p>0
        item['both_growing']+=g>0 and p>0 and (finite(r.get('PARENT_NETPROFIT')) or 0)>0
    for item in output.values(): item['growth_breadth']=item['both_growing']/item['disclosed_companies']
    return output

def enrich(sectors,store,history_limit=30,transport=get_json):
    stamp=now(); root=store.root/'normalized'/'sector_research';root.mkdir(parents=True,exist_ok=True)
    cache=root/'enrichment.json';old=json.loads(cache.read_text()) if cache.exists() else {}
    result=dict(generated_at=stamp,period=fiscal_period(stamp),history={},financial_candidates=[],issues=[],snapshots=[],benchmark=[],financial_scope={})
    for report in ('RPT_DMSK_FN_INCOME','RPT_DMSK_FN_CASHFLOW'):
        print('正在获取公开财报：'+report,flush=True)
        try:
            data,refs=fetch_statements(store,report,result['period'],transport)
            result[report]=data;result['snapshots'].extend(refs)
        except (ValueError,KeyError,TypeError) as e:
            result['issues'].append(dict(source=report,error=str(e)))
            if old.get(report) and old.get('period')==result['period']:
                result[report]=old[report];result['issues'].append(dict(source=report,error='使用同报告期缓存',cached_at=old['generated_at']))
    result['sector_financials']=sector_financials(result.get('RPT_DMSK_FN_INCOME',{}),sectors,stamp)
    result['financial_scope']={k:len(result.get(k,{})) for k in ('RPT_DMSK_FN_INCOME','RPT_DMSK_FN_CASHFLOW')}
    result['financial_candidates'],result['excluded_growth_count']=financial_candidates(result.get('RPT_DMSK_FN_INCOME',{}),result.get('RPT_DMSK_FN_CASHFLOW',{}),sectors,stamp)
    try:
        result['benchmark'],ref=fetch_benchmark(store,transport);result['snapshots'].append(ref)
    except (ValueError,KeyError,TypeError) as e: result['issues'].append(dict(source='沪深300',error=str(e)))
    # Research balanced inflow and outflow samples rather than only cherry-picking inflow leaders.
    ordered=sorted(sectors,key=lambda r:-(r.get('flow_watch_score') or 0))
    chosen=ordered[:history_limit*2//3]+sorted(sectors,key=lambda r:r.get('net_10d_yuan') if r.get('net_10d_yuan') is not None else float('inf'))[:history_limit-history_limit*2//3]
    chosen=list({s['code']:s for s in chosen}.values())
    consecutive_failures=0
    for i,sector in enumerate(chosen):
        print(f"行业历史 {i+1}/{len(chosen)}：{sector['name']}",flush=True)
        try:
            history,ref=fetch_history(store,sector,transport);result['snapshots'].append(ref)
            consecutive_failures=0
            result['history'][sector['code']]=dict(series=history,metrics=history_metrics(history,result['benchmark']),snapshot_id=ref['snapshot_id'])
        except (ValueError,KeyError,TypeError) as e:
            consecutive_failures+=1
            result['issues'].append(dict(source=sector['code'],error=str(e)))
            if consecutive_failures>=3:
                result['issues'].append(dict(source='sector_history',error='连续三个行业失败，停止本批历史请求；保留成功的财报数据'))
                break
        time.sleep(.2)
    result['price_history']={};failures=0
    price_candidates=[s for s in ordered if result['sector_financials'].get(s['code'],{}).get('disclosed_companies',0)>=3][:history_limit]
    for i,sector in enumerate(price_candidates):
        previous=old.get('price_history',{}).get(sector['code'])
        if previous and previous.get('series') and previous['series'][-1]['date']==(sector.get('data_time') or '')[:10]:
            result['price_history'][sector['code']]=previous
            result['snapshots'] += [r for r in old.get('snapshots',[]) if '/sector_price_history/'+sector['code']+'/' in r.get('path','')]
            continue
        print(f"行业价格与成交额 {i+1}/{len(price_candidates)}：{sector['name']}",flush=True)
        try:
            series,ref=fetch_price_history(store,sector,transport);result['snapshots'].append(ref)
            result['price_history'][sector['code']]=dict(series=series,snapshot_id=ref['snapshot_id']);failures=0
        except (ValueError,KeyError,TypeError) as e:
            failures+=1;result['issues'].append(dict(source=sector['code']+'.price',error=str(e)))
            if failures>=3:
                result['issues'].append(dict(source='sector_price_history',error='连续三个价格接口失败，停止本批；不伪造RS和成交额'));break
        time.sleep(.2)
    atomic_json(cache,result)
    return result

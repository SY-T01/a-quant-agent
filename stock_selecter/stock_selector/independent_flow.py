"""Independent THS observations; never substitute its taxonomy into Eastmoney scores."""
import html
import json
import math
import re
import time
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from .storage import atomic_json, now

NOTICE='同花顺独立行业观察：分类及资金定义与东财不同，不合并评分、不替换股票行业映射、不用于本期逐股大模型复核；页面未明确标注交易日期，采集时间和HTTP页面修改时间不视为交易时间。'
BASE='https://data.10jqka.com.cn/funds/hyzjl/'

class Table(HTMLParser):
    def __init__(self):
        super().__init__();self.rows=[];self.cells=[];self.links=[];self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.cells=[];self.links=[]
        if tag in ('td','th'):self.cell=''
        if tag=='a':self.links.append(dict(attrs).get('href',''))
    def handle_data(self,data):
        if self.cell is not None:self.cell+=data
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:self.cells.append(self.cell.strip());self.cell=None
        if tag=='tr' and self.cells:self.rows.append((self.cells,self.links))

def parse_page(text,period):
    # Isolate the table: malformed unrelated script tags must not swallow its HTML.
    match=re.search(r'<table\b[^>]*class=["\'][^"\']*m-table[^"\']*["\'][^>]*>.*?</table>',text,re.S|re.I)
    page=re.search(r'class=["\']page_info["\'][^>]*>\s*(\d+)/(\d+)',text)
    if not match or not page:raise ValueError('同花顺页面无完整行业表或分页信息')
    parser=Table();parser.feed(match.group());headers=parser.rows[0][0] if parser.rows else []
    required=['行业','流入资金(亿)','流出资金(亿)','净额(亿)','公司家数']
    if any(headers.count(k)!=1 for k in required):raise ValueError('同花顺字段结构变化')
    indexes={k:headers.index(k) for k in required};rows=[]
    for cells,links in parser.rows[1:]:
        if len(cells)!=len(headers):raise ValueError('同花顺行业列数不一致')
        codes=[m.group(1) for link in links if (m:=re.search(r'/thshy/detail/code/(\d{6})/',link))]
        if len(codes)!=1:raise ValueError('同花顺行业代码缺失或重复')
        row=dict(code=codes[0],name=cells[indexes['行业']],period=period,provider='ths',taxonomy='同花顺行业',trade_date=None)
        for label,key in [('流入资金(亿)','inflow_yuan'),('流出资金(亿)','outflow_yuan'),('净额(亿)','net_yuan')]:
            v=float(cells[indexes[label]].replace(',',''))*1e8
            if not math.isfinite(v):raise ValueError('同花顺资金数值无效')
            row[key]=v
        if row['inflow_yuan']<0 or row['outflow_yuan']<0 or abs(row['inflow_yuan']-row['outflow_yuan']-row['net_yuan'])>2000000.01:raise ValueError('同花顺资金净额核对失败')
        row['company_count']=int(cells[indexes['公司家数']]);rows.append(row)
    current,pages=map(int,page.groups())
    if not rows or not 1<=current<=pages<=10:raise ValueError('同花顺分页范围无效')
    return rows,current,pages

def get_page(url):
    with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':BASE}),timeout=15) as response:
        body=response.read(1000001)
        if len(body)>1000000:raise ValueError('同花顺页面超过读取上限')
        charset=response.headers.get_content_charset() or 'gb18030'
        return dict(text=body.decode(charset),last_modified=response.headers.get('Last-Modified'),observed_at=now())

def fetch(store,transport=get_page):
    rows=[];refs=[];universes=[];modified=[]
    for period in ('today','5d','10d'):
        page=1;pages=1;seen=set();declared=None
        while page<=pages:
            prefix='' if period=='today' else 'board/'+period[:-1]+'/'
            url=BASE+prefix+f'field/tradezdf/order/desc/page/{page}/'
            response=transport(url);items,current,pages=parse_page(response['text'],period)
            if current!=page or (declared is not None and pages!=declared):raise ValueError('同花顺分页数量变化')
            declared=pages
            for row in items:
                if row['code'] in seen:raise ValueError('同花顺分页重复，禁止发布部分榜单')
                seen.add(row['code']);row.update(source_url=url,observed_at=response['observed_at'],page_last_modified=response.get('last_modified'));rows.append(row)
            modified.append(response.get('last_modified'))
            refs.append(store.save('ths','industry_flow',period+'_page_'+str(page),dict(request_url=url,**response),[dict(date=response['observed_at'][:10],rows=items)]))
            page+=1
            time.sleep(1)
        universes.append(seen)
    if any(s!=universes[0] for s in universes):raise ValueError('同花顺各周期行业范围不一致')
    return dict(provider='ths',status='observed',observed_at=now(),trade_date=None,notice=NOTICE,rows=rows,industry_count=len(universes[0]),snapshots=refs)

def attach(report,store,refresh=False):
    cache=store.root/'normalized'/'sector_research'/'ths_observation.json';error=None
    if refresh:
        try:result=fetch(store);atomic_json(cache,result)
        except (OSError,ValueError,KeyError,TypeError,UnicodeError) as e:error=type(e).__name__;result=None
    else:result=None
    if result is None:
        result=json.loads(cache.read_text()) if cache.exists() else dict(provider='ths',rows=[],notice=NOTICE,trade_date=None)
        result=dict(result,status='cached' if cache.exists() else 'unavailable')
    if error:result=dict(result,refresh_error_type=error)
    report['independent_industry_flow']=result
    return result

def paragraphs(report):
    data=report.get('independent_industry_flow')
    if not data:return []
    lines=[NOTICE,'来源状态：'+{'observed':'已采集','cached':'本地存档','unavailable':'暂不可用'}.get(data['status'],'未知')+'；采集时间：'+str(data.get('observed_at','未取得'))+'；覆盖行业：'+str(data.get('industry_count',0))+'个。']
    if report.get('industry_refresh_diagnostics'):lines.insert(0,'东财主榜本次更新失败，仍使用截至 '+str(report.get('data_times',{}).get('last'))+' 的缓存；下列同花顺观察单独展示。')
    if data.get('refresh_error_type'):lines.append('本次备用源更新失败：'+data['refresh_error_type']+'；不得将缓存称为最新交易数据。')
    for period,label in [('today','即时'),('5d','5日'),('10d','10日')]:
        rows=sorted((r for r in data.get('rows',[]) if r['period']==period),key=lambda r:-r['net_yuan'])
        if rows:
            lines.append(label+'净流入居前：'+'；'.join(r['name']+f" {r['net_yuan']/1e8:+.2f}亿元" for r in rows[:5])+'。')
            lines.append(label+'净流出居前：'+'；'.join(r['name']+f" {r['net_yuan']/1e8:+.2f}亿元" for r in reversed(rows[-5:]))+'。')
    return lines

def html_section(report):
    lines=paragraphs(report)
    if not lines:return ''
    return '<section><h2>独立来源：同花顺行业资金观察</h2>'+''.join('<p>'+html.escape(s)+'</p>' for s in lines)+'<p><a href="'+BASE+'">查看同花顺原始页面</a> · <a href="./同花顺行业资金.csv">下载完整行业资金表</a></p></section>'

def markdown_section(report):
    lines=paragraphs(report)
    return ['','## 独立来源：同花顺行业资金观察','',*sum(([s,''] for s in lines),[]),'来源：'+BASE,''] if lines else []


def save_outputs(report,out):
    import csv
    data=report.get('independent_industry_flow',{})
    if not data.get('rows'):return
    with (out/'同花顺行业资金.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['周期','同花顺行业代码','行业','流入资金_元','流出资金_元','净额_元','采集时间','交易日期_未标注留空','原始页面'])
        for r in data['rows']:
            writer.writerow([dict(today='即时',**{'5d':'5日','10d':'10日'})[r['period']],r['code'],r['name'],r['inflow_yuan'],r['outflow_yuan'],r['net_yuan'],r['observed_at'],r.get('trade_date') or '',r['source_url']])

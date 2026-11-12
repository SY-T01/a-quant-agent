"""Official macro series and company disclosure retrieval, bounded and source-labelled."""
import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import re
import subprocess
import time
from datetime import datetime,timedelta
from pathlib import Path
from urllib.parse import urlencode,urljoin,urlparse
from urllib.request import Request,urlopen
from .storage import Store,PROJECT,now,atomic_json

NBS='https://www.stats.gov.cn/sj/zxfb/'
PBC='https://www.pbc.gov.cn/diaochatongjisi/116219/116225/index.html'
ALLOWED={'www.stats.gov.cn','www.pbc.gov.cn','www.cninfo.com.cn','static.cninfo.com.cn','datacenter.eastmoney.com'}


def fetch_bytes(url,data=None):
    if urlparse(url).scheme!='https' or urlparse(url).hostname not in ALLOWED:raise ValueError('来源不在允许的公开数据域名内')
    request=Request(url,data=urlencode(data).encode() if data else None,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.cninfo.com.cn/' if 'cninfo' in url else 'https://www.stats.gov.cn/'})
    with urlopen(request,timeout=25) as f:
        if urlparse(f.geturl()).hostname not in ALLOWED:raise ValueError('来源跳转到未批准域名')
        declared=getattr(f,'headers',{}).get('Content-Length','')
        if str(declared).isdigit() and int(declared)>25_000_000:
            raise ValueError('公开文档超过25MB上限')
        result=f.read(25_000_001)
    if len(result)>25_000_000:raise ValueError('公开文档超过25MB上限')
    return result


class Page(HTMLParser):
    def __init__(self):
        super().__init__();self.meta={};self.links=[];self.tables=[];self.text=[];self.link=None;self.table=None;self.row=None;self.cell=None;self.ignore=0
    def handle_starttag(self,t,attrs):
        a=dict(attrs)
        if t in ('script','style'):self.ignore+=1
        if t=='meta':self.meta[a.get('name','').lower()]=a.get('content','')
        if t=='a':self.link=[a.get('href',''),[]]
        if t=='table':self.table=[]
        if t=='tr':self.row=[]
        if t in ('td','th'):self.cell=[]
    def handle_data(self,s):
        if self.ignore:return
        self.text.append(s)
        if self.link:self.link[1].append(s)
        if self.cell is not None:self.cell.append(s)
    def handle_endtag(self,t):
        if t in ('script','style'):self.ignore=max(0,self.ignore-1)
        if t=='a' and self.link:self.links.append((self.link[0],''.join(self.link[1]).strip()));self.link=None
        if t in ('td','th') and self.cell is not None:
            if self.row is not None:self.row.append(re.sub(r'\s+','', ''.join(self.cell)))
            self.cell=None
        if t=='tr' and self.row is not None:
            if self.table is not None:self.table.append(self.row)
            self.row=None
        if t=='table' and self.table is not None:self.tables.append(self.table);self.table=None


def parse_page(body):
    p=Page();p.feed(body.decode('utf-8-sig') if isinstance(body,bytes) else body);return p


def publication(page,asof):
    raw=page.meta.get('pubdate','').replace('/','-')
    try:dt=datetime.fromisoformat(raw)
    except ValueError:raise ValueError('官方页面没有可识别发布日期') from None
    # A date without a time becomes available conservatively on the next day.
    available=dt.replace(tzinfo=asof.tzinfo) if ' ' in raw or 'T' in raw else (dt+timedelta(days=1)).replace(tzinfo=asof.tzinfo)
    if available>asof:raise ValueError('官方资料在研究时点尚未公开')
    return raw,available.isoformat()


def discover_links(page,base,keyword):
    items={urljoin(base,href):title for href,title in page.links if keyword in title}
    return sorted(items.items(),key=lambda p:p[0],reverse=True)


def pmi_series(page):
    chosen=None
    for table in page.tables:
        headings=''.join(''.join(r) for r in table[:4])
        if 'PMI' in headings and '新订单' in headings and '生产' in headings:chosen=table;break
    if not chosen:raise ValueError('未识别到制造业PMI主表')
    rows=[];seen=set()
    for cells in chosen:
        if len(cells)!=7:continue
        match=re.fullmatch(r'(20\d{2})年(\d{1,2})月',cells[0])
        if not match:continue
        if not 1<=int(match[2])<=12:raise ValueError('PMI月份非法')
        period=f'{int(match[1]):04d}-{int(match[2]):02d}'
        if period in seen:raise ValueError('PMI月份重复')
        seen.add(period)
        values=list(map(float,cells[1:]))
        if not all(0<=v<=100 for v in values):raise ValueError('PMI指标超出有效范围')
        rows.append(dict(period=period,**dict(zip(['pmi','production','new_orders','raw_inventory','employment','supplier_delivery'],values))))
    if len(rows)<4:raise ValueError('PMI有效历史不足4期')
    rows.sort(key=lambda r:r['period'])
    months=[int(r['period'][:4])*12+int(r['period'][5:]) for r in rows]
    if any(b-a!=1 for a,b in zip(months,months[1:])):raise ValueError('PMI月份不连续，不计算三个月变化')
    return rows


def trend(values):
    latest=values[-1];prior=values[-2] if len(values)>1 else None
    return dict(value=latest,previous=prior,change=latest-prior if prior is not None else None,
                change_3m=latest-values[-4] if len(values)>=4 else None,
                percentile=sum(v<=latest for v in values)/len(values),sample_months=len(values))


def credit_point(page):
    text=re.sub(r'\s+','',''.join(page.text));result={}
    match=re.search(r'(20\d{2})年(\d{1,2})月末[，,]?社会融资规模存量',text)
    if not match:raise ValueError('央行报告期无法识别')
    result['period']=f'{int(match[1]):04d}-{int(match[2]):02d}'
    expressions={'afre_yoy':r'社会融资规模存量(?:为[^。]{0,60}?，)?同比(增长|下降)([\d.]+)%',
                 'm2_yoy':r'广义货币[（(]M2[）)]余额[^。]{0,60}?同比(增长|下降)([\d.]+)%'}
    for key,exp in expressions.items():
        m=re.search(exp,text)
        if not m:raise ValueError('央行增速字段未识别：'+key)
        result[key]=float(m[2])*(-1 if m[1]=='下降' else 1)
    return result


def fetch_macro(store,asof,transport=fetch_bytes):
    result=dict(indicators=[],sources=[],issues=[],series={})
    try:directory=parse_page(transport(NBS))
    except (OSError,ValueError) as e:
        directory=Page();result['issues'].append(dict(source='国家统计局目录',error=type(e).__name__))
    for index in range(1,4):
        if all(any(k in title for _,title in directory.links) for k in ('居民消费价格同比','工业生产者出厂价格同比')):break
        try:directory.links.extend(parse_page(transport(urljoin(NBS,f'index_{index}.html'))).links)
        except (OSError,ValueError):pass
    targets=discover_links(directory,NBS,'中国采购经理指数运行情况')
    if not targets:result['issues'].append(dict(source='PMI',error='未发现当前可访问的原文'))
    for url,title in targets[:3]:
        try:
            body=transport(url);page=parse_page(body);pub,available=publication(page,asof);series=pmi_series(page)
            ref=store.save('nbs','macro','pmi',dict(url=url,body=body.decode('utf-8')), [dict(date=pub[:10].replace('/','-'),series=series)])
            result['sources'].append(dict(evidence_id='macro:pmi',title=title,url=url,published_at=pub,available_at=available,observed_at=now(),snapshot=ref))
            result['series']['pmi']=series
            for key,label in [('pmi','制造业PMI'),('new_orders','制造业新订单'),('production','制造业生产')]:
                item=dict(code=key,name=label,period=series[-1]['period'],unit='指数',evidence_id='macro:pmi',**trend([r[key] for r in series]))
                item['state']='扩张区间' if item['value']>=50 else '收缩区间'
                result['indicators'].append(item)
            break
        except (ValueError,OSError) as e:result['issues'].append(dict(source=url,error=str(e)))
    # CPI/PPI use explicit headline year-on-year values; do not infer from unrelated paragraphs.
    for keyword,code,label in [('居民消费价格同比','cpi_yoy','CPI同比'),('工业生产者出厂价格同比','ppi_yoy','PPI同比')]:
        links=discover_links(directory,NBS,keyword)
        for url,title in links[:2]:
            try:
                body=transport(url);page=parse_page(body);pub,available=publication(page,asof)
                title=page.meta.get('articletitle') or title
                m=re.search(r'(20\d{2})年(\d{1,2})月份.*?同比(上涨|下降|持平)([\d.]+)?',title)
                if not m or (m[3]!='持平' and not m[4]):raise ValueError('价格标题字段改变')
                value=0 if m[3]=='持平' else float(m[4])*(-1 if m[3]=='下降' else 1)
                ref=store.save('nbs','macro',code,dict(url=url,body=body.decode('utf8')),[dict(date=pub[:10].replace('/','-'),value=value)])
                eid='macro:'+code
                result['sources'].append(dict(evidence_id=eid,title=title,url=url,published_at=pub,available_at=available,observed_at=now(),snapshot=ref))
                result['indicators'].append(dict(code=code,name=label,period=f'{m[1]}-{int(m[2]):02d}',unit='%',value=value,change=None,change_3m=None,percentile=None,sample_months=1,evidence_id=eid,state='价格同比上涨' if value>0 else '价格同比下降' if value<0 else '同比持平'))
                break
            except (ValueError,OSError) as e:result['issues'].append(dict(source=url,error=str(e)))
    try:
        listing=parse_page(transport(PBC));links=discover_links(listing,PBC,'金融统计数据报告');points=[]
        def title_period(item):
            title=item[1];year=re.search(r'(20\d{2})年',title);month=re.search(r'年(\d{1,2})月',title)
            m=int(month[1]) if month else 9 if '前三季度' in title else 6 if '上半年' in title else 3 if '一季度' in title else 12
            return (int(year[1]) if year else 0,m)
        links.sort(key=title_period,reverse=True)
        for url,title in links[:3]:
            body=transport(url);page=parse_page(body);pub,available=publication(page,asof);point=credit_point(page);points.append(point)
            ref=store.save('pbc','macro',point['period'].replace('-',''),dict(url=url,body=body.decode('utf8')),[dict(date=pub[:10],**point)])
            result['sources'].append(dict(evidence_id='macro:credit:'+point['period'],title=title,url=url,published_at=pub,available_at=available,observed_at=now(),snapshot=ref))
        points.sort(key=lambda r:r['period']);result['series']['credit']=points
        for key,label in [('m2_yoy','M2同比'),('afre_yoy','社融存量同比')]:
            item=dict(code=key,name=label,period=points[-1]['period'],unit='%',evidence_id='macro:credit:'+points[-1]['period'],**trend([r[key] for r in points]))
            item['state']='增速上升' if item['change'] is not None and item['change']>0 else '增速回落' if item['change'] is not None and item['change']<0 else '增速持平'
            result['indicators'].append(item)
    except (ValueError,OSError) as e:result['issues'].append(dict(source='央行金融统计',error=str(e)))
    if not result['indicators']:raise ValueError('没有成功解析的官方宏观指标')
    result['method']='PMI与50比较；变化为百分点；分位仅基于本次取得的月份，不作长期分位或收益预测。M2与社融增速不能直接等同货币政策松紧。'
    return result


def get_json(url,data=None,transport=fetch_bytes):return json.loads(transport(url,data))


def profile(symbol,store,transport=fetch_bytes):
    url='https://datacenter.eastmoney.com/securities/api/data/v1/get?'+urlencode(dict(reportName='RPT_F10_ORG_BASICINFO',columns='ALL',filter=f'(SECUCODE="{symbol}")'))
    raw=get_json(url,transport=transport);data=(raw.get('result') or {}).get('data')
    if not raw.get('success') or not data or data[0].get('SECUCODE')!=symbol:raise ValueError('公司资料代码不匹配')
    row=data[0];ref=store.save('eastmoney','company_profile',symbol,dict(url=url,response=raw),[dict(date=now()[:10],profile=row)])
    return dict(evidence_id='profile:'+symbol,symbol=symbol,name=row['SECURITY_NAME_ABBR'],business=row.get('MAIN_BUSINESS') or '',introduction=row.get('ORG_PROFILE') or row.get('ORG_PROFIE','')[:450],
                product_mix=row.get('INCOME_STRU_NAMENEW') or row.get('INCOME_STRU_NAME',''),product_mix_ratios=row.get('INCOME_STRU_RATIONEW') or row.get('INCOME_STRU_RATIO',''),data_period_hint=row.get('MAX_DATE','')[:10],
                industry_levels=[dict(code=row.get(f'BOARD_CODE_BK_{i}LEVEL'),name=row.get(f'BOARD_NAME_{i}LEVEL')) for i in (1,2,3)],source_url=url,observed_at=now(),source_kind='供应商整理的公司资料，须结合原始公告核对',snapshot=ref)


def announcement_list(symbol,asof,transport=fetch_bytes):
    code=symbol[:6];search=get_json('https://www.cninfo.com.cn/new/information/topSearch/query',dict(keyWord=code,maxNum=10),transport)
    item=next((r for r in search if r.get('code')==code and r.get('category')=='A股'),None)
    if not item:raise ValueError('未找到匹配的巨潮证券身份')
    params=dict(pageNum=1,pageSize=20,column='sse' if symbol.endswith('.SH') else 'szse',tabName='fulltext',stock=code+','+item['orgId'],searchkey='年度报告',secid='',category='',plate='',seDate=str((asof-timedelta(days=550)).date())+'~'+str(asof.date()),sortName='time',sortType='desc',isHLtitle='false')
    documents=[]
    for keyword in ('年度报告','投资者关系活动记录'):
        params['searchkey']=keyword
        response=get_json('https://www.cninfo.com.cn/new/hisAnnouncement/query',params,transport)
        eligible=[]
        for r in response.get('announcements') or []:
            title=re.sub('<[^>]+>','',r['announcementTitle'])
            pub=datetime.fromtimestamp(r['announcementTime']/1000,asof.tzinfo)
            # Date-only release metadata is conservatively usable on the next day.
            if r.get('secCode')!=code or pub.date()>=asof.date() or any(s in title for s in ('摘要','英文','已取消')):continue
            if keyword=='年度报告' and '年度报告' not in title:continue
            if keyword!='年度报告' and not any(s in title for s in ('投资者关系','调研活动')):continue
            adjunct=r.get('adjunctUrl','')
            if not re.fullmatch(r'finalpage/\d{4}-\d{2}-\d{2}/\d+\.PDF',adjunct,re.I):continue
            eligible.append(dict(symbol=symbol,title=title,published_at=pub.isoformat(),source_url='https://static.cninfo.com.cn/'+adjunct,evidence_id='disclosure:'+symbol+':'+str(r['announcementId']),announcement_id=str(r['announcementId'])))
        if eligible:documents.append(sorted(eligible,key=lambda d:d['published_at'],reverse=True)[0])
    return documents


def read_pdf_excerpts(path,store):
    settings=json.loads((PROJECT/'configs/pdf.json').read_text());python=settings['python']
    script="from pypdf import PdfReader; import json,sys; r=PdfReader(sys.argv[1]); print(json.dumps([{'page':i+1,'text':(p.extract_text() or '')} for i,p in enumerate(r.pages[:220])],ensure_ascii=False))"
    result=subprocess.run([python,'-B','-c',script,str(path)],capture_output=True,text=True,timeout=90)
    if result.returncode:raise ValueError('公告PDF文本提取未完成')
    pages=json.loads(result.stdout)
    return retrieve_excerpts(pages)


def retrieve_excerpts(pages):
    """BM25 over Chinese character bigrams, with explicit management-heading boosts."""
    import math
    from collections import Counter
    def tokens(text):
        chars=''.join(re.findall(r'[\u4e00-\u9fffA-Za-z0-9]',text))
        return [chars[i:i+2] for i in range(len(chars)-1)]
    chunks=[]
    for p in pages:
        if p['page']<=4 and len(pages)>20:continue
        if len(pages)>30 and p['page']>min(80,len(pages)*.55):continue
        text=re.sub(r'\s+',' ',p['text'])
        for start in range(0,len(text),650):
            sample=text[start:start+800]
            if len(sample)<100 or sample.count('…')>3:continue
            chunks.append(dict(page=p['page'],text=sample,counts=Counter(tokens(sample))))
    if not chunks:return []
    df=Counter()
    for c in chunks:df.update(c['counts'].keys())
    avg=sum(sum(c['counts'].values()) for c in chunks)/len(chunks)
    definitions=[('业务与产品','主要业务 主营业务 主要产品 业务模式 客户',r'主营业务(?:情况|包括|为|及)|主要业务|主要产品|从事的.*?业务'),('发展方向','发展战略 经营计划 研发 创新 市场拓展',r'发展战略|经营计划|未来发展|年度经营目标'),('主要风险','面临风险 市场竞争 原材料价格 波动 风险因素',r'面临的风险|主要风险|风险因素|可能面对的风险|价格波动风险')]
    excerpts=[]
    for topic,query,heading in definitions:
        ranked=[]
        for c in chunks:
            match=re.search(heading,c['text'])
            if not match:continue
            length=sum(c['counts'].values());score=0
            for term in tokens(query):
                freq=c['counts'][term]
                if freq:
                    idf=math.log(1+(len(chunks)-df[term]+.5)/(df[term]+.5))
                    score+=idf*freq*2.2/(freq+1.2*(.25+.75*length/max(avg,1)))
            score+=8
            if any(word in c['text'] for word in ('主要风险和报酬的转移','会计政策','金融工具的分类')):score*=.1
            ranked.append((score,c,match.start()))
        if ranked:
            score,c,start=max(ranked,key=lambda v:v[0])
            if score<5:continue
            excerpt=c['text'][max(0,start-15):start+430]
            excerpts.append(dict(topic=topic,pdf_page=c['page'],excerpt=excerpt,retrieval_method='BM25-char-bigram-v1',retrieval_score=round(score,3)))
    return excerpts


def download_disclosure(document,store,transport=fetch_bytes):
    body=transport(document['source_url'])
    if not body.startswith(b'%PDF-'):raise ValueError('公告链接未返回PDF')
    sha=hashlib.sha256(body).hexdigest();dest=store.root/'raw'/'disclosures'/(sha+'.pdf');dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():
        with dest.open('xb') as f:f.write(body)
    elif hashlib.sha256(dest.read_bytes()).hexdigest()!=sha:raise ValueError('公告文件校验冲突')
    excerpts=read_pdf_excerpts(dest,store)
    return dict(document,extraction_version='bm25-v1',local_path=str(dest),sha256=sha,observed_at=now(),excerpts=excerpts,snapshot=dict(path=str(dest),sha256=sha))


def industry_effects(name,macro):
    indicators={i['code']:i for i in macro.get('indicators',[])};effects=[]
    rules=[(('光伏','电力设备','逆变器'),'制造业需求及信用周期会影响项目投资与设备订单；还需单独核对海外订单、政策和产品价格。'),
           (('金属','材料','化工'),'关注需求和价格传导；全国PPI不能直接代替锂、铜等具体商品价格，库存与产能决定利润弹性。'),
           (('包装','纸','个护'),'关注订单与终端消费，投入成本上涨未必能同步提价，应核对毛利率和客户结构。'),
           (('贸易','物流','供应链'),'关注生产和订单周转；信用增速只是环境变量，不能直接推断贸易利润。'),
           (('游戏','传媒','软件'),'制造业PMI解释力有限，重点转向用户付费、产品周期、监管及企业IT预算。')]
    for words,text in rules:
        if any(w in name for w in words):effects.append(text)
    if not effects:effects=['宏观影响需结合本行业需求、成本、融资和估值渠道判断，暂不赋予自动加减分。']
    return dict(industry=name,effects=effects,evidence_ids=[i['evidence_id'] for i in indicators.values()],kind='规则解释的条件性传导假说，不是统计估计的行业收益因果关系')


def public_document_error(error):
    known={'公开文档超过25MB上限','公告链接未返回PDF','公告PDF文本提取未完成','来源不在允许的公开数据域名内','来源跳转到未批准域名','未找到匹配的巨潮证券身份','公告文件校验冲突'}
    message=str(error)
    return type(error).__name__+('（'+message+'）' if message in known else '')


def refresh_context(report,store,transport=fetch_bytes):
    asof=datetime.fromisoformat(report['asof']);path=store.root/'normalized/context_research/context.json'
    old=json.loads(path.read_text()) if path.exists() else {}
    context=dict(generated_at=now(),macro={},companies=[],issues=[],snapshots=[])
    print('正在更新官方宏观资料…',flush=True)
    try:context['macro']=fetch_macro(store,asof,transport)
    except (OSError,ValueError,KeyError,TypeError) as e:
        context['macro']=old.get('macro',{});context['issues'].append(dict(source='macro',error=type(e).__name__,cache_used=bool(context['macro'])))
    previous={c['symbol']:c for c in old.get('companies',[])}
    for candidate in report.get('stock_recommendations',{}).get('stocks',[]):
        symbol=candidate['symbol'];print('公司业务与公告：'+candidate['name'],flush=True)
        company=dict(symbol=symbol,name=candidate['name'],industry_name=candidate['industry_name'],profile={},documents=[],issues=[])
        try:company['profile']=profile(symbol,store,transport)
        except (OSError,ValueError,KeyError,TypeError) as e:
            company['issues'].append('公司概况获取失败：'+type(e).__name__)
            company['profile']=previous.get(symbol,{}).get('profile',{})
        try:
            documents=announcement_list(symbol,asof,transport)
            for doc in documents:
                try:
                    # Reuse immutable PDFs; refresh the index to discover new releases each week.
                    cached=next((d for d in previous.get(symbol,{}).get('documents',[]) if d['source_url']==doc['source_url'] and Path(d.get('local_path','')).is_file()),None)
                    if cached and cached.get('extraction_version')!='bm25-v1':
                        cached=dict(cached,excerpts=read_pdf_excerpts(Path(cached['local_path']),store),extraction_version='bm25-v1')
                    company['documents'].append(cached or download_disclosure(doc,store,transport))
                except (OSError,ValueError,subprocess.TimeoutExpired) as e:
                    reason=public_document_error(e)
                    company['issues'].append('公告提取失败：'+reason)
                    company.setdefault('failed_documents',[]).append(dict(title=doc['title'],published_at=doc['published_at'],source_url=doc['source_url'],error=reason,body_read=False))
        except (OSError,ValueError,KeyError,TypeError) as e:company['issues'].append('公告索引获取失败：'+type(e).__name__)
        context['companies'].append(company)
        time.sleep(.25)
    for source in context['macro'].get('sources',[]):context['snapshots'].append(source['snapshot'])
    for company in context['companies']:
        if company['profile'].get('snapshot'):context['snapshots'].append(company['profile']['snapshot'])
        context['snapshots'] += [d['snapshot'] for d in company['documents']]
    atomic_json(path,context)
    return context


def attach(report,store,refresh=False):
    path=store.root/'normalized/context_research/context.json'
    context=refresh_context(report,store) if refresh else json.loads(path.read_text()) if path.exists() else dict(macro={},companies=[],issues=[])
    context=json.loads(json.dumps(context))
    cutoff=datetime.fromisoformat(report['asof'])
    sources=[]
    for source in context.get('macro',{}).get('sources',[]):
        try:
            if datetime.fromisoformat(source['available_at'])<=cutoff:sources.append(source)
        except (ValueError,TypeError,KeyError):pass
    valid={s['evidence_id'] for s in sources}
    context.setdefault('macro',{})['sources']=sources
    context['macro']['indicators']=[i for i in context['macro'].get('indicators',[]) if i['evidence_id'] in valid]
    for company in context.get('companies',[]):
        company['documents']=[d for d in company.get('documents',[]) if datetime.fromisoformat(d['published_at']).date()<cutoff.date()]
    selected={c['symbol']:c for c in report.get('stock_recommendations',{}).get('stocks',[])}
    context['companies']=[c for c in context.get('companies',[]) if c['symbol'] in selected]
    for c in context['companies']:
        c['macro_exposure']=industry_effects(c['industry_name'],context.get('macro',{}))
        # These are scenarios to test, not price targets or model-generated forecasts.
        c['scenarios']=[dict(label='乐观情景',condition='订单、收入及现金流持续改善，估值未透支，公告催化有数据兑现'),dict(label='基准情景',condition='现有经营趋势延续，但仍需核对季节性、竞争和估值'),dict(label='悲观情景',condition='需求或产品价格转弱、现金流下滑或公告否定原投资逻辑')]
        base=selected[c['symbol']]
        c['scenarios'][0]['condition']=f"公司披露的业务重点转化为订单和收入，现金流对增长形成支持；本期营收同比{base['revenue_growth_pct']:.1f}%和利润同比{base['profit_growth_pct']:.1f}%仅作比较基准，不外推增速。"
        c['scenarios'][1]['condition']='不预设高增幅延续，跟踪'+c['profile'].get('business','主营业务')[:65]+'的收入与毛利兑现，分别核对经营增长和估值变化。'
        c['scenarios'][2]['condition']=('本期利润增长超过100%，若主要是低基数或非经常性因素，不能据此外推；' if base['profit_growth_pct']>100 else '若行业需求转弱或竞争加剧，')+'经营现金流和后续公告否定增长逻辑时重新评估。'
        c['scenario_kind']='基于本期财务与主营的条件研究框架；尚未经过大模型结合公告定制，不是概率预测'
    refs=[s['snapshot'] for s in context.get('macro',{}).get('sources',[]) if s.get('snapshot')]
    for company in context['companies']:
        if company.get('profile',{}).get('snapshot'):refs.append(company['profile']['snapshot'])
        refs += [d['snapshot'] for d in company.get('documents',[]) if d.get('snapshot')]
    context['snapshots']=list({r['path']:r for r in refs}.values())
    sectors={s['code']:s for s in report.get('sectors',[])}
    context['industry_exposures']=[dict(code=code,**industry_effects(sectors[code]['name'],context.get('macro',{}))) for code in report.get('research_priorities',[])[:10] if code in sectors]
    report['research_context']=context
    return context


def evidence_packet(context):
    macro=context.get('macro',{});sources=[{k:s[k] for k in ('evidence_id','title','url','published_at','available_at','observed_at') if k in s} for s in macro.get('sources',[])]
    companies=[]
    for c in context.get('companies',[]):
        profile_data=c.get('profile',{})
        company=dict(symbol=c['symbol'],name=c['name'],industry=c['industry_name'],profile={k:profile_data.get(k) for k in ('evidence_id','business','introduction','product_mix','product_mix_ratios','data_period_hint','source_kind','observed_at')},macro_exposure=c.get('macro_exposure'),scenarios=c.get('scenarios'),scenario_kind=c.get('scenario_kind'),disclosures=[])
        for d in c.get('documents',[]):company['disclosures'].append({k:d[k] for k in ('evidence_id','title','published_at','source_url','observed_at','excerpts')})
        companies.append(company)
    return dict(macro=dict(indicators=macro.get('indicators',[]),sources=sources,method=macro.get('method')),industry_exposures=context.get('industry_exposures',[]),companies=companies)


def evidence_ids(context):
    result={s['evidence_id'] for s in context.get('macro',{}).get('sources',[])}
    for c in context.get('companies',[]):
        if c.get('profile',{}).get('evidence_id'):result.add(c['profile']['evidence_id'])
        result.update(d['evidence_id'] for d in c.get('documents',[]))
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--refresh',action='store_true');a=p.parse_args()
    report=json.loads((PROJECT/'reports/sector_research/research.json').read_text());c=attach(report,Store(),a.refresh)
    print(json.dumps(dict(indicators=len(c.get('macro',{}).get('indicators',[])),companies=len(c.get('companies',[])),documents=sum(len(c.get('documents',[])) for c in c.get('companies',[]))),ensure_ascii=False))

if __name__=='__main__':main()


def validate_analysis(result,context):
    """Keep individually cited model context views, without discarding stock reviews."""
    ids=evidence_ids(context);companies={c['symbol']:c for c in context.get('companies',[])}
    output=dict(macro_review=None,company_outlooks=[],issues=[])
    def cited(item,required=None):
        refs=item.get('evidence_ids')
        return isinstance(refs,list) and bool(refs) and all(isinstance(i,str) and i in ids for i in refs) and (required is None or any(i in required for i in refs))
    macro=result.get('macro_review')
    macro_ids={s['evidence_id'] for s in context.get('macro',{}).get('sources',[])}
    if isinstance(macro,dict) and isinstance(macro.get('assessment'),str) and macro['assessment'].strip() and cited(macro,macro_ids):output['macro_review']=macro
    elif macro is not None:output['issues'].append('宏观解读缺少有效原文引用，未采纳')
    seen=set()
    raw=result.get('company_outlooks',[])
    for item in raw if isinstance(raw,list) else []:
        if not isinstance(item,dict) or not isinstance(item.get('symbol'),str) or item['symbol'] not in companies or item['symbol'] in seen:
            output['issues'].append('公司前景代码未知或重复，未采纳');continue
        symbol=item['symbol'];seen.add(symbol);company=companies[symbol]
        refs={d['evidence_id'] for d in company.get('documents',[])}
        if company.get('profile',{}).get('evidence_id'):refs.add(company['profile']['evidence_id'])
        if not isinstance(item.get('business_summary'),str) or not item['business_summary'].strip() or not cited(item,refs):output['issues'].append(symbol+'业务概述缺少本公司证据，未采纳');continue
        if any(not isinstance(item.get(k),list) or not item[k] or any(not isinstance(v,str) or not v.strip() for v in item[k]) for k in ('drivers','risks')):output['issues'].append(symbol+'前景驱动或风险结构无效');continue
        scenarios=item.get('scenarios')
        if not isinstance(scenarios,list) or len(scenarios)!=3 or any(not isinstance(s,dict) or not isinstance(s.get('case'),str) for s in scenarios) or {s['case'] for s in scenarios}!={'optimistic','base','pessimistic'}:output['issues'].append(symbol+'缺少完整三种情景');continue
        if any(any(not isinstance(s.get(k),list) or not s[k] or any(not isinstance(v,str) for v in s[k]) for k in ('assumptions','watch_items')) for s in scenarios):output['issues'].append(symbol+'情景缺少成立条件或跟踪指标');continue
        output['company_outlooks'].append(item)
    return output

"""Shareable PDF with embedded Chinese font; no secrets or local paths in the PDF."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape
from .storage import PROJECT

PDF_NAME='行业研究周报.pdf'

def export_report(source,output=None):
    source=Path(source).resolve();output=Path(output or source.parent/PDF_NAME).resolve()
    if importlib.util.find_spec('reportlab'):
        create_pdf(source,output)
    else:
        config=json.loads((PROJECT/'configs/pdf.json').read_text())
        python=Path(config['python'])
        if not python.is_file():raise RuntimeError('PDF中文排版运行环境不可用；HTML报告仍可查看')
        result=subprocess.run([str(python),'-B','-m','stock_selector.pdf_export','--input',str(source),'--output',str(output),'--worker'],cwd=PROJECT,capture_output=True,text=True,timeout=120)
        if result.returncode:raise RuntimeError('PDF排版未完成；请查看运行环境和字体配置')
    if not output.is_file() or output.stat().st_size<1000:raise RuntimeError('PDF文件未完整生成')
    return output


def create_pdf(source,output):
    from reportlab.pdfgen import canvas
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak,KeepTogether
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from .model_review import finalize,LABELS
    from .research_report import plain_ai
    report=json.loads(Path(source).read_text())
    pipeline=report.get('decision_pipeline') or finalize(report)
    config=json.loads((PROJECT/'configs/pdf.json').read_text());font=Path(config['font'])
    if not font.is_file():raise RuntimeError('缺少中文字体')
    if 'ReportCJK' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('ReportCJK',str(font)))
        pdfmetrics.registerFontFamily('ReportCJK',normal='ReportCJK',bold='ReportCJK',italic='ReportCJK',boldItalic='ReportCJK')
    navy=colors.HexColor('#12324E');teal=colors.HexColor('#086A78');grey=colors.HexColor('#54657A');light=colors.HexColor('#EDF3F8')
    styles={
        'title':ParagraphStyle('Title',fontName='ReportCJK',fontSize=24,leading=34,textColor=navy,spaceAfter=12,keepWithNext=True),
        'h1':ParagraphStyle('Heading',fontName='ReportCJK',fontSize=16,leading=24,textColor=navy,spaceBefore=16,spaceAfter=9,keepWithNext=True),
        'h2':ParagraphStyle('Subheading',fontName='ReportCJK',fontSize=12,leading=19,textColor=teal,spaceBefore=11,spaceAfter=6,keepWithNext=True),
        'body':ParagraphStyle('Body',fontName='ReportCJK',fontSize=10,leading=17,spaceAfter=7,wordWrap='CJK'),
        'small':ParagraphStyle('Small',fontName='ReportCJK',fontSize=8.2,leading=13,textColor=grey,spaceAfter=5,wordWrap='CJK'),
        'cell':ParagraphStyle('Cell',fontName='ReportCJK',fontSize=8.3,leading=13,wordWrap='CJK'),
        'head':ParagraphStyle('Head',fontName='ReportCJK',fontSize=8.4,leading=13,textColor=colors.white,wordWrap='CJK')}
    story=[]
    def p(text,style='body'):
        return Paragraph(escape(str(text)).replace('\n','<br/>'),styles[style])
    def add(text,style='body'):story.append(p(text,style))
    def table(headers,rows,widths):
        content=[[p(v,'head') for v in headers]]+[[p(v,'cell') for v in row] for row in rows]
        t=Table(content,colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),navy),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,light]),('LINEBELOW',(0,0),(-1,0),.5,navy),('LINEBELOW',(0,1),(-1,-1),.3,colors.HexColor('#DAE3EC'))]))
        story.append(t);story.append(Spacer(1,9))
    def money(v):return '未取得' if v is None else f'{v/1e8:+.2f}'
    asof=report.get('asof','未知');source_time=report.get('data_times',{}).get('last') or '未知'
    sectors=report.get('sectors',[]);by_sector={s['code']:s for s in sectors};candidates=report.get('fundamental_candidates',[])
    picks=report.get('stock_recommendations',{}).get('stocks',[]);by_pick={s['symbol']:s for s in picks}
    scope=report.get('enrichment_scope',{})
    add('行业轮动与公司研究周报','title')
    horizon=report.get('investment_horizon')
    add('分享版 | 预计持有 '+(horizon['label'] if horizon else '3-6个月'),'h2')
    if horizon:
        add(horizon['focus'],'small')
        add(horizon['validation'],'small')
    add(f'报告生成：{asof}\n资金源时间：{source_time}\n财报期：{scope.get("period") or "未知"}','small')
    add('本期结论','h1')
    counts=pipeline['counts']
    add('量化规则负责找候选，大模型逐只反向审查。'+ '；'.join(LABELS[k]+str(counts.get(k,0))+'只' for k in LABELS)+'。')
    add(pipeline.get('availability_note',''))
    approved=[d['name']+'（'+d['symbol']+'）' for d in pipeline['decisions'] if d['verdict']=='approve_conditional']
    add('最终有条件通过名单：'+('、'.join(approved) if approved else '暂无。待复核、暂缓或计划不完整的股票不进入买入计划。'))
    add(f'覆盖 {len(sectors)} 个板块；公开利润表 {scope.get("statements",{}).get("RPT_DMSK_FN_INCOME",0)} 家；财务增长线索 {len(candidates)} 条。')
    add('资金流入与流出','h1')
    ranked=sorted([s for s in sectors if s.get('net_10d_yuan') is not None],key=lambda s:-s['net_10d_yuan'])
    flowrows=[]
    for direction,subset in [('流入居前',ranked[:3]),('流出居前',list(reversed(ranked[-3:])) if ranked else [])]:
        for s in subset:flowrows.append([direction,s['name'],money(s.get('net_5d_yuan')),money(s.get('net_10d_yuan'))])
    if flowrows:table(['方向','板块','5日净额 / 亿元','10日净额 / 亿元'],flowrows,[70,174,123,132])
    add('板块可能存在父子层级重叠，资金净额不能直接加总。源时间与报告生成时间不同；过期报告只供历史研究。','small')
    from .independent_flow import paragraphs as independent_paragraphs
    independent_lines=independent_paragraphs(report)
    if independent_lines:
        add('独立来源：同花顺行业资金观察','h1')
        for line in independent_lines:add(line)
    add('优先研究方向与行业内选股','h1')
    add('下列全部优先行业构成主名单范围；行业外公司只作补充观察，不进入本期股票与资金主方案。','small')
    if report.get('industry_scope_note'):add(report['industry_scope_note'],'small')
    add('未入选原因可由同一家公司重复命中，原因数量不能加总为公司总数。','small')
    audits={row['code']:row for row in report.get('stock_recommendations',{}).get('industry_audit',[])}
    for code in report.get('research_priorities',[]):
        industry_block_start=len(story)
        s=by_sector[code];f=s.get('financials',{})
        detail=f"{s['name']}：{s.get('research_state','资金观察')}；10日净额 {money(s.get('net_10d_yuan'))} 亿元。"
        if f.get('disclosed_companies'):detail+=f" 已披露 {f['disclosed_companies']} 家，收入利润双增长占比 {f.get('growth_breadth',0)*100:.1f}%。"
        add(detail)
        a=audits.get(code)
        if a:add(a['status']+'：'+a['explanation'],'small')
        story[industry_block_start:]=[KeepTogether(story[industry_block_start:])]
    observation=[by_sector[c]['name'] for c in report.get('market_observation_priorities',[]) if c in by_sector]
    if observation:add('仅作市场观察、暂不作为选股方向：'+'、'.join(observation)+'。直接匹配的可比财报不足3家。','small')
    story.append(PageBreak())
    add('量化初筛与大模型复核','title')
    add('初筛分数来自透明规则评分器，尚不是训练后的收益预测模型。模型复核是对所提供证据的审查，不代表外部事实已独立核实。','small')
    rows=[]
    for d in pipeline['decisions']:rows.append([d['name']+'\n'+d['symbol'],d.get('industry_name','未知'),f"{d['small_model_score']:.1f}",d['label'],d['final_action']])
    if rows:table(['股票','所属优先行业','初筛分','大模型状态','最终处理'],rows,[115,95,45,110,134])
    for d in pipeline['decisions']:
        block_start=len(story)
        add(d['name']+'（'+d['symbol']+'）','h2')
        c=by_pick.get(d['symbol'],{});q=c.get('quote',{})
        if q:add(f"参考行情 {q.get('price',0):.2f}元；供应商PE {q.get('pe_vendor',0):.2f}倍；行情时间 {q.get('quote_at','未知')}。",'small')
        add('初筛依据：'+'；'.join(d['small_model_reasons']))
        r=d.get('review')
        if not r:
            add('大模型：'+d.get('pending_reason','本期尚无匹配当前证据的逐股复核。'))
            old=d.get('historical_review')
            if old:add('上次模型意见（历史参考，'+old['date'][:19].replace('T',' ')+'）：'+old['verdict']+'。'+old['reason']+' 本期新资料尚未复核，不进入本期买入计划。')
            from .scenario_analysis import paragraphs
            for title,text in paragraphs(d['scenario_analysis']):add(title+'：'+text)
            continue
        add('模型意见：'+d['label']+'。'+str(r.get('reason','未提供')))
        add('反方论据：'+str(r.get('counter_case','未提供')))
        if d.get('validation_issue'):add('计划检查：'+d['validation_issue'],'small')
        blockers=r.get('blockers')
        if isinstance(blockers,list) and blockers:add('待解决：'+'；'.join(str(x) for x in blockers))
        from .scenario_analysis import paragraphs
        for title,text in paragraphs(d['scenario_analysis']):add(title+'：'+text)
        if d['verdict']!='approve_conditional':continue
        plan=r['buy_plan'];add('开始条件：'+plan['entry_condition'])
        if d['verdict']=='approve_conditional':
            add('分批比例以另行确定的该股计划金额为基数，不是总资产：','small')
            for i,t in enumerate(plan['tranches'],1):add(f"第{i}批 {t['fraction']*100:.0f}%：{t['condition']}")
        add('取消条件：'+plan['cancel_condition'])
        add('退出或复核条件：'+plan['exit_review_condition'])
        # Longer research and scenario sections may span pages.
    story.append(Spacer(1,10))
    if report.get('capital_settings'):story.append(PageBreak())
    add('按计划资金分配','h1')
    from .capital_allocation import sections as allocation_sections
    for text in allocation_sections(report):add(text)
    add('优先行业以外：补充观察','h1')
    add('以下公司不属于主名单，未纳入本期逐股复核与资金配置。','small')
    supplementary=report.get('stock_recommendations',{}).get('supplementary_stocks',[])
    for c in supplementary:add(c['name']+'（'+c['symbol']+'） · '+c['industry_name']+' · 初筛分 '+str(c['recommendation_score']))
    if not supplementary:add('本期暂无通过门槛且有有效行情的外围观察公司。','small')
    add('公司财务线索与综合解读','title')
    add('以下为盈利且收入、利润同比增长的研究线索，并非已获大模型通过的买入名单。财务数据为报告期累计同比，不是单季或TTM。','small')
    rows=[]
    for c in candidates[:12]:
        cash='另评' if c.get('cash_profit_ratio') is None else f"{c['cash_profit_ratio']:.2f}"
        rows.append([c['name']+'\n'+c['symbol'],c['industry_name'],f"{c['revenue_growth_pct']:+.1f}%",f"{c['profit_growth_pct']:+.1f}%",cash])
    if rows:table(['公司','行业','营收同比','利润同比','现金流/利润'],rows,[143,116,80,80,80])
    for title,text in plain_ai(report):add(title,'h2');add(text)
    from .context_report import sections as context_sections
    context_blocks=context_sections(report)
    if context_blocks:
        story.append(Spacer(1,10))
        add('宏观环境与公司发展前景','title')
        add('官方数据、供应商整理、管理层表述和模型推断分别标明；情景仅为条件研究，不是概率或收益承诺。','small')
        for block in context_blocks:
            add(block['title'],'h1')
            for paragraph in block['paragraphs']:add(paragraph)
            for source in block['sources']:
                from html import escape as html_escape
                label='来源：'+source['title']+'；日期：'+source['date']+' '
                markup=escape(label)+'<link href="'+html_escape(source['url'],quote=True)+'" color="#086A78">查看原文</link>'
                story.append(Paragraph(markup,styles['small']))
    from .research_lab import paragraphs as lab_paragraphs
    if report.get('research_lab'):
        add('增强量化研究与数据审计','h1')
        for paragraph in lab_paragraphs(report):add(paragraph)
        from .research_lab import priority_rows
        factor_rows=[r for r in priority_rows(report) if any(r.get(k) is not None for k in ('flow_5d','flow_10d','excess_20d'))]
        if factor_rows:
            data=[]
            for r in factor_rows:data.append([r['name']]+['—' if r.get(k) is None else f"{r[k]*100:+.2f}%" for k in ('flow_5d','flow_10d','excess_20d')])
            table(['优先行业','5日净额/成交额','10日净额/成交额','20日相对沪深300'],data,[145,115,115,124])
            add('原始因子并行观察，不是新增模型通过名单；行业层级尚未核验时不跨层级给增强排名。','small')
    add('来源与使用边界','h1')
    add('公开来源：东方财富板块资金及季报数据库；腾讯股票行情及沪深300序列。现金流与归母利润口径存在差异，供应商PE尚未交叉核实为TTM；交易前需核对公告及ST/停牌状态。')
    add('本报告用于研究和沟通，不自动交易。分批方案仅对模型有条件通过且触发条件达成的股票适用。当前尚未完成63/126交易日样本外检验，不能把规则分或模型意见解释为上涨概率。')
    add('数据页面：https://data.eastmoney.com/bbsj/lrb.html；行情来源：https://finance.qq.com/','small')
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.weekly-',suffix='.pdf',dir=output.parent);os.close(fd)
    def decorate(c,doc):
        w,h=A4;c.saveState();c.setStrokeColor(colors.HexColor('#D7E1EB'));c.line(48,h-38,w-48,h-38)
        c.setFont('ReportCJK',8);c.setFillColor(grey);c.drawString(48,h-28,'股票研究助手 | 分享版')
        c.drawString(48,27,'资金源 '+source_time[:10]+' | 计划不等于交易指令');c.drawRightString(w-48,27,str(doc.page));c.restoreState()
    try:
        doc=SimpleDocTemplate(tmp,pagesize=A4,rightMargin=48,leftMargin=48,topMargin=53,bottomMargin=47,title='行业轮动与公司研究周报',author='股票研究助手')
        doc.build(story,onFirstPage=decorate,onLaterPages=decorate)
        os.replace(tmp,output)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def main():
    p=argparse.ArgumentParser(description='导出适合分享的中文PDF')
    p.add_argument('--input',type=Path,default=PROJECT/'reports/sector_research/research.json');p.add_argument('--output',type=Path);p.add_argument('--worker',action='store_true')
    a=p.parse_args();output=a.output or a.input.parent/PDF_NAME
    if a.worker:create_pdf(a.input,output)
    else:export_report(a.input,output)
    print(str(output))

if __name__=='__main__':main()

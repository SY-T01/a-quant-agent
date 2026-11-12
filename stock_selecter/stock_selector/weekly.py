"""Weekly runner: tests, archive, refresh/enrich, compare, archive. No trading or self-tuning."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import fcntl
from pathlib import Path
from datetime import datetime
from .storage import PROJECT,now


def compare(previous,current):
    old={r['code']:r for r in previous.get('sectors',[])};changes=[]
    for rank,r in enumerate(current.get('sectors',[]),1):
        p=old.get(r['code'])
        if not p: continue
        changes.append(dict(code=r['code'],name=r['name'],rank=rank,state=r.get('research_state'),previous_state=p.get('research_state'),
                            net_10d_change_yuan=(r['net_10d_yuan']-p['net_10d_yuan']) if r.get('net_10d_yuan') is not None and p.get('net_10d_yuan') is not None else None))
    def source_time(r):
        try:return datetime.fromisoformat(r.get('data_times',{}).get('last'))
        except (ValueError,TypeError):return None
    old_time,new_time=source_time(previous),source_time(current)
    newer=bool(new_time and (not old_time or new_time>old_time))
    regressed=bool(old_time and new_time and new_time<old_time)
    old_picks={r['symbol'] for r in previous.get('stock_recommendations',{}).get('stocks',[])}
    new_picks={r['symbol'] for r in current.get('stock_recommendations',{}).get('stocks',[])}
    old_final=set(previous.get('decision_pipeline',{}).get('final_buy_candidates',[]))
    new_final=set(current.get('decision_pipeline',{}).get('final_buy_candidates',[]))
    return dict(final_buy_added=sorted(new_final-old_final),final_buy_removed=sorted(old_final-new_final),shortlist_added=sorted(new_picks-old_picks),shortlist_removed=sorted(old_picks-new_picks),shortlist_count=len(new_picks),previous_source=previous.get('data_times',{}).get('last'),current_source=current.get('data_times',{}).get('last'),
                previous_horizon=previous.get('investment_horizon',{}).get('label','3—6个月'),current_horizon=current.get('investment_horizon',{}).get('label','3—6个月'),horizon_changed=previous.get('investment_horizon')!=current.get('investment_horizon'),
                new_source_data=newer,source_regressed=regressed,
                comparisons=changes,companies_before=len(previous.get('fundamental_candidates',[])),companies_after=len(current.get('fundamental_candidates',[])))


def acquire_lock(path):
    """OS lock is released on process exit; leftover files cannot block future runs."""
    handle=Path(path).open('a+')
    try:
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close();raise RuntimeError('已有周更新任务运行，本次未重复启动') from None
    handle.seek(0);handle.truncate();handle.write(str(os.getpid()));handle.flush()
    return handle


def run(project=PROJECT,refresh=True):
    project=Path(project).resolve()
    if not os.path.ismount('/Volumes/tsy_macmini') or not project.is_dir():
        raise RuntimeError('外置磁盘未挂载，停止运行；不会在系统盘创建替代数据目录')
    runid=now().replace(':','').replace('+','_')
    out=project/'reports'/'weekly'/runid;out.mkdir(parents=True,exist_ok=False)
    lock=project/'data'/'weekly.lock'
    handle=acquire_lock(lock)
    try:
        src=project/'reports'/'sector_research';before={}
        if (src/'research.json').exists(): before=json.loads((src/'research.json').read_text())
        if src.exists(): shutil.copytree(src,out/'before')
        # Cached raw data references are content-addressed; retain the exact pre-run manifest too.
        for name in ('snapshot.json','enrichment.json','recommendation_quotes.json'):
            p=project/'data'/'normalized'/'sector_research'/name
            if p.exists():
                (out/'manifests').mkdir(exist_ok=True);shutil.copy2(p,out/'manifests'/name)
        context_manifest=project/'data/normalized/context_research/context.json'
        if context_manifest.exists():
            (out/'manifests').mkdir(exist_ok=True);shutil.copy2(context_manifest,out/'manifests/context.json')
        for relative in ('configs/research_lab.json','data/research/market_evidence.json'):
            source=project/relative
            if source.exists():
                (out/'manifests').mkdir(exist_ok=True);shutil.copy2(source,out/'manifests'/source.name)
        print('步骤1/4：检查程序并归档当前报告…',flush=True)
        tests=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','tests','-q'],cwd=project,capture_output=True,text=True,timeout=180)
        (out/'tests.txt').write_text(tests.stdout+tests.stderr,encoding='utf-8')
        if tests.returncode: raise RuntimeError('回归测试未通过，未更新正式报告；日志已保存')
        staged=out/'staged'
        command=[sys.executable,'-B','-m','stock_selector.sector_research','--out',str(staged)]
        if refresh:command+=['--refresh','--enrich']
        # Only scheduled live updates may use configured credentials; cached tests never incur AI calls.
        if refresh and os.environ.get('DEEPSEEK_API_KEY'):command+=['--ai']
        print('步骤2/4：更新数据，按优先行业筛选公司；如已配置密钥，将在本次调用中生成AI分析。请稍候…' if refresh else '步骤2/4：使用本地缓存按优先行业重筛；本次不联网、不调用付费API。',flush=True)
        result=subprocess.run(command,cwd=project,capture_output=True,text=True,timeout=1800)
        (out/'update.txt').write_text(result.stdout+result.stderr,encoding='utf-8')
        if result.returncode:raise RuntimeError('数据或报告任务未完成，请查看周目录update.txt')
        print('步骤3/4：检查新报告、行业归属与资金方案…',flush=True)
        current=json.loads((staged/'research.json').read_text());diff=compare(before,current)
        if diff['source_regressed']:raise RuntimeError('数据源时间倒退，未发布新报告；保留当前正式报告')
        from .publication import publish
        publish(staged,src,out/'previous-published')
        limitations=current.get('limitations',[])
        fallback=any('本次更新失败' in line for line in limitations)
        diff['status']='cached_after_refresh_failure' if fallback else 'updated' if diff['new_source_data'] else 'no_new_sector_snapshot'
        shutil.copytree(src,out/'after')
        (out/'comparison.json').write_text(json.dumps(diff,ensure_ascii=False,indent=2),encoding='utf-8')
        summary=['# 每周更新与迭代记录','',f'运行时间：{now()}。',f'资金快照状态：{diff["status"]}。',f'上次源时间：{diff["previous_source"]}；本次源时间：{diff["current_source"]}。',f'公司财务线索：{diff["companies_before"]} → {diff["companies_after"]}。','','## 本次可验证改进','','已运行回归测试、更新数据覆盖、记录行业方向变化并归档报告。未自动调整评分权重；未运行交易。公开接口故障、数据覆盖和后续修复事项见after/research.json与update.txt。']
        pipeline=current.get('decision_pipeline',{})
        summary+=['','## 两级模型复核结果','', '本期规则初筛 '+str(len(pipeline.get('decisions',[])))+' 只；大模型有条件通过 '+str(pipeline.get('counts',{}).get('approve_conditional',0))+' 只，暂缓 '+str(pipeline.get('counts',{}).get('defer',0))+' 只，否决 '+str(pipeline.get('counts',{}).get('reject',0))+' 只，待复核 '+str(pipeline.get('counts',{}).get('pending',0))+' 只；计划待补全 '+str(pipeline.get('counts',{}).get('plan_incomplete',0))+' 只，返回待核实 '+str(pipeline.get('counts',{}).get('invalid',0))+' 只。逐股分歧和买入分批计划见 after/研究周报.md。']
        summary+=['','预计持有：'+diff['current_horizon']+'；上期：'+diff['previous_horizon']+'。期限变更可能改变名单与排序，不应归因于市场表现。']
        capital=current.get('capital_settings')
        if capital:
            summary+=['','## 计划资金分配','', '本次新增资金 '+capital['budget_yuan']+' 元，'+capital['label']+'风格；资金方案状态：'+current.get('capital_allocation',{}).get('status','未生成')+'。当前条件方案与未来备选方案是同一笔资金的不同情景，不能相加投入；金额、现金与分批条件见正式报告。']
        context=current.get('research_context',{})
        lab=current.get('research_lab',{})
        if lab:
            summary+=['','## 增强量化研究与前瞻跟踪','',lab.get('mode','')+'；数据覆盖见after/research_lab.json与行业增强因子.csv。增强模型未自动替代主名单，未宣称完成多年样本外验证。']
            for row in current.get('forward_validation',{}).get('horizons',[]):summary.append(f"前瞻{row['horizon']}交易日：已测{row['measured']}，等待{row['pending']}，受阻{row['blocked']}。")
        summary+=['','## 宏观与公司资料更新','', '已接入官方宏观指标 '+str(len(context.get('macro',{}).get('indicators',[])))+' 项，候选公司业务资料 '+str(len(context.get('companies',[])))+' 家，原始公告 '+str(sum(len(c.get('documents',[])) for c in context.get('companies',[])))+' 份。具体发布日期、行业传导与三种发展情景见 after/研究周报.md。']
        summary += ['', '## 小模型初筛候选变化', '', '本期候选数量：'+str(diff['shortlist_count'])+'；新入选：'+('、'.join(diff['shortlist_added']) or '无')+'；移出：'+('、'.join(diff['shortlist_removed']) or '无')+'。具体股票、价格时间与买入前提见after/index.html。']
        (out/'更新与迭代记录.md').write_text('\n'.join(summary),encoding='utf-8')
        print('步骤4/4：报告已保存，历史版本已归档。',flush=True)
        if (current.get('ai') or {}).get('error'):
            print('数据与规则筛选已更新，但本次DeepSeek解读未完成：'+current['ai']['error'],flush=True)
            print('稍后可用菜单4仅重试解读，无需再次抓取数据；未自动重复调用。',flush=True)
        print(json.dumps(dict(weekly_report=str(out),status=diff['status'],company_signals=diff['companies_after']),ensure_ascii=False))
        return out
    except Exception as e:
        from .storage import atomic_json
        atomic_json(out/'failure.json',dict(status='failed',error=str(e),type=type(e).__name__,reported_at=now()))
        raise
    finally:
        fcntl.flock(handle.fileno(),fcntl.LOCK_UN);handle.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--cached',action='store_true',help='只以本地缓存验证周报流水线，不联网')
    a=p.parse_args();run(refresh=not a.cached)

if __name__=='__main__':main()

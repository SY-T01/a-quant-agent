"""Offline operational checks. Research readiness is separate from strategy validity."""
import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from .storage import PROJECT,now,atomic_json


def assess(report,asof=None):
    cutoff=datetime.fromisoformat(asof or report['asof']);checks=[]
    def check(name,status,detail):checks.append(dict(name=name,status=status,detail=detail))
    stamp=report.get('data_times',{}).get('last')
    try:
        ts=datetime.fromisoformat(stamp)
        age=(cutoff-ts).total_seconds()/86400
        valid=ts.tzinfo is not None and 0<=age<=7
        check('板块数据时间','pass' if valid else 'fail',f'源时间 {stamp}，距评估时点 {age:.1f} 天')
    except (ValueError,TypeError):check('板块数据时间','fail','缺少有效源时间')
    scope=report.get('enrichment_scope',{})
    counts=scope.get('statements',{})
    enough=counts.get('RPT_DMSK_FN_INCOME',0)>0 and counts.get('RPT_DMSK_FN_CASHFLOW',0)>0
    check('公司公开财报','pass' if enough else 'fail',f"利润表 {counts.get('RPT_DMSK_FN_INCOME',0)} 家；现金流表 {counts.get('RPT_DMSK_FN_CASHFLOW',0)} 家")
    coverage=report.get('stock_recommendations',{}).get('scope',{})
    quote_count=coverage.get('quotes_available',0);needed=coverage.get('quote_pool',0)
    check('候选行情覆盖','pass' if quote_count==needed and needed>0 else 'warning',f'本次核验池 {needed} 只，取得行情 {quote_count} 只')
    history=scope.get('history_sectors',0)
    check('中期行业历史','pass' if history else 'warning',f'已核验 {history} 个行业；未覆盖的行业不输出中期趋势结论')
    check('投资策略验证','warning','尚未完成63/126交易日样本外检验，条件名单不能视作已验证策略')
    blocked=any(c['status']=='fail' for c in checks)
    return dict(status='暂停新增推荐' if blocked else '可用于研究，策略待验证',checks=checks,assessed_at=cutoff.isoformat(),blocked=blocked)


def inspect_project(project=PROJECT,asof=None):
    project=Path(project);path=project/'reports/sector_research/research.json'
    result=dict(project=str(project),checked_at=asof or now(),report_exists=path.exists(),external_volume_mounted=os.path.ismount('/Volumes/tsy_macmini'))
    if not path.exists():result['error']='尚未生成研究报告';return result
    try:
        report=json.loads(path.read_text());result['health']=assess(report,asof or now())
    except (ValueError,TypeError):result['error']='报告文件无效，保留原文件后重新生成';return result
    root=(project/'data/raw').resolve();refs=report.get('snapshots',[]);verified=0;issues=[]
    for ref in refs:
        p=Path(ref.get('path','')).resolve()
        if root not in p.parents:issues.append('源文件路径不在项目raw目录');continue
        if not p.is_file():issues.append('原始快照文件缺失');continue
        if hashlib.sha256(p.read_bytes()).hexdigest()!=ref.get('sha256'):issues.append('原始快照校验值不一致');continue
        verified+=1
    if issues:
        result['health']['checks'].append(dict(name='原始数据完整性',status='fail',detail='；'.join(issues)))
        result['health']['blocked']=True;result['health']['status']='原始数据完整性异常，暂停新增推荐'
    result['source_integrity']=dict(verified=verified,referenced=len(refs),issues=issues)
    return result


def main():
    result=inspect_project()
    atomic_json(PROJECT/'reports/status/health.json',result)
    print('项目自检：'+str(PROJECT))
    if result.get('error'):print(result['error']);return
    print('整体状态：'+result['health']['status'])
    for c in result['health']['checks']:print(c['name']+'：'+c['detail'])
    integrity=result['source_integrity'];print(f"原始快照校验：{integrity['verified']}/{integrity['referenced']} 通过")
    if integrity['issues']:print('需要处理：'+'；'.join(integrity['issues']))
    print('详细结果：'+str(PROJECT/'reports/status/health.json'))

if __name__=='__main__':main()

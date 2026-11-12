"""Publish an already-generated report, rolling back if directory replacement fails."""
import json
import os
import shutil
from pathlib import Path


def publish(stage,target,backup):
    stage,target,backup=map(Path,(stage,target,backup))
    report=json.loads((stage/'research.json').read_text())
    picks=report.get('stock_recommendations',{})
    if picks.get('selection_policy')=='industry-first-v1':
        focus=set(report.get('research_priorities',[]))
        if any(c.get('industry_code') not in focus for c in picks.get('stocks',[])):
            raise ValueError('主名单存在优先行业以外股票，停止发布并保留原报告')
    if not report.get('sectors') or not (stage/'index.html').is_file() or not (stage/'研究周报.md').is_file():
        raise ValueError('新报告不完整，保留当前正式报告')
    if backup.exists():raise ValueError('备份目录已存在，不能覆盖历史报告')
    moved=False
    if target.exists():os.replace(target,backup);moved=True
    try:os.replace(stage,target)
    except OSError:
        if moved:os.replace(backup,target)
        raise
    return report

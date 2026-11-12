"""Isolated research jobs sharing the selector's update lock and publication rules."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import sys
import uuid

from .selector_bridge import engine


@contextmanager
def research_lock(root):
    import fcntl
    path = Path(root) / "data/weekly.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("已有选股更新或研究任务运行，请完成后再操作") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def invoke(module, args=()):
    saved = sys.argv
    sys.argv = [module.__name__, *args]
    try:
        module.main()
    finally:
        sys.argv = saved


def generate(root, action, load):
    """Generate into a fresh folder; keep the published report on any failure."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    archive = root / "reports/desktop_runs" / (stamp + "-" + uuid.uuid4().hex[:8])
    stage = archive / "staged"
    archive.mkdir(parents=True)
    flags = {"rescreen": [], "ai": ["--ai"], "replay": ["--replay-ai"]}[action]
    invoke(load("stock_selector.sector_research"), ["--out", str(stage), *flags])
    report = json.loads((stage / "research.json").read_text())
    # sector_research records API failures in JSON while exiting successfully.
    if action == "ai" and (report.get("ai") or {}).get("error"):
        raise ValueError("本次模型复核未完成，已有报告保留；详情见本次日志与暂存报告")
    target = root / "reports/sector_research"
    if (target / "research.json").exists():
        previous = json.loads((target / "research.json").read_text())
        if load("stock_selector.weekly").compare(previous, report)["source_regressed"]:
            raise ValueError("数据源时间倒退，已保留原报告")
    # Finish the directory swap before responding to a request to stop.
    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
    try:
        load("stock_selector.publication").publish(stage, target, archive / "previous-published")
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
    print("研究报告已发布；上一版保存在本次归档。", flush=True)


def run(root, action):
    root = Path(root).resolve()
    load = engine(root)
    os.chdir(root)
    if action not in ("ai", "full"):
        os.environ.pop("DEEPSEEK_API_KEY", None)
    if action in ("update", "full"):
        # The weekly runner acquires this same lock and archives/tests/publishes.
        archive = load("stock_selector.weekly").run()
        report = json.loads((root / "reports/sector_research/research.json").read_text())
        if action == "full" and (report.get("ai") or {}).get("error"):
            raise ValueError("数据已更新，但模型复核未完成；可单独重试模型复核")
        print("周更新归档：" + str(archive), flush=True)
        return
    with research_lock(root):
        if action in ("rescreen", "ai", "replay"):
            generate(root, action, load)
        else:
            module = {"health": "health", "lab": "research_lab", "validation": "forward_validation",
                      "pdf": "pdf_export"}[action]
            invoke(load("stock_selector." + module))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--action", required=True,
                        choices=("update", "full", "rescreen", "ai", "replay", "health", "lab", "validation", "pdf"))
    args = parser.parse_args()
    try:
        run(args.project, args.action)
    except (OSError, ValueError, RuntimeError) as exc:
        # Never echo credentials or request headers from a nested exception.
        print("研究任务未完成：" + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

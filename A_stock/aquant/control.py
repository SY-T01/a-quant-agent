"""User-owned foreground controls; diagnostics and previews never use network."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .domain import SHANGHAI, ValidationError
from .lifecycle import WatchLock, active, request_stop


def configuration(path):
    from .watch import check_config
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    check_config(cfg)
    return cfg


def status(cfg):
    root = Path(cfg["state_dir"])
    info = active(root)
    lines = [f"自动任务：{'运行中' if info else '已停止'}", f"发送时间：交易日 {cfg['schedule'][0]}（Asia/Shanghai）",
             "数据能力：已完成日线；14:00报告不包含当日盘中行情"]
    cooldown = root / "data" / "eastmoney-cooldown.json"
    if cooldown.exists():
        try:
            until = datetime.fromisoformat(json.loads(cooldown.read_text())["retry_after"])
            if until > datetime.now(SHANGHAI):
                lines.append(f"东方财富主源冷却中：北京时间{until.astimezone(SHANGHAI):%m-%d %H:%M}前不再请求；历史行情会尝试独立腾讯备用源")
        except (ValueError, TypeError, KeyError):
            lines.append("行情源冷却记录异常，请检查本地文件")
    if info:
        lines.append(f"本实例PID：{info.get('pid', '初始化中')}；模式：{info.get('mode', '初始化中')}")
    from .sources import pool_status
    lines.extend(pool_status(root / "data"))
    dbpath = root / "watch.sqlite"
    if dbpath.exists():
        with sqlite3.connect(dbpath.resolve().as_uri() + "?mode=ro", uri=True) as db:
            rows = db.execute("SELECT status,updated_at,error FROM jobs ORDER BY updated_at DESC LIMIT 3").fetchall()
        labels = {"accepted": "SMTP已接受（不代表保证收件）", "unknown": "发送结果未知，核对邮箱后处理",
                  "running": "处理中；若任务已停止可用watch-recover恢复",
                  "sending": "进入发送阶段；若中断则按结果未知处理", "failed": "未发信阶段失败，可检查后重试",
                  "preview": "仅本地预览"}
        for state, at, error in rows:
            lines.append(f"任务记录：{labels.get(state, state)}；{at}")
            if error:
                lines.append(f"原因：{error}")
    else:
        lines.append("尚无自动发信记录")
    return "\n".join(lines)


def doctor(path):
    """Return local readiness; don't print secrets, address values or holdings."""
    from .watch import credentials
    lines = []
    try:
        cfg = configuration(path)
    except (OSError, ValueError, TypeError, KeyError):
        return "配置不可用，请先运行watch-setup或检查配置字段"
    lines += ["配置格式：通过", f"发送时间：{cfg['schedule'][0]}"]
    for name in ("akshare", "pandas", "baostock"):
        lines.append(f"{name}：{'已安装' if importlib.util.find_spec(name) else '未安装'}")
    try:
        values = credentials(cfg)
        keys = ("DEEPSEEK_API_KEY", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO")
        lines.append("模型及邮箱字段：" + ("齐全，尚不代表联网联调成功" if all(values.get(k) for k in keys) else "不完整"))
    except (OSError, ValueError, KeyError):
        lines.append("本机凭据不可读或权限异常，请检查密钥文件")
    lines += ["实盘适用性：未验收；缺少实时行情、净值溢价和独立策略收益验证",
              "本次自检未联网、未调用DeepSeek、未发信", status(cfg)]
    return "\n".join(lines)


def recover(cfg):
    """Recover a killed process only after obtaining its exclusive ownership lock."""
    root = Path(cfg["state_dir"])
    with WatchLock(root, "recover"):
        dbpath = root / "watch.sqlite"
        if not dbpath.exists():
            return "没有需要恢复的任务"
        with sqlite3.connect(dbpath) as db:
            failed = db.execute("UPDATE jobs SET status='failed',error='用户恢复中断分析，未进入发信' WHERE status='running'").rowcount
            unknown = db.execute("UPDATE jobs SET status='unknown',error='发送阶段进程中断，须核对邮箱' WHERE status='sending'").rowcount
    return f"已恢复{failed}项中断分析；{unknown}项发信中断标为结果未知；不会重发accepted或unknown邮件"


def offline_preview(cfg):
    from .etf import summarize
    from .positions import Positions
    from .watch import packet_for, format_daily, review_synthesis
    import pandas as pd
    root = Path(cfg["state_dir"])
    items = []
    for instrument in cfg["instruments"]:
        query, kind = instrument["query"], instrument["type"]
        catalog = root / "data" / f"catalog-{kind}.json"
        names = json.loads(catalog.read_text())["names"] if catalog.exists() else {}
        code = query if query.isdigit() and len(query) == 6 else next((c for c, n in names.items() if n == query), None)
        if not code:
            raise ValidationError("离线目录中无法确定标的代码，请使用准确代码")
        paths = [root / "data" / f"{kind}-{code}.qfq.csv"]
        if kind == "etf":
            paths.append(Path("runtime/etf") / f"{code}.daily.qfq.csv")
        path = next((p for p in paths if p.exists()), None)
        if path is None:
            raise ValidationError("缺少离线历史缓存；本命令不会联网下载")
        items.append({"code": code, "type": kind, "name": names.get(code, code), "stats": summarize(pd.read_csv(path))})
    now = datetime.now(SHANGHAI)
    cutoff = min(i["stats"]["last_date"] for i in items)
    packet = packet_for(items, cutoff, now)
    recs = []
    for s in packet["stocks"]:
        action = "BUY" if s["trend"] > 0 and s["momentum20"] > 0 else "SELL" if s["trend"] < 0 and s["momentum20"] < 0 else "HOLD"
        recs.append({"symbol": s["symbol"], "action": action, "reason": "离线规则演示：均线与20日动量同向才产生方向倾向",
                     "uncertainty": "未调用DeepSeek；缓存可能过期；不能用于当前交易", "evidence_ids": [s["id"]]})
    synthesis, _ = review_synthesis(packet, {"summary": "离线规则预览，未调用DeepSeek，不是最新建议。", "recommendations": recs})
    store = Positions(root)
    try:
        text = "离线旧缓存预览 · 不联网 · 不发信 · 不作为当前买卖依据\n\n" + format_daily(packet, synthesis, store.all())
    finally:
        store.close()
    output = root / "offline-preview.txt"
    output.write_text(text, encoding="utf-8")
    return output.resolve()


def set_schedule(path, value):
    from .watch import check_config
    target = Path(path)
    cfg = configuration(path)
    cfg["schedule"] = [value.strip()]
    check_config(cfg)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)


def menu(path):
    while True:
        print("\nA-Quant 个人助手\n1 查看任务状态\n2 录入成本和份数\n3 本地自检\n4 修改发送时间\n5 当前终端启动自动任务（联网/API计费/邮件）\n6 离线报告预览（不联网、不发信）\n7 请求停止自动任务\n0 退出菜单")
        choice = input("请选择：").strip()
        if choice == "0":
            return 0
        try:
            cfg = configuration(path)
            if choice == "1":
                print(status(cfg))
            elif choice == "2":
                from .positions import position_cli
                position_cli(cfg["state_dir"], "edit")
            elif choice == "3":
                print(doctor(path))
            elif choice == "4":
                set_schedule(path, input("北京时间HH:MM："))
                print("已保存；运行中的任务下次检查时读取新时间")
            elif choice == "5":
                from .watch import watch_main
                watch_main(path)
            elif choice == "6":
                print("已保存离线预览：", offline_preview(cfg))
            elif choice == "7":
                print(request_stop(cfg["state_dir"]))
            else:
                print("请选择0至7")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            print(str(exc) if isinstance(exc, ValidationError) else "本地输入或文件有误，请用本地自检检查")

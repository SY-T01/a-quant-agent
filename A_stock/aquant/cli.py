from __future__ import annotations

import argparse
import json
import sys
import sqlite3
from datetime import datetime
from pathlib import Path

from .data import demo_inputs, fetch_snapshot
from .domain import SHANGHAI, ValidationError, timestamp
from .report import send, write_report
from .service import run
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS = ROOT / "config/settings.json"
if not DEFAULT_SETTINGS.exists():
    DEFAULT_SETTINGS = Path(__file__).resolve().parent / "defaults.json"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parser():
    p = argparse.ArgumentParser(description="A-Quant 个人量化研究助手，默认不联网、不发信、不下单")
    p.add_argument("--db", default="runtime/account.sqlite")
    p.add_argument("--settings", default=str(DEFAULT_SETTINGS))
    sub = p.add_subparsers(dest="command", required=True)
    for name, help_text in (("menu", "统一操作菜单"), ("watch-status", "只读查看任务状态"),
                            ("watch-stop", "请求当前自动任务停止，不猜测进程编号"),
                            ("watch-recover", "恢复已中断分析；不重发结果未知邮件"),
                            ("doctor", "离线检查配置及运行条件"), ("offline-preview", "用本地旧缓存预览，不联网或发信")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--config", default="runtime/watch-config.json")
    positions = sub.add_parser("positions", help="录入成本和当前份数/股数，供自动邮件读取")
    positions.add_argument("action", choices=["edit", "set", "list"], nargs="?", default="edit")
    positions.add_argument("--config", default="runtime/watch-config.json")
    positions.add_argument("--code")
    positions.add_argument("--type", choices=["etf", "stock"], default="etf")
    positions.add_argument("--shares", type=int)
    positions.add_argument("--cost", help="每份/股成本，最多4位小数")
    setup = sub.add_parser("watch-setup", help="在本机交互配置自动日报及密钥")
    setup.add_argument("--config", default="runtime/watch-config.json")
    check = sub.add_parser("watch-check", help="仅验证真实数据自动检索和下载，不调用DeepSeek或邮件")
    check.add_argument("--config", default="config/watch.example.json")
    watch = sub.add_parser("watch", help="自动检索、下载、量化、DeepSeek综合并发邮件")
    watch.add_argument("--config", default="runtime/watch-config.json")
    watch.add_argument("--once", action="store_true", help="立即执行一次，忽略调度时间")
    watch.add_argument("--preview", action="store_true", help="真实DeepSeek分析，仅保存本地报告不发信")
    watch.add_argument("--retry-failed", action="store_true", help="修复问题后手动重试数据/模型失败；不重发结果未知邮件")
    demo = sub.add_parser("demo", help="在单独数据库运行虚构数据演示")
    demo.add_argument("--output", default="runtime/demo/reports")
    demo.add_argument("--mode", choices=["rules", "replay"], default="rules")
    demo.add_argument("--replay")
    imp = sub.add_parser("account-import", help="导入已核对券商快照")
    imp.add_argument("file")
    sub.add_parser("account-show")
    backtest = sub.add_parser("backtest", help="下一事件成交的最小快照回放，不是完整历史回测")
    backtest.add_argument("file", help="包含account和events的JSON")
    backtest.add_argument("--output", default="runtime/backtest.json")
    dashboard = sub.add_parser("dashboard", help="仅本机可访问的账户和报告面板")
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.add_argument("--demo", action="store_true", help="单独演示数据库")
    dashboard.add_argument("--market")
    dashboard.add_argument("--mode", choices=["rules", "deepseek"], default="rules")
    dashboard.add_argument("--allow-api", action="store_true")
    fill = sub.add_parser("fill", help="录入真实成交；不会替你执行交易")
    fill.add_argument("file")
    fill.add_argument("--version", type=int, required=True)
    confirm = sub.add_parser("confirm", help="确认该报告所有成交均已录入，或确认没有执行")
    confirm.add_argument("run_id")
    confirm.add_argument("--version", type=int, required=True)
    confirm.add_argument("--at", help="带时区时间，默认当前时间")
    for command in ("run", "tick"):
        r = sub.add_parser(command, help="手动研究运行" if command == "run" else "外部调度器每分钟调用一次")
        source = r.add_mutually_exclusive_group(required=True)
        source.add_argument("--market", help="JSON盘中快照文件")
        source.add_argument("--market-url", help="返回本项目快照格式的HTTPS适配服务")
        r.add_argument("--mode", choices=["rules", "replay", "deepseek"], default="rules")
        r.add_argument("--replay", help="冻结模型响应JSON，必须包含全部股票")
        r.add_argument("--allow-api", action="store_true", help="允许将证据包发送至DeepSeek")
        r.add_argument("--at", help="历史回放时间；设置后禁用真实API及邮件")
        r.add_argument("--slot", default="manual", help="独立报告批次，同日同批次同模式幂等")
        r.add_argument("--output", default="runtime/reports")
        r.add_argument("--send", action="store_true", help="显式启用SMTP发送")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    store = None
    try:
        cfg = load(args.settings)
        if args.command in ("menu", "watch-status", "watch-stop", "watch-recover", "doctor", "offline-preview"):
            from . import control
            if args.command == "menu":
                return control.menu(args.config)
            if args.command == "doctor":
                print(control.doctor(args.config))
            else:
                watch_cfg = control.configuration(args.config)
                if args.command == "watch-status":
                    print(control.status(watch_cfg))
                elif args.command == "watch-stop":
                    print(control.request_stop(watch_cfg["state_dir"]))
                elif args.command == "watch-recover":
                    print(control.recover(watch_cfg))
                elif args.command == "offline-preview":
                    print("离线预览已保存：", control.offline_preview(watch_cfg))
            return 0
        if args.command == "positions":
            from .positions import position_cli
            watch_cfg = load(args.config)
            position_cli(watch_cfg["state_dir"], args.action, args.code, args.type, args.shares, args.cost)
            return 0
        if args.command == "watch-setup":
            from .watch import configure
            configure(args.config)
            return 0
        if args.command == "watch-check":
            from .watch import check_data
            check_data(args.config)
            return 0
        if args.command == "watch":
            from .watch import watch_main
            return watch_main(args.config, args.once, args.preview, args.retry_failed)
        if args.command == "demo":
            # Never import synthetic account into the user's account DB.
            db_path = Path(args.output).parent / f"demo-{args.mode}.sqlite"
            store = Store(db_path)
            account, market, as_of = demo_inputs()
            try:
                store.account()
            except ValidationError:
                store.import_account(account)
            result = run(store, market, cfg, as_of, "am", args.mode, demo=True,
                         replay=load(args.replay) if args.replay else None)
            print(write_report(result, args.output).resolve())
            print(f"状态：{result['status']}；报告ID：{result['id']}")
            return 0 if result["status"] != "PAUSED" else 2
        if args.command == "backtest":
            from .backtest import replay_events
            data = load(args.file)
            result = replay_events(data["account"], data["events"], cfg, data.get("responses"))
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"回放完成：{result['trade_count']}次成交；费用{result['total_fees']:.2f}元。输出：{output.resolve()}")
            return 0
        store = Store("runtime/demo/panel.sqlite" if args.command == "dashboard" and args.demo else args.db)
        if args.command == "dashboard":
            from .dashboard import serve
            if args.demo and args.mode != "rules":
                raise ValidationError("控制台演示只允许离线规则模式")
            serve(store, cfg, args.port, args.demo, args.market, args.mode, args.allow_api)
            return 0
        if args.command == "account-import":
            result = store.import_account(load(args.file))
            print(f"已导入账户版本 {result['version']}；原建议执行情况仍需逐份confirm确认。")
        elif args.command == "account-show":
            print(json.dumps(store.account(), ensure_ascii=False, indent=2))
            print("待确认报告：", store.unresolved())
        elif args.command == "fill":
            print("已记录实际成交，账户版本", store.fill(load(args.file), args.version)["version"])
        elif args.command == "confirm":
            at = args.at or datetime.now(SHANGHAI).isoformat()
            print("已确认反馈，账户版本", store.confirm(args.run_id, args.version, at)["version"])
        else:
            now = datetime.now(SHANGHAI)
            at = timestamp(args.at) if args.at else now
            if args.at and (args.allow_api or args.send):
                raise ValidationError("指定历史时间时禁止真实API及发信，请使用冻结响应回放")
            if args.command == "tick":
                if args.at:
                    raise ValidationError("tick不接受历史时间")
                if (at.hour, at.minute) not in ((9, 40), (14, 20)):
                    print("未到报告时间，无操作")
                    return 0
                args.slot = "am" if at.hour == 9 else "pm"
            market = load(args.market) if args.market else fetch_snapshot(args.market_url)
            if args.command == "tick" and at.date().isoformat() not in market.get("trading_days", []):
                print("交易日历未确认本日开市，无操作")
                return 0
            result = run(store, market, cfg, at, args.slot, args.mode,
                         replay=load(args.replay) if args.replay else None, allow_api=args.allow_api)
            print(write_report(result, args.output).resolve())
            print(f"状态：{result['status']}；报告ID：{result['id']}")
            if args.send:
                send(store, result["id"], datetime.now(SHANGHAI))
                print("SMTP已接受邮件；不代表保证送达收件箱")
            return 2 if result["status"] == "PAUSED" else 0
        return 0
    except (ValidationError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        # Avoid echoing exception payloads from remote services or malformed private data.
        message = str(exc) if isinstance(exc, ValidationError) else "文件、配置或输入格式错误，请检查README"
        print(f"错误：{message}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\n已取消当前操作；不会启动后台任务。")
        return 130
    finally:
        if store:
            store.close()

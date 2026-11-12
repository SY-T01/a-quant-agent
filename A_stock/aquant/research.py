"""Desktop adapter for the existing industry-first stock selection application."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from .desktop_model import PROJECT, atomic_json
from .domain import SHANGHAI, ValidationError
from .selector_bridge import engine


ACTIONS = {
    "rescreen": "本地重新筛选", "update": "更新数据与周报", "ai": "DeepSeek 逐股复核",
    "full": "更新并完成模型复核", "replay": "重放已有模型结果", "health": "检查研究数据",
    "lab": "增强因子审计", "validation": "前瞻验证", "pdf": "导出研究 PDF",
}
PAID = {"ai", "full"}


class ResearchModel:
    def __init__(self, desktop, project=None):
        self.desktop = desktop
        self.project = Path(project or desktop.config().get("research_project") or PROJECT.parent / "stock_selecter").resolve()
        self.child = None
        self.stream = None
        self.action = None
        self.message = "尚未运行研究任务。"
        self.log_path = desktop.root() / "research/desktop-research.log"
        self.stop_requested = None

    @property
    def preferences_path(self):
        return self.project / "configs/research_preferences.json"

    @property
    def report_path(self):
        return self.project / "reports/sector_research/research.json"

    def preferences(self):
        load = engine(self.project)
        return {"horizon": load("stock_selector.investment_horizon").load(self.preferences_path),
                "capital": load("stock_selector.capital_allocation").load(self.preferences_path)}

    def save_preferences(self, months, budget, style):
        from .research_worker import research_lock
        if self.busy:
            raise ValidationError("请等待当前研究任务完成后再修改偏好")
        load = engine(self.project)
        try:
            if not re.fullmatch(r"[0-9]{1,2}", str(months)):
                raise ValueError("预计持有时间请输入1至24之间的整月数")
            horizon = load("stock_selector.investment_horizon").profile(int(months))
            capital_module = load("stock_selector.capital_allocation")
            capital = capital_module.settings(capital_module.parse_amount(budget), style) if budget.strip() else None
            # Validate all inputs first; one replacement preserves other preference fields.
            with research_lock(self.project):
                data = json.loads(self.preferences_path.read_text()) if self.preferences_path.exists() else {}
                data["holding_months"] = horizon["months"]
                if capital:
                    data.update(capital_yuan=capital["budget_yuan"], allocation_style=style)
                else:
                    data.pop("capital_yuan", None)
                    data.pop("allocation_style", None)
                atomic_json(self.preferences_path, data)
        except ValueError as exc:
            raise ValidationError(str(exc)) from None
        return "研究偏好已保存；请本地重新筛选，或按新设置进行模型复核。"

    def snapshot(self):
        preferences = self.preferences()
        if not self.report_path.exists():
            return {"preferences": preferences, "report": None, "stocks": [],
                    "summary": "尚无研究报告。请先更新数据与周报。"}
        try:
            report = json.loads(self.report_path.read_text())
            if not isinstance(report, dict) or not isinstance(report.get("stock_recommendations"), dict):
                raise ValueError()
            load = engine(self.project)
            # Re-check existing evidence hashes in memory; never write on viewing.
            load("stock_selector.model_review").finalize(report)
            health = load("stock_selector.health").assess(report, datetime.now(SHANGHAI).isoformat())
            matching = (report.get("investment_horizon") == preferences["horizon"] and
                        report.get("capital_settings") == preferences["capital"])
            pipeline = report.get("decision_pipeline", {})
            generated = datetime.fromisoformat(report["asof"])
            valid_time = generated.tzinfo is not None and generated <= datetime.now(SHANGHAI)
            current = bool(matching and pipeline.get("review_current") and not health["blocked"] and valid_time)
            decisions = {row["symbol"]: row for row in pipeline.get("decisions", [])}
            watched = {x["query"] for x in self.desktop.config()["instruments"] if x["type"] == "stock"}
            stocks = []
            for group, rows in (("主名单", report["stock_recommendations"].get("stocks", [])),
                                ("外围观察", report["stock_recommendations"].get("supplementary_stocks", []))):
                for row in rows:
                    decision = decisions.get(row["symbol"], {})
                    label = decision.get("label", "待复核") if group == "主名单" else "仅观察"
                    if decision and not current:
                        label = "历史意见 / " + label
                    stocks.append({**row, "group": group, "decision": decision, "review_label": label,
                                   "watched": row["symbol"].split(".")[0] in watched})
            lines = ["报告生成：" + str(report.get("asof", "未知"))[:19].replace("T", " "),
                     "行业数据截止：" + str(report.get("data_times", {}).get("last") or "未知"),
                     f"优先行业 {len(report.get('research_priorities', []))} 个 · 主名单 "
                     f"{len(report['stock_recommendations'].get('stocks', []))} 只 · "
                     f"外围观察 {len(report['stock_recommendations'].get('supplementary_stocks', []))} 只",
                     "数据检查：" + health["status"]]
            if not matching:
                lines.append("研究设置已变化，当前报告尚未按新设置重筛；旧模型意见仅供历史查看。")
            if not valid_time:
                lines.append("报告生成时间异常，当前仅供历史查看。")
            if not current:
                lines.append("当前没有可作为本期有效结论的模型复核；初筛评分不代表买入通过。")
            if report.get("capital_allocation"):
                lines.append("资金方案：" + report["capital_allocation"].get("status", "待生成"))
            for check in health.get("checks", []):
                if check["status"] == "fail":
                    lines.append(check["name"] + "：" + check["detail"])
            for line in report.get("limitations", []):
                if "本次更新失败" in line:
                    lines.append("上次更新失败，当前使用旧缓存；完整原因见周报。")
            return {"preferences": preferences, "report": report, "stocks": stocks,
                    "current": current, "summary": "\n".join(lines)}
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ValidationError("研究报告格式不可用；原文件已保留，请检查研究数据或重新更新") from None

    def add_watch(self, symbol):
        # Only an actual candidate in the current on-disk report can be added.
        if not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", str(symbol)):
            raise ValidationError("请选择有效的A股候选代码")
        if symbol not in {row["symbol"] for row in self.snapshot()["stocks"]}:
            raise ValidationError("候选名单已更新，请刷新后重新选择")
        cfg = self.desktop.config()
        item = {"query": symbol[:6], "type": "stock"}
        if item in cfg["instruments"]:
            return "该股票已在日报关注列表中。"
        if self.desktop.state()["active"] or (self.desktop.child and self.desktop.child.poll() is None):
            raise ValidationError("请先停止自动日报，再修改关注列表；保存后可重新启动")
        from .watch import check_config
        cfg["instruments"].append(item)
        check_config(cfg)
        atomic_json(self.desktop.path, cfg)
        return "已加入日报关注；未录入持仓。下次启动日报时会分析该股票。"

    def output(self, kind="report"):
        paths = {"report": "sector_research/index.html", "folder": "sector_research",
                 "pdf": "sector_research/行业研究周报.pdf", "lab": "research_lab/index.html",
                 "validation": "research_validation/index.html"}
        path = self.project / "reports" / paths[kind]
        if not path.exists():
            raise ValidationError("尚无对应文件，请先运行相关研究任务")
        return path

    @property
    def busy(self):
        return bool(self.child and self.child.poll() is None)

    def start(self, action):
        if action not in ACTIONS:
            raise ValidationError("未知研究操作")
        if self.busy:
            raise ValidationError("已有研究任务运行，请勿重复点击")
        self.poll()
        engine(self.project)
        env = dict(os.environ)
        env.pop("DEEPSEEK_API_KEY", None)
        env.pop("AQUANT_PARENT_PIPE", None)
        env.pop("AQUANT_RESUME_DAILY_ONCE", None)
        if action in PAID:
            from .watch import credentials
            key = credentials(self.desktop.config()).get("DEEPSEEK_API_KEY", "").strip()
            if not key or not key.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in key):
                raise ValidationError("请先在设置页保存有效的 DeepSeek API 密钥")
            env["DEEPSEEK_API_KEY"] = key
            env["DEEPSEEK_MODEL"] = self.desktop.config()["model"]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(self.log_path, 0o600)
        self.stream = os.fdopen(fd, "a", encoding="utf-8")
        self.stream.write("\n" + datetime.now(SHANGHAI).isoformat() + " " + ACTIONS[action] + "\n")
        self.stream.flush()
        try:
            self.child = subprocess.Popen([sys.executable, "-u", "-B", "-m", "aquant.research_worker",
                "--project", str(self.project), "--action", action], cwd=PROJECT, env=env,
                stdin=subprocess.DEVNULL, stdout=self.stream, stderr=subprocess.STDOUT, start_new_session=True)
        except BaseException:
            self.stream.close()
            self.stream = None
            raise
        self.action = action
        self.stop_requested = None
        self.message = ACTIONS[action] + "进行中，可继续查看持仓和已有报告。"
        return self.message

    def stop(self):
        if self.busy and self.stop_requested is None:
            self.stop_requested = time.monotonic()
            self._signal(signal.SIGTERM)
            self.message = "正在停止研究任务；已发布的报告保留。"
        return self.message

    def _signal(self, value):
        try:
            os.killpg(self.child.pid, value)
        except ProcessLookupError:
            pass

    def poll(self):
        if self.child is None:
            return False
        if self.stop_requested is not None and time.monotonic() - self.stop_requested > 5:
            # Also reap descendants of a weekly runner terminated before its child.
            self._signal(signal.SIGKILL)
        code = self.child.poll()
        if code is None:
            return False
        self.child.wait()
        if self.stop_requested is not None:
            self._signal(signal.SIGKILL)
        if self.stream:
            self.stream.close()
            self.stream = None
        self.message = ("研究任务已停止，已发布报告保留。" if self.stop_requested is not None else
                        ACTIONS[self.action] + "已完成。" if code == 0 else
                        ACTIONS[self.action] + "未完成，请查看研究日志；已有报告仍可查看。")
        self.child = None
        self.stop_requested = None
        return True

    def logs(self):
        if not self.log_path.exists():
            return "尚无研究任务日志。"
        with self.log_path.open("rb") as stream:
            stream.seek(max(0, self.log_path.stat().st_size - 16000))
            return stream.read().decode("utf-8", errors="replace")

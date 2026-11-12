"""Native research pages in the A-Quant desktop window."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import subprocess

from .domain import ValidationError
from .research import ResearchModel

STYLES = {"保守": "conservative", "均衡": "balanced", "积极": "active"}


class ResearchView:
    def __init__(self, app, parent):
        self.app = app
        self.model = ResearchModel(app.model)
        self.rows = {}
        self.summary = tk.StringVar(value="正在读取已有研究报告…")
        self.task = tk.StringVar(value=self.model.message)
        self.actions = []
        for name in ("Research.Soft.TButton", "Research.Accent.TButton"):
            app.style.configure(name, padding=(12, 7), font=("PingFang SC", 11, "bold"))
        tk.Label(parent, textvariable=self.task, bg="#F3F5F9", fg="#6D7C92", anchor="w",
                 justify="left", wraplength=740, font=("PingFang SC", 10)).pack(side="bottom", fill="x", pady=(8, 0))
        tabs = ttk.Notebook(parent)
        self.tabs = tabs
        tabs.pack(fill="both", expand=True)
        candidates, sectors, operations = (tk.Frame(tabs, bg="white", padx=14, pady=12) for _ in range(3))
        for page, label in ((candidates, "候选与复核"), (sectors, "行业与资金"), (operations, "研究设置与任务")):
            tabs.add(page, text=label)
        tk.Label(candidates, textvariable=self.summary, bg="white", fg="#17243B", anchor="w",
                 justify="left", wraplength=700, font=("PingFang SC", 11)).pack(fill="x", pady=(0, 12))
        row = tk.Frame(candidates, bg="white")
        row.pack(fill="x", pady=(0, 10))
        self.button(row, "打开完整周报", self.open_report).pack(side="left")
        self.button(row, "刷新列表", self.load).pack(side="left", padx=8)
        table = tk.Frame(candidates, bg="white")
        cols = ("symbol", "name", "industry", "group", "score", "review", "watch")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=4, selectmode="browse")
        for key, title, width in zip(cols, ("代码", "公司", "行业", "范围", "规则分", "模型复核", "关注"),
                                     (105, 86, 100, 75, 60, 150, 55)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=width, stretch=key in ("industry", "review"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.bind("<Double-Button-1>", lambda _: self.details())
        footer = tk.Frame(candidates, bg="white")
        footer.pack(side="bottom", fill="x", pady=(10, 0))
        row = tk.Frame(footer, bg="white")
        row.pack(fill="x", pady=(0, 8))
        self.details_button = self.button(row, "查看研究依据", self.details)
        self.details_button.pack(side="left")
        self.button(row, "加入日报关注", self.add_watch, True).pack(side="left", padx=8)
        tk.Label(footer, text="规则分用于排序；关注可包含暂缓股票。实际买入后到“我的持仓”录入。",
                 bg="white", fg="#6D7C92", wraplength=650, justify="left",
                 font=("PingFang SC", 10)).pack(anchor="w")
        table.pack(fill="both", expand=True)
        self.sector_text = self.textbox(sectors)

        fields = tk.Frame(operations, bg="white")
        fields.pack(fill="x")
        self.months, self.budget, self.style = tk.StringVar(value="6"), tk.StringVar(), tk.StringVar(value="均衡")
        for index, (title, variable) in enumerate((("预计持有（月，1—24）", self.months),
                                                  ("计划新增资金（元，可填10万）", self.budget),
                                                  ("资金分配风格", self.style))):
            cell = tk.Frame(fields, bg="white")
            cell.grid(row=0, column=index, sticky="ew", padx=(0, 10))
            fields.columnconfigure(index, weight=1)
            app.label(cell, title, 10, color="#6D7C92").pack(anchor="w", pady=(0, 5))
            if index == 2:
                ttk.Combobox(cell, textvariable=variable, values=list(STYLES), state="readonly", width=12).pack(fill="x")
            else:
                ttk.Entry(cell, textvariable=variable, width=16).pack(fill="x")
        self.save_button = self.button(operations, "保存研究偏好", self.save, True)
        self.save_button.pack(anchor="w", pady=(8, 6))
        tk.Label(operations, text="资金仅指本次新增预算；不包含已有持仓。留空可取消资金设置。\n"
                 "离线重筛沿用本地缓存；模型复核共用“设置”页密钥和模型，会消耗 API 额度。",
                 bg="white", fg="#6D7C92", justify="left", wraplength=700,
                 font=("PingFang SC", 10)).pack(anchor="w", pady=(0, 10))
        buttons = tk.Frame(operations, bg="white")
        buttons.pack(fill="x")
        choices = (("rescreen", "本地重新筛选"), ("update", "更新数据与周报"), ("ai", "DeepSeek 复核（计费）"),
                   ("replay", "重放模型结果"), ("pdf", "导出研究 PDF"), ("full", "更新并复核（计费）"),
                   ("health", "检查研究数据"), ("lab", "增强因子审计"), ("validation", "前瞻验证"))
        for index, (action, title) in enumerate(choices):
            button = self.button(buttons, title, lambda value=action: self.start(value))
            button.grid(row=index // 3, column=index % 3, sticky="ew", padx=(0, 8), pady=(0, 6))
            buttons.columnconfigure(index % 3, weight=1)
            self.actions.append(button)
        row = tk.Frame(operations, bg="white")
        row.pack(fill="x", pady=(4, 8))
        self.stop_button = self.button(row, "停止研究任务", self.stop)
        self.stop_button.pack(side="left")
        self.button(row, "研究日志", self.logs).pack(side="left", padx=8)
        self.button(row, "报告文件夹", lambda: self.open_report("folder")).pack(side="left")
        self.progress = ttk.Progressbar(operations, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 8))
        self.log_text = self.textbox(operations, height=4)
        self.load(preferences=True)
        self.refresh()

    def button(self, parent, title, command, primary=False):
        button = self.app.button(parent, title, command, primary)
        button.configure(style="Research.Accent.TButton" if primary else "Research.Soft.TButton")
        return button

    def textbox(self, parent, height=10):
        frame = tk.Frame(parent, bg="white")
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, height=height, bg="white", fg="#17243B", relief="flat", wrap="word",
                       font=("PingFang SC", 11), padx=6, pady=6)
        text.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(frame, command=text.yview)
        bar.pack(side="right", fill="y")
        text.configure(yscrollcommand=bar.set)
        return text

    def load(self, preferences=False):
        try:
            data = self.model.snapshot()
            if preferences:
                prefs = data["preferences"]
                self.months.set(str(prefs["horizon"]["months"]))
                self.budget.set((prefs["capital"] or {}).get("budget_yuan", ""))
                style = (prefs["capital"] or {}).get("style", "balanced")
                self.style.set(next(key for key, value in STYLES.items() if value == style))
            self.summary.set(data["summary"])
            selected = self.tree.selection()
            selected_symbol = self.rows[selected[0]]["symbol"] if selected and selected[0] in self.rows else None
            self.tree.delete(*self.tree.get_children())
            self.rows = {}
            for index, item in enumerate(data["stocks"]):
                iid = str(index)
                self.rows[iid] = item
                self.tree.insert("", "end", iid=iid, values=(item["symbol"], item.get("name", ""),
                    item.get("industry_name", ""), item["group"], item.get("recommendation_score", "—"),
                    item["review_label"], "已关注" if item["watched"] else "—"))
                if selected_symbol == item["symbol"]:
                    self.tree.selection_set(iid)
            self.app.fill_text(self.sector_text, self.sector_summary(data))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.summary.set(str(exc) if isinstance(exc, (ValidationError, ValueError)) else "研究文件不可用，请检查数据目录。")
            self.tree.delete(*self.tree.get_children())
            self.rows = {}
            self.app.fill_text(self.sector_text, "研究资料暂时不可读；原始文件保留。")

    @staticmethod
    def sector_summary(data):
        report = data["report"]
        if not report:
            return data["summary"]
        priorities = set(report.get("research_priorities", []))
        lines = [data["summary"], "", "优先行业（沿用原行业筛选规则）"]
        for sector in report.get("sectors", []):
            if sector["code"] in priorities:
                lines.append(sector["name"] + " · " + sector.get("research_state", sector.get("flow_state", "")))
        if not priorities:
            lines.append("暂无满足要求的优先行业。")
        lines += ["", "新增资金的研究分配"]
        allocation = report.get("capital_allocation", {})
        lines.append(allocation.get("summary", "尚无资金分配，请保存预算后进行模型复核。"))
        if not data["current"]:
            lines.append("以下是报告中的历史方案，当前未作为有效的本期分配。")
        for portfolio in allocation.get("portfolios", []):
            lines += ["", portfolio.get("title", "资金方案") + " · " + portfolio.get("status", ""),
                      "保留现金：" + str(portfolio.get("cash_yuan", "未知")) + " 元"]
            for row in portfolio.get("allocations", []):
                lines += [row.get("name", row["symbol"]) + " " + row["symbol"] + "：" + str(row.get("amount_yuan", "")) + " 元",
                          "条件：" + row.get("condition", "待核实")]
        lines += ["", allocation.get("note", "当前与未来备选方案是同一笔新增资金的不同情景。"),
                  "", "数据与方法限制", *report.get("limitations", [])]
        return "\n".join(lines)

    def selected(self):
        selected = self.tree.selection()
        if not selected:
            raise ValidationError("请先在候选表中选择一只股票")
        return self.rows[selected[0]]

    def details(self):
        try:
            item = self.selected()
            decision = item.get("decision", {})
            review = decision.get("review") or {}
            quote = item.get("quote") or {}
            lines = [item.get("name", "") + " " + item["symbol"], item["group"] + " · " + item["review_label"],
                     "行业：" + item.get("industry_name", "未知"),
                     "参考价：" + str(quote.get("price", "缺失")) + "；行情时间：" + str(quote.get("quote_at", "缺失")),
                     "", "规则依据", *item.get("reasons", []), "", "模型复核", review.get("reason", "尚无有效复核意见"),
                     "反向风险：" + review.get("counter_case", "待核实"), *review.get("blockers", []),
                     "", "买入前提", item.get("entry_condition", "待核实"), "", "失效条件", item.get("invalidation", "待核实"),
                     "", "风险", *item.get("risks", []), "", "模型条件计划"]
            plan = review.get("buy_plan") or {}
            for key, title in (("entry_condition", "开始条件"), ("cancel_condition", "取消条件"),
                               ("exit_review_condition", "退出复核")):
                lines.append(title + "：" + plan.get(key, "尚无有效计划"))
            for tranche in plan.get("tranches", []):
                lines.append("分批条件：" + tranche.get("condition", "待核实"))
            lines += ["", "加入关注只开启后续日线研究，不代表模型通过或实际持有。"]
            self.app.text_window("候选股研究依据", "\n".join(lines))
        except Exception as exc:
            self.app.error(exc)

    def add_watch(self):
        try:
            self.app.note.set(self.model.add_watch(self.selected()["symbol"]))
            self.load()
            self.app.refresh_once()
        except Exception as exc:
            self.app.error(exc)

    def save(self):
        try:
            message = self.model.save_preferences(self.months.get().strip(), self.budget.get().strip(), STYLES[self.style.get()])
            self.app.note.set(message)
            self.load(preferences=True)
        except Exception as exc:
            self.app.error(exc)

    def start(self, action):
        if self.app.closing:
            return
        try:
            self.task.set(self.model.start(action))
            self.refresh()
        except Exception as exc:
            self.app.error(ValidationError(str(exc)) if isinstance(exc, ValueError) else exc)

    def stop(self):
        self.task.set(self.model.stop())

    def refresh(self):
        finished = self.model.poll()
        busy = self.model.busy
        for button in (*self.actions, self.save_button):
            button.configure(state="disabled" if busy or self.app.closing else "normal")
        self.stop_button.configure(state="normal" if busy and self.model.stop_requested is None else "disabled")
        self.task.set(self.model.message)
        if busy:
            if not getattr(self, "animating", False):
                self.progress.start(15)
        else:
            self.progress.stop()
        self.animating = busy
        if busy or finished:
            self.app.fill_text(self.log_text, self.model.logs())
            self.log_text.see("end")
        if finished:
            self.load()

    def logs(self):
        self.app.text_window("研究任务日志", self.model.logs())

    def open_report(self, kind="report"):
        try:
            subprocess.Popen(["open", str(self.model.output(kind))])
        except Exception as exc:
            self.app.error(exc)

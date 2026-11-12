"""Native local Mac/Tk desktop. Ordinary launches keep the mail task stopped."""
from __future__ import annotations

import fcntl
import json
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .control import doctor, offline_preview
from .desktop_model import DesktopModel, atomic_json, PROJECT
from .domain import ValidationError
from .menubar import MacMenuBar, menu_state
from .research_view import ResearchView

BG, INK, MUTED, BLUE, BORDER = "#F3F5F9", "#17243B", "#6D7C92", "#245FE5", "#E1E7F0"
FONT = "PingFang SC"


class DesktopApp:
    def __init__(self, root, model):
        self.root, self.model = root, model
        self.closing = False
        self.stopping = False
        self.menubar = None
        self.current_page = "总览"
        self.root.title("A-Quant · 个人量化助手")
        self.root.geometry("1080x780")
        self.root.minsize(960, 720)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close_window)
        self.root.option_add("*Font", (FONT, 12))
        style = ttk.Style()
        self.style = style
        style.theme_use("clam")
        style.configure("TEntry", padding=8, fieldbackground="white", foreground=INK, bordercolor=BORDER)
        style.configure("TCombobox", padding=8, fieldbackground="white", foreground=INK)
        style.map("TCombobox", fieldbackground=[("readonly", "white")], foreground=[("readonly", INK)])
        style.configure("Accent.TButton", padding=(18, 11), background=BLUE, foreground="white", borderwidth=0,
                        font=(FONT, 12, "bold"), focuscolor=BLUE)
        style.map("Accent.TButton", background=[("disabled", "#E3EAF6"), ("active", "#1D50C0")],
                  foreground=[("disabled", "#8A99B0")])
        style.configure("Soft.TButton", padding=(18, 11), background="#EAF0FA", foreground=BLUE, borderwidth=0,
                        font=(FONT, 12, "bold"), focuscolor="#EAF0FA")
        style.map("Soft.TButton", background=[("disabled", "#F0F3F8"), ("active", "#DCE7FB")],
                  foreground=[("disabled", "#8A99B0")])
        style.configure("Treeview", font=(FONT, 12), rowheight=36, background="white", fieldbackground="white", borderwidth=0)
        style.configure("Treeview.Heading", font=(FONT, 11, "bold"), background="#EDF2FA", padding=10)
        style.map("Treeview", background=[("selected", "#DDE9FF")], foreground=[("selected", INK)])
        sidebar = tk.Frame(root, bg="#17243B", width=210)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="A-Quant", bg="#17243B", fg="white", font=(FONT, 25, "bold")).pack(anchor="w", padx=26, pady=(34, 0))
        tk.Label(sidebar, text="个人量化助手", bg="#17243B", fg="#A9BDD9", font=(FONT, 12)).pack(anchor="w", padx=28, pady=(4, 34))
        self.nav = {}
        for index, title in enumerate(("总览", "选股研究", "我的持仓", "设置", "报告与诊断")):
            nav_style = f"Nav{index}.TButton"
            style.configure(nav_style, anchor="w", padding=(20, 13), borderwidth=0,
                            background="#17243B", foreground="#CBD6E7", font=(FONT, 12), focuscolor="#17243B")
            style.map(nav_style, background=[("active", "#2B405E")], foreground=[("active", "white")])
            button = ttk.Button(sidebar, text=title, style=nav_style, cursor="hand2", command=lambda t=title: self.show(t))
            button.pack(fill="x", padx=14, pady=3)
            self.nav[title] = button
        tk.Label(sidebar, text="本机运行 · 密钥本地保存\n启动后才分析和发邮件", justify="left", bg="#17243B", fg="#A9BDD9",
                 font=(FONT, 10), wraplength=170).pack(side="bottom", anchor="w", padx=26, pady=30)
        main = tk.Frame(root, bg=BG)
        main.pack(side="left", fill="both", expand=True, padx=30, pady=28)
        self.page_title = tk.StringVar(value="总览")
        tk.Label(main, textvariable=self.page_title, bg=BG, fg=INK, font=(FONT, 25, "bold")).pack(anchor="w")
        tk.Label(main, text="把数据整理好，把任务状态看清楚。", bg=BG, fg=MUTED).pack(anchor="w", pady=(4, 20))
        self.container = tk.Frame(main, bg=BG)
        self.pages = {title: tk.Frame(self.container, bg=BG) for title in self.nav}
        self.note = tk.StringVar(value="软件已打开，自动日报尚未由本窗口启动。")
        tk.Label(main, textvariable=self.note, bg=BG, fg=MUTED, anchor="w", justify="left", wraplength=780,
                 font=(FONT, 10)).pack(side="bottom", fill="x", pady=(12, 0))
        self.container.pack(fill="both", expand=True)
        self.overview()
        self.research = ResearchView(self, self.pages["选股研究"])
        self.positions_page()
        self.settings_page()
        self.reports_page()
        self.show("总览")
        self.refresh()

    def label(self, parent, text, size=12, bold=False, color=INK):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color, font=(FONT, size, "bold" if bold else "normal"), anchor="w")

    def button(self, parent, text, command, primary=False):
        return ttk.Button(parent, text=text, command=command, style="Accent.TButton" if primary else "Soft.TButton", cursor="hand2")

    def card(self, parent, title=None):
        frame = tk.Frame(parent, bg="white", highlightbackground=BORDER, highlightthickness=1, padx=20, pady=18)
        if title:
            self.label(frame, title, 14, True).pack(anchor="w", pady=(0, 12))
        return frame

    def show(self, title):
        self.current_page = title
        self.page_title.set(title)
        for key, page in self.pages.items():
            page.pack_forget()
            self.style.configure(self.nav[key].cget("style"), background="#2B405E" if key == title else "#17243B",
                                 foreground="white" if key == title else "#CBD6E7")
        self.pages[title].pack(fill="both", expand=True)
        if title == "我的持仓":
            self.load_positions()
        if title == "报告与诊断":
            self.load_reports()
        if title == "选股研究":
            self.research.load()

    def overview(self):
        parent = self.pages["总览"]
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x")
        self.metrics = {}
        for index, title in enumerate(("自动日报", "发送时间", "关注标的")):
            row.columnconfigure(index, weight=1, uniform="metrics")
            frame = self.card(row)
            frame.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0))
            self.label(frame, title, color=MUTED).pack(anchor="w")
            value = tk.StringVar(value="—")
            tk.Label(frame, textvariable=value, bg="white", fg=INK, font=(FONT, 20, "bold")).pack(anchor="w", pady=(5, 0))
            self.metrics[title] = value
        actions = self.card(parent, "让日报按时送达")
        actions.pack(fill="x", pady=16)
        self.label(actions, "每天一次 · 三源轮换 · 自动综合 · 邮件提醒", color=MUTED).pack(anchor="w")
        buttons = tk.Frame(actions, bg="white")
        buttons.pack(anchor="w", pady=(16, 8))
        self.start_button = self.button(buttons, "启动自动日报", self.start, True)
        self.start_button.pack(side="left")
        self.stop_button = self.button(buttons, "停止日报", self.stop)
        self.stop_button.pack(side="left", padx=10)
        self.button(buttons, "离线报告预览", self.preview).pack(side="left")
        self.manual_button = self.button(actions, "立即生成并发送邮件", self.send_now, True)
        self.manual_button.pack(anchor="w", pady=(8, 4))
        self.manual_note = tk.StringVar(value="")
        tk.Label(actions, textvariable=self.manual_note, bg="white", fg=MUTED, justify="left", wraplength=740,
                 font=(FONT, 10)).pack(anchor="w")
        self.owner_note = tk.StringVar(value="")
        tk.Label(actions, textvariable=self.owner_note, bg="white", fg=MUTED, justify="left", wraplength=740,
                 font=(FONT, 10)).pack(anchor="w", pady=(4, 0))
        frame = self.card(parent, "数据来源")
        frame.pack(fill="x")
        self.source_text = tk.Text(frame, height=6, bg="white", fg=INK, relief="flat", wrap="word", font=(FONT, 11))
        self.source_text.pack(fill="x")
        self.label(parent, "当前依据：已完成日线。盘中收到报告也不代表当日实时判断。", color=MUTED).pack(anchor="w", pady=14)

    def positions_page(self):
        parent = self.pages["我的持仓"]
        frame = self.card(parent, "录入当前持仓")
        frame.pack(fill="x")
        self.position_vars = {key: tk.StringVar() for key in ("code", "kind", "shares", "cost")}
        self.position_vars["kind"].set("ETF")
        fields = tk.Frame(frame, bg="white")
        fields.pack(fill="x")
        for col, (key, title) in enumerate((("code", "证券代码 / 已保存列表"), ("kind", "类型"), ("shares", "当前份数 / 股数"), ("cost", "每份 / 股成本"))):
            cell = tk.Frame(fields, bg="white")
            cell.grid(row=0, column=col, padx=(0, 12), sticky="ew")
            fields.columnconfigure(col, weight=1)
            self.label(cell, title, 10, color=MUTED).pack(anchor="w", pady=(0, 6))
            if key == "code":
                widget = ttk.Combobox(cell, textvariable=self.position_vars[key], width=17)
                self.position_code_combo = widget
                widget.bind("<<ComboboxSelected>>", self.choose_position_code)
            elif key == "kind":
                widget = ttk.Combobox(cell, textvariable=self.position_vars[key], values=["ETF", "股票"], state="readonly", width=7)
            else:
                widget = ttk.Entry(cell, textvariable=self.position_vars[key], width=12)
            widget.pack(fill="x")
        actions = tk.Frame(frame, bg="white")
        actions.pack(anchor="w", pady=(14, 8))
        self.button(actions, "保存持仓并关注", self.save_position, True).pack(side="left")
        self.names_button = self.button(actions, "补全证券名称", self.update_position_names)
        self.names_button.pack(side="left", padx=(10, 0))
        self.name_note = tk.StringVar(value=self.model.name_message)
        tk.Label(frame, textvariable=self.name_note, bg="white", fg=MUTED, justify="left", wraplength=700,
                 font=(FONT, 10)).pack(anchor="w", pady=(0, 4))
        self.label(frame, "填写当前总持仓，不是追加买入。清仓填 0；成本保留最多四位小数。", 10, color=MUTED).pack(anchor="w")
        table_frame = self.card(parent, "已保存持仓")
        table_frame.pack(fill="both", expand=True, pady=(16, 0))
        self.position_choices = {}
        table = tk.Frame(table_frame, bg="white")
        self.label(table_frame, "下拉列表或点击持仓可载入修改；名称会保留，联网失败时沿用已识别的名称。", 10, color=MUTED).pack(side="bottom", anchor="w", pady=(10, 0))
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("code", "name", "kind", "shares", "cost", "total"), show="headings", height=5)
        for key, title, width in (("code", "证券代码", 88), ("name", "证券名称", 185), ("kind", "类型", 65), ("shares", "持有数量", 95), ("cost", "成本单价", 95), ("total", "成本总额", 110)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=65 if key != "name" else 140, anchor="w" if key == "name" else "center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.select_position)

    def load_positions(self):
        identifiers = set()
        choices = {}
        for row in self.model.positions():
            key = row["kind"] + ":" + row["code"]
            identifiers.add(key)
            kind = "ETF" if row["kind"] == "etf" else "股票"
            name = row.get("name") or "待联网识别"
            choices[row["code"] + " · " + name + " · " + kind] = key
            values = (row["code"], name, kind, row["shares"], row["cost"], f"{Decimal(row['cost']) * row['shares']:.2f}")
            if self.tree.exists(key):
                self.tree.item(key, values=values)
            else:
                self.tree.insert("", "end", iid=key, values=values)
        for key in self.tree.get_children():
            if key not in identifiers:
                self.tree.delete(key)
        self.position_choices = choices
        self.position_code_combo.configure(values=list(choices))

    def choose_position_code(self, _=None):
        key = self.position_choices.get(self.position_vars["code"].get())
        if key and self.tree.exists(key):
            self.tree.selection_set(key)
            self.tree.see(key)
            self.select_position()

    def select_position(self, _=None):
        selected = self.tree.selection()
        if selected:
            values = dict(zip(self.tree["columns"], self.tree.item(selected[0], "values")))
            for key in ("code", "kind", "shares", "cost"):
                self.position_vars[key].set(values[key])

    def save_position(self):
        try:
            values = {k: v.get().strip() for k, v in self.position_vars.items()}
            values["code"] = values["code"].split(" · ", 1)[0]
            self.model.save_position(values["code"], "etf" if values["kind"] == "ETF" else "stock", int(values["shares"]), values["cost"])
            self.load_positions()
            self.note.set("持仓已保存并加入列表；首次可点击“补全证券名称”，后续直接从下拉列表选择。")
        except Exception as exc:
            self.error(exc)

    def update_position_names(self):
        try:
            self.note.set(self.model.update_names())
            self.names_button.configure(state="disabled")
            self.name_note.set(self.model.name_message)
        except Exception as exc:
            self.error(exc)

    def settings_page(self):
        outer = self.card(self.pages["设置"], "发送计划与服务设置")
        outer.pack(fill="both", expand=True)
        frame = tk.Frame(outer, bg="white")
        frame.pack(fill="both", expand=True)
        values = self.model.settings()
        self.settings_vars = {k: tk.StringVar(value=values.get(k, "")) for k in
                              ("schedule", "model", "SMTP_HOST", "SMTP_USER", "SMTP_FROM", "SMTP_TO", "DEEPSEEK_API_KEY", "SMTP_PASSWORD")}
        fields = (("schedule", "发送时间（北京时间 HH:MM）"), ("model", "DeepSeek 模型名"),
                  ("SMTP_HOST", "SMTP 服务器 · SSL 465"), ("SMTP_USER", "SMTP 登录邮箱"),
                  ("SMTP_FROM", "发件邮箱"), ("SMTP_TO", "收件邮箱"),
                  ("DEEPSEEK_API_KEY", "API 密钥 · 留空保留原值"), ("SMTP_PASSWORD", "邮箱授权码 · 留空保留原值"))
        for index, (key, label) in enumerate(fields):
            cell = tk.Frame(frame, bg="white")
            cell.grid(row=index // 2 + 1, column=index % 2, sticky="ew", padx=(0, 20), pady=(0, 20))
            frame.columnconfigure(index % 2, weight=1)
            self.label(cell, label, 10, color=MUTED).pack(anchor="w", pady=(0, 6))
            ttk.Entry(cell, textvariable=self.settings_vars[key], show="•" if key in ("DEEPSEEK_API_KEY", "SMTP_PASSWORD") else "", width=30).pack(fill="x")
        self.button(frame, "保存设置", self.save_settings, True).grid(row=6, column=0, sticky="w", pady=(0, 15))
        self.label(frame, "保存仅修改本地配置，不会立即试发邮件。密钥不回显；不填写即可保留。", 10, color=MUTED).grid(row=7, column=0, columnspan=2, sticky="w")

    def save_settings(self):
        try:
            self.model.save_settings({k: v.get() for k, v in self.settings_vars.items()})
            for key in ("DEEPSEEK_API_KEY", "SMTP_PASSWORD"):
                self.settings_vars[key].set("")
            self.note.set("设置已保存；发送计划将在下一次检查时生效。")
            self.refresh_once()
        except Exception as exc:
            self.error(exc)

    def reports_page(self):
        parent = self.pages["报告与诊断"]
        frame = self.card(parent, "历史报告")
        frame.pack(fill="x")
        self.report_list = tk.Listbox(frame, height=5, relief="flat", bg="white", fg=INK, selectbackground="#DDE9FF", selectforeground=INK)
        self.report_list.pack(fill="x")
        self.report_list.bind("<Double-Button-1>", lambda _: self.open_report())
        row = tk.Frame(frame, bg="white")
        row.pack(anchor="w", pady=(12, 0))
        self.button(row, "查看所选报告", self.open_report).pack(side="left")
        self.button(row, "刷新列表", self.load_reports).pack(side="left", padx=10)
        frame = self.card(parent, "本机检查")
        frame.pack(fill="both", expand=True, pady=(16, 0))
        row = tk.Frame(frame, bg="white")
        row.pack(anchor="w")
        self.button(row, "离线自检", self.diagnostics).pack(side="left")
        self.button(row, "查看运行日志", self.logs).pack(side="left", padx=10)
        self.diagnostic_text = tk.Text(frame, height=9, bg="white", fg=INK, relief="flat", wrap="word", font=(FONT, 11))
        self.diagnostic_text.pack(fill="both", expand=True, pady=(12, 0))
        self.fill_text(self.diagnostic_text, "自检不联网、不调用 DeepSeek、不发送邮件。")

    def load_reports(self):
        self.report_paths = self.model.reports()
        self.report_list.delete(0, "end")
        for path in self.report_paths:
            at = datetime.fromtimestamp(path.stat().st_mtime).strftime("%m-%d %H:%M")
            self.report_list.insert("end", at + "  ·  量化日报")

    def open_report(self):
        selection = self.report_list.curselection()
        if selection:
            self.text_window("历史量化日报", self.report_paths[selection[0]].read_text(encoding="utf-8"))

    def text_window(self, title, text):
        window = tk.Toplevel(self.root)
        window.transient(self.root)
        window.title(title)
        window.geometry("820x650")
        widget = tk.Text(window, bg="white", fg=INK, padx=24, pady=24, wrap="word", font=(FONT, 12))
        widget.pack(fill="both", expand=True)
        self.fill_text(widget, text)

    @staticmethod
    def fill_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def diagnostics(self):
        try:
            self.fill_text(self.diagnostic_text, doctor(self.model.path))
        except Exception as exc:
            self.error(exc)

    def logs(self):
        path = self.model.root() / "desktop-watch.log"
        text = path.read_text(encoding="utf-8", errors="replace")[-8000:] if path.exists() else "本软件尚无任务日志。"
        self.fill_text(self.diagnostic_text, text)

    def preview(self):
        try:
            path = offline_preview(self.model.config())
            self.text_window("离线预览 · 不联网不发信", path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.error(exc)

    def start(self):
        try:
            self.model.start()
            self.stopping = False
            self.note.set("日报正在启动；可关闭窗口收起到右上角，请保持电脑开机、联网且不休眠。" if self.menubar else
                          "任务正在本软件内启动，请保持软件打开和电脑联网。")
            self.refresh_once()
        except Exception as exc:
            self.error(exc)

    def send_now(self):
        if self.closing or self.stopping:
            return
        try:
            self.note.set(self.model.send_now())
            self.refresh_once()
        except Exception as exc:
            self.error(exc)

    def stop(self):
        try:
            self.note.set(self.model.stop_owned())
            self.stopping = bool(self.model.child and self.model.child.poll() is None)
            self.refresh_once()
        except Exception as exc:
            self.error(exc)

    def refresh_once(self):
        if hasattr(self, "research"):
            self.research.refresh()
        self.model.release()
        if hasattr(self, "names_button"):
            self.names_button.configure(state="disabled" if self.model.names_busy() or self.closing else "normal")
            self.name_note.set(self.model.name_message)
            if self.current_page == "我的持仓":
                self.load_positions()
        state = self.model.state()
        running = bool(state["active"] or state["owned"])
        if not state["owned"]:
            self.stopping = False
        pending = self.closing or self.stopping
        manual_busy, manual_text = self.model.manual_status()
        state["manual_busy"] = manual_busy
        state["manual_external"] = bool(state["active"] and not state["owned"])
        self.manual_note.set(manual_text)
        self.manual_button.configure(state="disabled" if pending or manual_busy or state["manual_external"] else "normal")
        self.metrics["自动日报"].set("停止中" if pending and running else "运行中" if running else "已停止")
        self.metrics["发送时间"].set(state["schedule"])
        self.metrics["关注标的"].set(str(len(self.model.config()["instruments"])))
        self.start_button.configure(state="disabled" if running or pending else "normal")
        self.stop_button.configure(state="normal" if state["owned"] and not pending else "disabled")
        self.owner_note.set(("关闭窗口后仍在右上角运行；菜单栏“停止日报”暂停任务，“退出”停止并关闭软件。" if self.menubar else
                             "由本软件管理，退出软件会请求停止并等待本实例退出。") if state["owned"] else
                            "另一个终端的任务正在运行；请在原终端 Ctrl+C，本软件不接管或自动停止它。" if running else
                            ("可从右上角菜单启动日报。关闭窗口仅收起界面；菜单栏“退出”才完全关闭。" if self.menubar else
                             "点击启动后才会联网分析和发邮件；退出不会留下本软件的发信任务。"))
        self.fill_text(self.source_text, "\n".join(state["sources"]))
        if self.menubar:
            self.menubar.update(menu_state(state, closing=self.closing, stopping=self.stopping))

    def refresh(self):
        if not self.closing:
            try:
                self.refresh_once()
            except Exception:
                self.note.set("状态读取失败，请检查配置或使用离线自检。")
                if self.menubar:
                    self.menubar.update(menu_state({}, failed=True))
            self.root.after(3000, self.refresh)

    def error(self, exc):
        text = str(exc) if isinstance(exc, ValidationError) else "本地输入或文件有误，请检查填写内容。"
        self.note.set(text)
        self.show_window()
        messagebox.showerror("操作未完成", text, parent=self.root)

    def install_menu_bar(self, library=None):
        if sys.platform != "darwin":
            return False
        library = library or PROJECT / "A-Quant.app/Contents/Resources/libAQuantMenuBar.dylib"
        try:
            self.menubar = MacMenuBar(library, self.root,
                {"show": self.show_window, "start": self.start, "stop": self.stop, "quit": self.close, "manual": self.send_now})
            self.refresh_once()
            self.note.set("右上角 A 图标为常驻入口。关窗口继续运行；菜单栏“退出”才完全关闭。")
            return True
        except Exception:
            if self.menubar:
                self.menubar.restore_dock()
                self.menubar.close()
                self.menubar = None
            self.note.set("菜单栏入口未能加载，已保留主窗口；关闭窗口将退出软件。")
            self.show_window()
            return False

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        if self.menubar:
            self.menubar.activate()
        self.root.focus_force()

    def close_window(self):
        if self.closing:
            return
        if self.menubar:
            self.root.withdraw()
        else:
            self.close()

    def close(self):
        if self.closing:
            return
        self.closing = True
        research = getattr(self, "research", None)
        research_busy = bool(research and research.model.busy)
        if research_busy:
            research.model.stop()
        daily_busy = bool(self.model.child and self.model.child.poll() is None)
        if daily_busy:
            self.model.stop_owned()
        if daily_busy or research_busy:
            self.note.set("正在停止本软件任务并等待退出；已保存的报告保留。")
            self.wait_close()
        else:
            self.model.release()
            self.finish_close()

    def finish_close(self):
        if self.menubar:
            self.menubar.close()
            self.menubar = None
        self.root.destroy()

    def wait_close(self):
        research = getattr(self, "research", None)
        if research:
            research.model.poll()
        daily_busy = bool(self.model.child and self.model.child.poll() is None)
        if daily_busy or (research and research.model.busy):
            self.refresh_once()
            self.root.after(250, self.wait_close)
        else:
            self.model.release()
            self.finish_close()


def ensure_config(path):
    path = Path(path)
    if not path.exists():
        atomic_json(path, {"instruments": [{"query": "159567", "type": "etf"}], "schedule": ["14:00"],
                           "start_date": "20240101", "model": "deepseek-flash",
                           "state_dir": str(path.parent / "watch"), "secrets_file": str(path.parent / "watch-secrets.json")})


def resume_daily_once(app, smoke_test=False):
    """Restore an explicitly requested running task after a local migration."""
    requested = os.environ.pop("AQUANT_RESUME_DAILY_ONCE", "") == "1"
    if not requested or smoke_test or app.closing:
        return
    try:
        state = app.model.state()
        if not (state["active"] or state["owned"]):
            app.start()
    except Exception as exc:
        app.error(exc)


def main(path="runtime/watch-config.json", smoke_test=False):
    smoke_test = smoke_test or os.environ.get("AQUANT_DESKTOP_SMOKE") == "1"
    ensure_config(path)
    model = DesktopModel(path)
    lock_path = model.root() / "desktop.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("A-Quant", "软件已打开，请切换到现有窗口。")
            root.destroy()
            return 0
        root = tk.Tk()
        root.withdraw()
        app = DesktopApp(root, model)
        root.bind("<Command-q>", lambda event: (app.close(), "break")[1])
        root.createcommand("::tk::mac::Quit", app.close)
        root.createcommand("::tk::mac::ReopenApplication", app.show_window)
        if os.environ.get("AQUANT_APP_ICON"):
            root.app_icon = tk.PhotoImage(file=os.environ["AQUANT_APP_ICON"])
            root.iconphoto(True, root.app_icon)
        if not app.install_menu_bar():
            app.show_window()
        resume_daily_once(app, smoke_test=smoke_test)
        if smoke_test:
            root.after(500, app.close)
        root.mainloop()
    return 0


if __name__ == "__main__":
    main()

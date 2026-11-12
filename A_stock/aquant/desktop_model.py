"""Local desktop actions. No task starts until the user chooses Start."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from .control import configuration, set_schedule
from .domain import ValidationError
from .lifecycle import active, request_stop
from .positions import Positions
from .sources import pool_status
from .watch import check_config, credentials

PROJECT = Path(__file__).resolve().parent.parent


def atomic_json(path, value, private=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".desktop.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        if private:
            temp.chmod(0o600)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class DesktopModel:
    def __init__(self, config_path):
        self.path = Path(config_path).resolve()
        self.child = None
        self.log_stream = None
        self.name_thread = None
        self.name_message = "首次输入代码保存持仓，联网识别后会记住证券名称。"

    def config(self):
        return configuration(self.path)

    def root(self):
        return Path(self.config()["state_dir"])

    def positions(self):
        store = Positions(self.root())
        try:
            return store.all()
        finally:
            store.close()

    def names_busy(self):
        return bool(self.name_thread and self.name_thread.is_alive())

    def update_names(self):
        if self.names_busy():
            raise ValidationError("正在补全证券名称，请稍候")
        root = self.root()
        if not self.positions():
            raise ValidationError("请先输入证券代码并保存持仓")
        self.name_message = "正在识别证券名称；缺少目录时联网查询，请稍候…"

        def work():
            from .positions import complete_names
            try:
                self.name_message = complete_names(root)
            except Exception:
                self.name_message = "暂时无法取得证券名称，已保留原记录，请稍后重试。"

        self.name_thread = threading.Thread(target=work, daemon=True, name="security-names")
        self.name_thread.start()
        return "正在补全证券名称，请稍候。"

    def save_position(self, code, kind, shares, cost):
        cfg = self.config()
        item = {"query": code, "type": kind}
        if item not in cfg["instruments"]:
            cfg["instruments"].append(item)
        check_config(cfg)
        store = Positions(cfg["state_dir"])
        try:
            row = store.save(code, kind, shares, cost)
        finally:
            store.close()
        atomic_json(self.path, cfg)
        return row

    def save_schedule(self, value):
        set_schedule(self.path, value)

    def settings(self):
        cfg = self.config()
        values = credentials(cfg)
        # Secret fields are never returned to widgets; blank means keep existing.
        return {**{k: values.get(k, "") for k in ("SMTP_HOST", "SMTP_USER", "SMTP_FROM", "SMTP_TO")},
                "model": cfg["model"], "schedule": cfg["schedule"][0],
                "has_api_key": bool(values.get("DEEPSEEK_API_KEY")), "has_smtp_password": bool(values.get("SMTP_PASSWORD"))}

    def save_settings(self, fields):
        cfg = self.config()
        cfg["schedule"] = [fields["schedule"].strip()]
        cfg["model"] = fields["model"].strip()
        check_config(cfg)
        if not cfg["model"]:
            raise ValidationError("模型名不能为空")
        secret_path = Path(cfg["secrets_file"])
        saved = json.loads(secret_path.read_text()) if secret_path.exists() else {}
        for key in ("SMTP_HOST", "SMTP_USER", "SMTP_FROM", "SMTP_TO"):
            value = fields[key].strip()
            if not value or "\n" in value or "\r" in value:
                raise ValidationError("SMTP服务器和邮箱均不能为空或包含换行")
            if key != "SMTP_HOST" and (value.count("@") != 1 or any(c.isspace() for c in value)):
                raise ValidationError("请填写有效的邮箱地址")
            saved[key] = value
        for key in ("DEEPSEEK_API_KEY", "SMTP_PASSWORD"):
            if fields.get(key, "").strip():
                saved[key] = fields[key].strip()
        saved.setdefault("SMTP_PORT", "465")
        atomic_json(secret_path, saved, private=True)
        atomic_json(self.path, cfg)

    def state(self):
        info = active(self.root())
        return {"active": info, "owned": bool(self.child and self.child.poll() is None),
                "schedule": self.config()["schedule"][0], "sources": pool_status(self.root() / "data")}

    def manual_status(self):
        root = self.root()
        info = active(root)
        pending = root / "manual-send-request.json"
        busy = bool(info and info.get("mode") == "once")
        if pending.exists():
            try:
                request = json.loads(pending.read_text())
                busy = busy or bool(info and request.get("instance") == info.get("instance"))
            except (OSError, ValueError):
                pass
        result_path = root / "manual-send-result.json"
        text = "手动发送会联网分析并发送到设置中的邮箱；同日报告防重。"
        if busy:
            text = "手动发送：处理中，请稍候；可在报告与诊断中查看日志。"
        elif result_path.exists():
            try:
                result = json.loads(result_path.read_text())
                text = "上次手动发送（" + result["at"] + "）：" + result["message"]
            except (OSError, ValueError, KeyError):
                text = "手动发送结果记录不可读，请查看日志。"
        return busy, text

    def send_now(self):
        if self.manual_status()[0]:
            raise ValidationError("手动发送正在处理中，请勿重复点击")
        info = active(self.root())
        if info:
            if not (self.child and self.child.poll() is None and info.get("pid") == self.child.pid):
                raise ValidationError("另一个终端任务正在运行，请先在原终端停止，再从软件操作")
            request = {"instance": info["instance"]}
            atomic_json(self.root() / "manual-send-request.json", request, private=True)
            return "已提交手动发送，自动日报继续运行。"
        if self.child and self.child.poll() is None:
            raise ValidationError("任务正在启动或停止，请稍后再试")
        self.start(once=True)
        return "正在生成并发送本次报告；完成后本次任务自动结束。"

    def start(self, once=False):
        if active(self.root()) or (self.child and self.child.poll() is None):
            raise ValidationError("已有任务在运行，请勿重复启动")
        values = credentials(self.config())
        required = ("DEEPSEEK_API_KEY", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO")
        if not all(values.get(key) for key in required):
            raise ValidationError("请先在设置页补齐DeepSeek和邮箱配置")
        self.root().mkdir(parents=True, exist_ok=True)
        self.log_stream = (self.root() / "desktop-watch.log").open("a", encoding="utf-8")
        env = dict(os.environ)
        env["AQUANT_PARENT_PIPE"] = "1"
        try:
            self.child = subprocess.Popen([sys.executable, "-m", "aquant", "watch", "--config", str(self.path)] + (["--once", "--retry-failed"] if once else []),
                                          cwd=PROJECT, env=env, stdin=subprocess.PIPE,
                                          stdout=self.log_stream, stderr=subprocess.STDOUT)
        except BaseException:
            self.log_stream.close()
            self.log_stream = None
            raise
        return self.child.pid

    def stop_owned(self):
        # Closing the parent's pipe requests cancellation, even if the child
        # hasn't yet published its instance lock. Never kill an unrelated PID.
        if self.child and self.child.poll() is None and self.child.stdin:
            if not self.child.stdin.closed:
                self.child.stdin.close()
            info = active(self.root())
            if info and info.get("pid") == self.child.pid:
                return request_stop(self.root())
            return "已请求停止本软件任务，等待进程退出"
        return "本软件没有正在运行的任务"

    def release(self):
        if self.child and self.child.poll() is not None:
            self.child.wait()
            if self.child.stdin and not self.child.stdin.closed:
                self.child.stdin.close()
            if self.log_stream:
                self.log_stream.close()
                self.log_stream = None

    def reports(self):
        root = self.root()
        return sorted((p for p in root.glob("*.txt") if p.name != "offline-preview.txt"),
                      key=lambda p: p.stat().st_mtime, reverse=True)[:15]

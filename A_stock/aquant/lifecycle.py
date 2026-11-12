"""Owned process lock: one watch instance per state directory, no PID guessing."""
from __future__ import annotations

import fcntl
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from .domain import SHANGHAI, ValidationError


def active(directory):
    path = Path(directory) / "watch.lock"
    if not path.exists():
        return None
    try:
        with path.open("r+") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                try:
                    return json.loads(stream.read())
                except ValueError:
                    return {"state": "starting"}
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
                return None  # A leftover PID file is not a running process.
    except FileNotFoundError:
        return None


def request_stop(directory):
    info = active(directory)
    if not info:
        return "自动任务未运行，无需停止"
    if not info.get("instance"):
        return "任务正在初始化，请稍后重试停止"
    path = Path(directory) / f"stop-{info['instance']}.json"
    path.write_text(json.dumps({"requested_at": datetime.now(SHANGHAI).isoformat()}))
    return "已请求停止；网络请求结束后退出，发送前也会检查取消。请用watch-status确认。"


class WatchLock:
    def __init__(self, directory, mode):
        self.directory = Path(directory)
        self.mode = mode
        self.instance = uuid.uuid4().hex
        self.cancelled = False

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.stream = (self.directory / "watch.lock").open("a+")
        try:
            fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.stream.close()
            raise ValidationError("已有自动任务在运行；先用watch-status查看，勿重复启动") from None
        self.info = {"pid": os.getpid(), "instance": self.instance, "mode": self.mode,
                     "started_at": datetime.now(SHANGHAI).isoformat()}
        self.stream.seek(0)
        self.stream.truncate()
        json.dump(self.info, self.stream)
        self.stream.flush()
        return self

    def should_stop(self):
        return self.cancelled or self.stop_path.exists()

    @property
    def stop_path(self):
        return self.directory / f"stop-{self.instance}.json"

    def __exit__(self, *args):
        self.stop_path.unlink(missing_ok=True)
        fcntl.flock(self.stream, fcntl.LOCK_UN)
        self.stream.close()

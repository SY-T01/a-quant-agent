"""Persistent round-robin routing and per-provider circuit breakers."""
from __future__ import annotations

import fcntl
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .domain import SHANGHAI, ValidationError

PROVIDER_LABELS = {"eastmoney": "AKShare/Eastmoney", "tencent": "AKShare/Tencent", "baostock": "BaoStock"}


class UnsupportedSource(ValidationError):
    pass


class SourcePool:
    def __init__(self, directory, clock=None):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(SHANGHAI))
        self.db = sqlite3.connect(self.root / "sources.sqlite", timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS health (
            provider TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0,
            retry_after TEXT, last_error TEXT, last_success TEXT);
          CREATE TABLE IF NOT EXISTS cursors (scope TEXT PRIMARY KEY, next_index INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY, at TEXT NOT NULL, scope TEXT NOT NULL,
            provider TEXT NOT NULL, status TEXT NOT NULL, error TEXT);
        ''')

    def close(self):
        self.db.close()

    def fetch(self, scope, providers, validate):
        if not providers:
            raise ValidationError("没有配置可用数据源")
        lock = (self.root / "sources.lock").open("a+")
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValidationError("另一项行情下载正在进行，请勿并发重复下载") from None
            names = list(providers)
            cursor = self.db.execute("SELECT next_index FROM cursors WHERE scope=?", (scope,)).fetchone()
            start = cursor[0] % len(names) if cursor else 0
            trace = []
            for offset in range(len(names)):
                index = (start + offset) % len(names)
                name = names[index]
                now = self.clock()
                state = self.db.execute("SELECT * FROM health WHERE provider=?", (name,)).fetchone()
                if state and state["retry_after"] and datetime.fromisoformat(state["retry_after"]) > now:
                    trace.append({"provider": name, "status": "cooldown", "retry_after": state["retry_after"]})
                    with self.db:
                        self.db.execute("INSERT INTO attempts(at,scope,provider,status,error) VALUES(?,?,?,?,?)",
                                        (now.isoformat(), scope, name, "cooldown", None))
                    continue
                try:
                    frame, stats = validate(providers[name]())
                except UnsupportedSource:
                    trace.append({"provider": name, "status": "unsupported"})
                    with self.db:
                        self.db.execute("INSERT INTO attempts(at,scope,provider,status,error) VALUES(?,?,?,?,?)",
                                        (now.isoformat(), scope, name, "unsupported", "UnsupportedSource"))
                    continue
                except Exception as exc:
                    # Only exception classes are stored, not URLs, bodies or credentials.
                    error = type(exc).__name__
                    failures = state["failures"] + 1 if state else 1
                    minutes = 60 if error in ("Push2RateLimitError", "SourceRateLimited") else min(30, 5 * 2 ** min(failures - 1, 3))
                    until = getattr(exc, "retry_after", None) or (now + timedelta(minutes=minutes))
                    if until <= now:
                        until = now + timedelta(minutes=minutes)
                    with self.db:
                        self.db.execute("INSERT INTO health(provider,failures,retry_after,last_error) VALUES(?,?,?,?) "
                                        "ON CONFLICT(provider) DO UPDATE SET failures=excluded.failures, retry_after=excluded.retry_after,last_error=excluded.last_error",
                                        (name, failures, until.isoformat(), error))
                        self.db.execute("INSERT INTO attempts(at,scope,provider,status,error) VALUES(?,?,?,?,?)",
                                        (now.isoformat(), scope, name, "failed", error))
                    trace.append({"provider": name, "status": "failed", "error": error, "retry_after": until.isoformat()})
                    continue
                with self.db:
                    self.db.execute("INSERT INTO health(provider,last_success) VALUES(?,?) "
                                    "ON CONFLICT(provider) DO UPDATE SET failures=0,retry_after=NULL,last_error=NULL,last_success=excluded.last_success",
                                    (name, self.clock().isoformat()))
                    self.db.execute("INSERT OR REPLACE INTO cursors VALUES(?,?)", (scope, (index + 1) % len(names)))
                    self.db.execute("INSERT INTO attempts(at,scope,provider,status,error) VALUES(?,?,?,?,?)",
                                    (self.clock().isoformat(), scope, name, "success", None))
                trace.append({"provider": name, "status": "success"})
                return frame, stats, name, trace
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO cursors VALUES(?,?)", (scope, (start + 1) % len(names)))
            reasons = "；".join(t["provider"] + ":" + t["status"] + ("/" + t["error"] if t.get("error") else "") for t in trace)
            raise ValidationError("历史数据不可用，所有来源均未通过校验或处于冷却：" + reasons + "。不调用DeepSeek或发送交易建议。")
        finally:
            lock.close()


def pool_status(directory):
    path = Path(directory) / "sources.sqlite"
    if not path.exists():
        return ["数据源轮换池：尚未执行新下载"]
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        states = {r[0]: r[1:] for r in db.execute("SELECT provider,retry_after,last_error,last_success FROM health")}
        cursors = db.execute("SELECT scope,next_index FROM cursors ORDER BY scope").fetchall()
    lines = []
    for name, label in PROVIDER_LABELS.items():
        until, error, success = states.get(name, (None, None, None))
        if until and datetime.fromisoformat(until) > datetime.now(SHANGHAI):
            lines.append(f"{label}：冷却至{until}；原因{error}")
        else:
            lines.append(f"{label}：可尝试；最近成功{success or '尚无记录'}（不保证当前可用）")
    names = list(PROVIDER_LABELS)
    lines.extend(f"{scope}下次新下载优先：{PROVIDER_LABELS[names[index % len(names)]]}（冷却来源会跳过）"
                 for scope, index in cursors)
    return lines

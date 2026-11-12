from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .domain import (ValidationError, cents, integer, number, timestamp,
                     validate_account)


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA foreign_keys=ON;
          CREATE TABLE IF NOT EXISTS accounts (
            version INTEGER PRIMARY KEY, body TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, session_key TEXT UNIQUE NOT NULL,
            account_version INTEGER NOT NULL, body TEXT NOT NULL,
            feedback INTEGER NOT NULL DEFAULT 0, email_status TEXT NOT NULL DEFAULT 'pending');
          CREATE TABLE IF NOT EXISTS fills (
            id TEXT PRIMARY KEY, body TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def account(self):
        row = self.db.execute("SELECT body FROM accounts ORDER BY version DESC LIMIT 1").fetchone()
        if row is None:
            raise ValidationError("请先导入账户")
        return json.loads(row[0])

    def _save_account(self, account):
        validate_account(account)
        self.db.execute("INSERT INTO accounts VALUES (?, ?)",
                        (account["version"], json.dumps(account, ensure_ascii=False)))

    def import_account(self, account, expected_version=None):
        account = dict(account)
        with self.transaction():
            row = self.db.execute("SELECT MAX(version) FROM accounts").fetchone()
            if expected_version is not None and (row[0] or 0) != expected_version:
                raise ValidationError("账户版本已变化，请重新核对")
            account["version"] = (row[0] or 0) + 1
            self._save_account(account)
        return account

    def unresolved(self):
        return [r[0] for r in self.db.execute("SELECT id FROM runs WHERE feedback=0")]

    def run(self, run_id):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValidationError("找不到报告")
        return dict(row)

    def session(self, key):
        row = self.db.execute("SELECT body FROM runs WHERE session_key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_run(self, report):
        with self.transaction():
            if self.account()["version"] != report["account_version"]:
                raise ValidationError("账户在分析过程中已变化，请重新运行")
            needs_feedback = any(d["quantity"] for d in report["decisions"])
            self.db.execute("INSERT INTO runs(id, session_key, account_version, body, feedback) "
                            "VALUES(?,?,?,?,?)", (report["id"], report["session_key"],
                            report["account_version"], json.dumps(report, ensure_ascii=False),
                            0 if needs_feedback else 1))

    def confirm(self, run_id, expected_version, at):
        """User confirms all actual executions have been entered (including none)."""
        timestamp(at)
        with self.transaction():
            saved = self.run(run_id)
            account = self.account()
            if account["version"] != expected_version:
                raise ValidationError("账户版本已变化")
            if timestamp(at) < timestamp(json.loads(saved["body"])["as_of"]):
                raise ValidationError("反馈确认时间不能早于报告生成时间")
            if saved["feedback"]:
                return account
            if timestamp(at) < timestamp(account["confirmed_at"]):
                raise ValidationError("确认时间不能倒退")
            account["version"] += 1
            account["confirmed_at"] = at
            self._save_account(account)
            self.db.execute("UPDATE runs SET feedback=1 WHERE id=?", (run_id,))
        return account

    def fill(self, fill, expected_version):
        """Record actual broker fills, never fictional executions of recommendations."""
        for key in ("id", "symbol", "side", "at", "board"):
            if not isinstance(fill.get(key), str) or not fill[key]:
                raise ValidationError(f"成交缺少{key}")
        if fill["side"] not in ("BUY", "SELL"):
            raise ValidationError("成交方向无效")
        if fill["board"] not in ("MAIN", "CHINEXT", "STAR", "BSE"):
            raise ValidationError("成交板块无效")
        integer(fill["quantity"], "成交数量", 1)
        number(fill["price"], "成交价格", 0.000001)
        number(fill["fee"], "实际费用")
        at = timestamp(fill["at"])
        encoded = json.dumps(fill, sort_keys=True)
        with self.transaction():
            old = self.db.execute("SELECT body FROM fills WHERE id=?", (fill["id"],)).fetchone()
            if old:
                if old[0] != encoded:
                    raise ValidationError("相同成交ID内容不同")
                return self.account()
            account = self.account()
            if account["version"] != expected_version:
                raise ValidationError("账户版本已变化")
            if at < timestamp(account["confirmed_at"]):
                raise ValidationError("成交时间早于已确认快照，请对账后导入新快照")
            if at.date() != timestamp(account["confirmed_at"]).date():
                raise ValidationError("跨交易日请先导入券商最新快照，确认T+1可卖数量")
            holding = account["holdings"].get(fill["symbol"],
                       {"shares": 0, "available": 0, "cost": 0,
                        "board": fill["board"], "industry": fill.get("industry", "未知")})
            if holding["board"] != fill["board"]:
                raise ValidationError("成交板块不匹配")
            amount = cents(fill["price"]) * fill["quantity"]
            cost = cents(fill["fee"])
            cash = cents(account["cash"])
            if fill["side"] == "BUY":
                if amount + cost > cash:
                    raise ValidationError("成交后现金不足，请核对账户")
                holding["cost"] = (cents(holding["cost"]) * holding["shares"] + amount + cost) / (
                    holding["shares"] + fill["quantity"]) / 100
                holding["shares"] += fill["quantity"]
                cash -= amount + cost
            else:
                if fill["quantity"] > holding["available"]:
                    raise ValidationError("实际卖出超过可卖数量")
                holding["shares"] -= fill["quantity"]
                holding["available"] -= fill["quantity"]
                cash += amount - cost
            account["cash"] = cash / 100
            account["holdings"][fill["symbol"]] = holding
            account["version"] += 1
            account["confirmed_at"] = fill["at"]
            self._save_account(account)
            self.db.execute("INSERT INTO fills VALUES (?,?)", (fill["id"], encoded))
        return account

    def claim_email(self, run_id):
        with self.transaction():
            result = self.db.execute("UPDATE runs SET email_status='sending' "
                                     "WHERE id=? AND email_status='pending'", (run_id,))
            if result.rowcount != 1:
                raise ValidationError("邮件已发送、正在发送或结果未知，请人工核对，禁止重复发送")

    def email_status(self, run_id, status):
        with self.db:
            self.db.execute("UPDATE runs SET email_status=? WHERE id=?", (status, run_id))

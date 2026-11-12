"""Local research holdings; separate from the execution/account ledger."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .domain import SHANGHAI, ValidationError


class Positions:
    def __init__(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.directory = Path(directory)
        self.db = sqlite3.connect(Path(directory) / "positions.sqlite", timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS positions (
            code TEXT NOT NULL, kind TEXT NOT NULL, shares INTEGER NOT NULL,
            cost TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(code,kind));
          CREATE TABLE IF NOT EXISTS position_history (
            id INTEGER PRIMARY KEY, body TEXT NOT NULL, changed_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS security_names (
            code TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
            source TEXT NOT NULL, observed_at TEXT NOT NULL, PRIMARY KEY(code,kind));
        ''')

    def close(self):
        self.db.close()

    def all(self):
        self.sync_names_from_catalogs()
        return [dict(row) for row in self.db.execute('''
          SELECT p.*, COALESCE(n.name, '') AS name
          FROM positions p LEFT JOIN security_names n ON p.code=n.code AND p.kind=n.kind
          ORDER BY p.kind,p.code
        ''')]

    def remember_name(self, code, kind, name, source="行情名称目录", observed_at=None):
        if (not re.fullmatch(r"[0-9]{6}", str(code)) or kind not in ("etf", "stock")
                or not isinstance(name, str) or not name.strip() or len(name) > 200
                or any(c in name for c in ("\n", "\r", "\x00")) or name.strip() == code):
            raise ValidationError("证券名称未核实，保留代码等待下次联网识别")
        with self.db:
            self.db.execute('''
              INSERT INTO security_names VALUES(?,?,?,?,?)
              ON CONFLICT(code,kind) DO UPDATE SET
                name=excluded.name,source=excluded.source,observed_at=excluded.observed_at
              WHERE excluded.observed_at >= security_names.observed_at AND
                (security_names.name != excluded.name OR security_names.observed_at != excluded.observed_at)
            ''', (code, kind, name.strip(), source, observed_at or datetime.now(SHANGHAI).isoformat()))

    def sync_names_from_catalogs(self):
        """Backfill old holdings from successful downloads; opening stays offline."""
        for kind in ("etf", "stock"):
            path = self.directory / "data" / f"catalog-{kind}.json"
            try:
                catalog = json.loads(path.read_text(encoding="utf-8"))
                names = catalog["names"]
                if not isinstance(names, dict):
                    continue
                observed_at = datetime.fromtimestamp(path.stat().st_mtime, SHANGHAI).isoformat()
            except (OSError, ValueError, KeyError, TypeError):
                continue
            for row in self.db.execute("SELECT code FROM positions WHERE kind=?", (kind,)).fetchall():
                name = names.get(row["code"])
                if name:
                    try:
                        self.remember_name(row["code"], kind, name, "已下载的证券目录", observed_at)
                    except ValidationError:
                        pass


    def save(self, code, kind, shares, cost):
        if not re.fullmatch(r"\d{6}", str(code)) or kind not in ("etf", "stock"):
            raise ValidationError("需要6位代码及etf/stock类型")
        if isinstance(shares, bool) or not isinstance(shares, int) or shares < 0 or shares > 1_000_000_000:
            raise ValidationError("数量必须为0至10亿之间的整数；0表示已清仓")
        try:
            value = Decimal(str(cost))
        except InvalidOperation:
            raise ValidationError("成本价格无效") from None
        if not value.is_finite() or value < 0 or value > 1_000_000 or (shares and value <= 0):
            raise ValidationError("持仓成本须为正数；清仓可填0")
        if value != value.quantize(Decimal("0.0001")):
            raise ValidationError("成本最多保留4位小数")
        record = {"code": code, "kind": kind, "shares": shares, "cost": format(value, ".4f"),
                  "updated_at": datetime.now(SHANGHAI).isoformat()}
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO positions VALUES(:code,:kind,:shares,:cost,:updated_at)", record)
            self.db.execute("INSERT INTO position_history(body,changed_at) VALUES(?,?)",
                            (json.dumps(record, ensure_ascii=False), record["updated_at"]))
        return record


def complete_names(directory, provider=None):
    """Name-only public lookup. Uses its own connection; never edits holdings."""
    store = Positions(directory)
    try:
        rows = store.all()
        if not rows:
            return "请先保存至少一只证券的持仓，再补全名称。"
        if provider is None:
            from .watch import AKDaily
            provider = AKDaily(Path(directory) / "data")
        succeeded, cached, failed = 0, 0, 0
        for row in rows:
            try:
                code, name = provider.resolve(row["code"], row["kind"])
                if code != row["code"]:
                    raise ValidationError("证券代码与名称不匹配")
                suffix = "（名称沿用缓存）"
                used_cache = isinstance(name, str) and name.endswith(suffix)
                canonical = name.removesuffix(suffix) if isinstance(name, str) else name
                store.remember_name(code, row["kind"], canonical)
                succeeded += 1
                cached += int(used_cache)
            except Exception:
                # A failed lookup cannot delete a previously verified name.
                failed += 1
        message = f"已记住 {succeeded} 个证券名称，后续可直接从列表选择。"
        if cached:
            message += f"其中 {cached} 个沿用已保存的名称。"
        if failed:
            message += f"另有 {failed} 个暂未取得新名称，已保留原记录，可稍后重试。"
        return message
    finally:
        store.close()


def local_summary(position):
    total = Decimal(position["cost"]) * position["shares"]
    return (f"{position['code']}：{position['shares']}份/股；每份/股成本{position['cost']}元；"
            f"按录入成本计算总额{total:.2f}元。")


def position_cli(directory, action, code=None, kind="etf", shares=None, cost=None):
    store = Positions(directory)
    try:
        if action == "edit":
            print("录入当前持仓快照；不会向券商下单，也不会修改真实成交账本。")
            code = input("股票/ETF代码：").strip()
            kind = input("类型 etf或stock（回车为etf）：").strip() or "etf"
            shares = int(input("当前持有份数/股数（清仓填0）：").strip())
            cost = input("券商显示的每份/股成本，最多4位小数：").strip()
            action = "set"
        if action == "set":
            row = store.save(code, kind, shares, cost)
            print("已保存：" + local_summary(row))
        elif action == "list":
            rows = store.all()
            print("\n".join(local_summary(row) for row in rows) or "尚未录入持仓")
    finally:
        store.close()

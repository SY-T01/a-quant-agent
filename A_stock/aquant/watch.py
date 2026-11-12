"""Automatic daily data -> quantitative evidence -> DeepSeek -> email pipeline."""
from __future__ import annotations

import getpass
import copy
import hashlib
import html
import json
import os
import re
import smtplib
import sqlite3
import ssl
import time
import signal
import threading
import tempfile
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from .domain import SHANGHAI, ValidationError
from .etf import summarize
from .llm import SYSTEM, synthesize, validate_synthesis
from .positions import Positions, local_summary
from .lifecycle import WatchLock
from .sources import SourcePool, UnsupportedSource, PROVIDER_LABELS

DAILY_PROMPT = SYSTEM + '''
本次是股票或场内ETF日频研究，不是盘中成交指令。输入不含用户真实账户。
BUY解释为关注买入机会，SELL为已有持仓者的减仓倾向，HOLD为持有或观望。
必须解释各量化指标分歧；不得编造新闻、实时价格、基金净值、折溢价或收益概率。
普通股票如附有selector_research，必须同时参考行业与逐股复核；暂缓、否决、待复核或历史意见不能升级为买入倾向。
如NAV/溢价、行业基本面或账户资料缺失，应在uncertainty明确写出。
基金不使用普通股票PE/ROE推断其价值。本次禁止给股数。
'''


class SourceRateLimited(ValidationError):
    """Data-provider circuit breaker, independent of model and mail retries."""
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def rate_limit_message(until):
    return (f"东方财富疑似限流或主动断连，主源冷却至北京时间{until:%m-%d %H:%M}。"
            "请勿连续重启或重试；这不是DeepSeek或邮箱错误。历史行情将尝试独立备用源；均不可用时不生成建议。")


def configure(path):
    target = Path(path)
    if target.exists():
        raise ValidationError("配置已存在，请编辑本地配置；不会覆盖已有密钥")
    print("自动日报配置：跟踪159567，交易日14:00；日线依据为上一交易日，密钥仅存本机。")
    values = {}
    for key, label in (("DEEPSEEK_API_KEY", "DeepSeek API密钥"), ("SMTP_HOST", "SMTP服务器"),
                       ("SMTP_USER", "SMTP登录邮箱"), ("SMTP_PASSWORD", "SMTP授权码/应用密码"),
                       ("SMTP_FROM", "发件邮箱"), ("SMTP_TO", "收件邮箱")):
        values[key] = getpass.getpass(label + "：") if key.endswith(("KEY", "PASSWORD")) else input(label + "：").strip()
        if not values[key]:
            raise ValidationError("配置不能为空，未保存任何密钥")
    values["SMTP_PORT"] = "465"
    model = input("DeepSeek模型名（回车用deepseek-flash）：").strip() or "deepseek-flash"
    target.parent.mkdir(parents=True, exist_ok=True)
    secret = target.parent / "watch-secrets.json"
    if secret.exists():
        raise ValidationError("密钥文件已存在，请手动核对；不覆盖")
    fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(values, stream)
    body = {"instruments": [{"query": "159567", "type": "etf"}], "schedule": ["14:00"],
            "start_date": "20240101", "model": model, "state_dir": str(target.parent / "watch"),
            "secrets_file": str(secret)}
    target.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已保存本地配置：{target}。下一步：./run.sh watch --config {target} --once")


def credentials(cfg):
    values = {}
    if cfg.get("secrets_file") and Path(cfg["secrets_file"]).exists():
        path = Path(cfg["secrets_file"])
        if path.stat().st_mode & 0o077:
            raise ValidationError("密钥文件权限过宽，请运行chmod 600后重试")
        values = json.loads(path.read_text())
    for key in ("DEEPSEEK_API_KEY", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO", "SMTP_PORT"):
        if os.getenv(key):
            values[key] = os.environ[key]
    return values


def check_config(cfg):
    if not cfg.get("instruments") or len(cfg["instruments"]) > 20:
        raise ValidationError("跟踪列表需包含1至20个标的")
    for item in cfg["instruments"]:
        if item.get("type") not in ("etf", "stock") or not isinstance(item.get("query"), str) or not item["query"].strip():
            raise ValidationError("标的需要query及etf/stock类型")
    if not cfg.get("schedule") or len(cfg["schedule"]) != 1:
        raise ValidationError("每天需设置且仅设置一次发送时间")
    for schedule in cfg["schedule"]:
        if not re.fullmatch(r"[0-2]\d:[0-5]\d", schedule) or not (
                "09:35" <= schedule <= "11:15" or "13:05" <= schedule <= "14:40" or "18:00" <= schedule <= "23:59"):
            raise ValidationError("发送时间需为09:35—11:15、13:05—14:40或18:00—23:59；日线不代表实时判断")


class AKDaily:
    def __init__(self, directory):
        import akshare as ak
        self.ak = ak
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)

    def _source(self, call, **kwargs):
        """Persist a conservative 60-minute cooldown; restarts cannot bypass it."""
        path = self.root / "eastmoney-cooldown.json"
        now = datetime.now(SHANGHAI)
        if path.exists():
            saved = json.loads(path.read_text())
            until = datetime.fromisoformat(saved["retry_after"]).astimezone(SHANGHAI)
            if now < until:
                raise SourceRateLimited(rate_limit_message(until), until)
        try:
            return call(**kwargs)
        except Exception as exc:
            if type(exc).__name__ != "Push2RateLimitError":
                raise
            until = now + timedelta(minutes=60)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps({"source": "Eastmoney", "detected_at": now.isoformat(),
                                        "retry_after": until.isoformat(), "reason": "Push2RateLimitError"}))
            temp.replace(path)
            raise SourceRateLimited(rate_limit_message(until), until) from None

    def calendar(self):
        cache = self.root / "calendar.json"
        today = datetime.now(SHANGHAI).date().isoformat()
        if cache.exists():
            saved = json.loads(cache.read_text())
            if saved["fetched_on"] == today:
                return saved["dates"]
        dates = sorted({str(d)[:10] for d in self.ak.tool_trade_date_hist_sina()["trade_date"]})
        cache.write_text(json.dumps({"fetched_on": today, "dates": dates}))
        return dates

    def resolve(self, query, kind):
        cache = self.root / f"catalog-{kind}.json"
        today = datetime.now(SHANGHAI).date().isoformat()
        catalog = json.loads(cache.read_text()) if cache.exists() else {}
        # Symbol/name references need not be fetched daily for a known exact code.
        # This never relaxes the separate history-date validation.
        if (re.fullmatch(r"\d{6}", query) and query in catalog.get("names", {})
                and catalog.get("date")):
            age = (datetime.fromisoformat(today) - datetime.fromisoformat(catalog["date"])).days
            if 0 <= age <= 7:
                return query, catalog["names"][query]
        if catalog.get("date") != today:
            try:
                if kind == "etf":
                    frame = self._source(self.ak.fund_name_em)
                    names = {str(r["基金代码"]).zfill(6): str(r["基金简称"]) for _, r in frame.iterrows()
                             if "ETF" in str(r["基金简称"]).upper() and "联接" not in str(r["基金简称"])}
                else:
                    frame = self._source(self.ak.stock_info_a_code_name)
                    names = {str(r["code"]).zfill(6): str(r["name"]) for _, r in frame.iterrows()}
            except (OSError, ValueError, KeyError, TypeError):
                if re.fullmatch(r"\d{6}", query) and query in catalog.get("names", {}):
                    return query, catalog["names"][query] + "（名称沿用缓存）"
                raise ValidationError("名称目录不可用且无法核实此代码；不猜测标的名称或类型") from None
            catalog = {"date": today, "names": names}
            fd, temp = tempfile.mkstemp(prefix="." + cache.name, dir=self.root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(catalog, stream, ensure_ascii=False)
                os.replace(temp, cache)
            finally:
                Path(temp).unlink(missing_ok=True)
        names = catalog["names"]
        matches = [(c, n) for c, n in names.items() if query == c or query == n]
        if not matches:
            matches = [(c, n) for c, n in names.items() if query in n]
        if len(matches) != 1:
            raise ValidationError("标的检索无唯一结果，请填写准确代码")
        return matches[0]

    def history(self, code, kind, start, end):
        import pandas as pd
        target = self.root / f"{kind}-{code}.qfq.csv"
        metadata = target.with_suffix(".meta.json")
        # Daily bars are reusable for the same completed session. Never accept
        # an earlier session as today's result; stamp the original fetch time.
        candidates = [(target, metadata)]
        if kind == "etf":
            candidates.append((Path("runtime/etf") / f"{code}.daily.qfq.csv",
                               Path("runtime/etf") / f"{code}.summary.json"))
        today = datetime.now(SHANGHAI).date().isoformat()
        for csv_path, meta_path in candidates:
            if csv_path.exists() and meta_path.exists():
                meta = json.loads(meta_path.read_text())
                if (meta.get("symbol") == code and meta.get("adjust") == "qfq"
                        and meta.get("last_date") == end
                        and str(meta.get("fetched_at", ""))[:10] == today
                        and meta.get("request_start", "20240101") <= start):
                    cached = pd.read_csv(csv_path)
                    stats = summarize(cached)
                    if stats["last_date"] == end and stats["bars"] == meta.get("bars"):
                        cached.attrs["provenance"] = {"source": meta.get("source", "AKShare/Eastmoney"),
                                                        "fetched_at": meta["fetched_at"], "cache": True,
                                                        "fallback_reason": meta.get("fallback_reason"),
                                                        "attempts": meta.get("attempts", [])}
                        return cached
        call = self.ak.fund_etf_hist_em if kind == "etf" else self.ak.stock_zh_a_hist
        def validate(frame):
            selected = frame[(frame["日期"].astype(str) <= end)
                             & (frame["日期"].astype(str) >= f"{start[:4]}-{start[4:6]}-{start[6:]}")].copy()
            stats = summarize(selected)
            if stats["last_date"] != end:
                raise ValidationError("历史行情末日未达到所需交易日")
            return selected, stats
        pool = SourcePool(self.root)
        try:
            frame, stats, provider, attempts = pool.fetch(f"{kind}:{code}:qfq", {
                "eastmoney": lambda: self._source(call, symbol=code, period="daily", start_date=start,
                                                  end_date=end.replace("-", ""), adjust="qfq"),
                "tencent": lambda: self._tencent_history(code, kind, start, end),
                "baostock": lambda: self._baostock_history(code, kind, start, end)}, validate)
        finally:
            pool.close()
        source = PROVIDER_LABELS[provider]
        failures = [a for a in attempts if a["status"] != "success"]
        fallback = failures[0].get("error", failures[0]["status"]) if failures else None
        tmp = target.with_suffix(".tmp")
        frame.to_csv(tmp, index=False, encoding="utf-8-sig")
        tmp.replace(target)
        fetched = datetime.now(SHANGHAI).isoformat()
        metadata.write_text(json.dumps({**stats, "symbol": code, "adjust": "qfq", "request_start": start,
                                        "source": source, "fetched_at": fetched, "fallback_reason": fallback,
                                        "attempts": attempts}))
        frame.attrs["provenance"] = {"source": source, "fetched_at": fetched, "cache": False,
                                     "fallback_reason": fallback, "attempts": attempts}
        return frame

    def _tencent_history(self, code, kind, start, end):
        if not re.fullmatch(r"\d{6}", code):
            raise ValidationError("备用源需要准确证券代码")
        if kind == "etf":
            market = "sz" if code.startswith(("15", "16")) else "sh" if code.startswith(("50", "51", "52", "56", "58")) else None
        else:
            market = "sh" if code.startswith("6") else "sz" if code.startswith(("0", "3")) else None
        if not market:
            raise UnsupportedSource("此证券市场尚未验证腾讯备用接口")
        frame = self.ak.stock_zh_a_hist_tx(symbol=market + code, start_date=start,
                                         end_date=end.replace("-", ""), adjust="qfq")
        return frame.rename(columns={"date": "日期", "open": "开盘", "close": "收盘",
                                     "high": "最高", "low": "最低", "amount": "成交额",
                                     "volume": "成交量", "turnover": "换手率"})

    def _baostock_history(self, code, kind, start, end):
        import importlib.util
        import subprocess
        import sys
        import pandas as pd
        if not re.fullmatch(r"\d{6}", code) or not (
            kind == "etf" and code.startswith(("15", "16", "50", "51", "52", "56", "58"))
            or kind == "stock" and code.startswith(("0", "3", "6"))):
            raise UnsupportedSource("此证券市场尚未验证BaoStock")
        if importlib.util.find_spec("baostock") is None:
            raise UnsupportedSource("未安装BaoStock依赖")
        # The SDK uses a global socket and internal retries. Isolate it so a
        # hung server cannot hold up every other source or the daily task.
        result = subprocess.run([sys.executable, "-m", "aquant.baostock_worker", code, kind, start, end],
                                capture_output=True, text=True, timeout=25)
        if result.returncode != 0:
            raise ValidationError("BaoStock查询失败或未返回完整数据")
        body = json.loads(result.stdout)
        return pd.DataFrame(body["data"], columns=body["columns"])

    def _baostock_history_direct(self, code, kind, start, end):
        import contextlib
        import io
        import socket
        import pandas as pd
        try:
            import baostock as bs
            import baostock.common.context as context
        except ImportError:
            raise UnsupportedSource("未安装可选BaoStock依赖") from None
        if kind == "etf":
            market = "sz" if code.startswith(("15", "16")) else "sh" if code.startswith(("50", "51", "52", "56", "58")) else None
        else:
            market = "sh" if code.startswith("6") else "sz" if code.startswith(("0", "3")) else None
        if not market:
            raise UnsupportedSource("此证券市场尚未验证BaoStock")
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(15)
        logged_in = False
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                login = bs.login()
                if login.error_code != "0":
                    raise ValidationError("BaoStock匿名登录失败")
                logged_in = True
                result = bs.query_history_k_data_plus(market + "." + code,
                    "date,open,high,low,close,volume,amount", start_date=f"{start[:4]}-{start[4:6]}-{start[6:]}",
                    end_date=end, frequency="d", adjustflag="2")
                if result.error_code != "0":
                    raise ValidationError("BaoStock历史数据查询失败")
                rows = []
                while result.next():
                    rows.append(result.get_row_data())
                if result.error_code != "0":
                    raise ValidationError("BaoStock历史数据读取不完整")
                frame = pd.DataFrame(rows, columns=result.fields)
        finally:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    if logged_in:
                        bs.logout()
            finally:
                sock = getattr(context, "default_socket", None)
                if sock is not None:
                    sock.close()
                socket.setdefaulttimeout(old_timeout)
        for col in ("open", "high", "low", "close", "volume", "amount"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        return frame.rename(columns={"date": "日期", "open": "开盘", "close": "收盘", "high": "最高",
                                     "low": "最低", "volume": "成交量", "amount": "成交额"})


def packet_for(items, cutoff, now):
    stocks = []
    for item in items:
        stats = item["stats"]
        key = f"{item['type']}:{item['code']}"
        trend = 1 if stats["last_close"] > stats["ma20"] > stats["ma60"] else -1 if stats["last_close"] < stats["ma20"] < stats["ma60"] else 0
        stocks.append({"id": f"quant:{key}", "symbol": key, "name": item["name"],
                       "asset_type": item["type"], "trend": trend, "momentum20": stats["return20"],
                       "risk": stats["volatility20_annualized"], "forced_exit": False, "statistics": stats})
    return {"as_of": now.isoformat(), "data_date": cutoff, "template_version": "daily-v1",
            "scope": "指定标的前复权日频研究，非盘中报价或账户操作指令", "stocks": stocks, "news": [],
            "missing": ["账户及交易权限", "基金净值及折溢价", "实时盘口", "资讯及基本面"]}


def review_synthesis(packet, synthesis):
    """A simple, versioned research constraint, not a calibrated profit model."""
    final = copy.deepcopy(synthesis)
    signals = {s["symbol"]: s for s in packet["stocks"]}
    changes = []
    for rec in final["recommendations"]:
        s = signals[rec["symbol"]]
        research = s.get("selector_research")
        if rec["action"] == "BUY" and research and not research["allow_buy_tendency"]:
            changes.append({"symbol": rec["symbol"], "from": "BUY", "to": "HOLD",
                            "reason": "选股研究尚未通过有效的本期复核：" + research["label"]})
            rec["action"] = "HOLD"
            rec["reason"] = changes[-1]["reason"] + "。原模型理由：" + rec["reason"]
        if rec["action"] == "BUY" and s["trend"] < 0 and s["momentum20"] < 0:
            changes.append({"symbol": rec["symbol"], "from": "BUY", "to": "HOLD",
                            "reason": "下行均线结构与负20日动量同时出现，暂停新增买入倾向"})
            rec["action"] = "HOLD"
            rec["reason"] = "量化约束拦截买入倾向：" + changes[-1]["reason"] + "。原模型理由：" + rec["reason"]
    return final, changes


def format_daily(packet, synthesis, positions=None):
    lines = [f"A-Quant 自动量化日报｜数据截至{packet['data_date']}",
             "量化计算 + DeepSeek综合判断；日频研究，非盘中交易指令。",
             "注意：盘中收到此报告，也不包含今天的价格变化；不得直接作为实时下单依据。", "", synthesis["summary"]]
    lookup = {r["symbol"]: r for r in synthesis["recommendations"]}
    for item in packet["stocks"]:
        rec = lookup[item["symbol"]]
        label = {"BUY": "买入倾向", "SELL": "卖出/减仓倾向（已有持仓）", "HOLD": "不动/观望"}[rec["action"]]
        stats = item["statistics"]
        lines += ["", f"{item['name']}（{item['symbol'].split(':')[1]}）：{label}",
                  f"依据：{rec['reason']}", f"不确定性：{rec['uncertainty']}",
                  f"前复权收盘{stats['last_close']:.3f}；MA20 {stats['ma20']:.3f}；MA60 {stats['ma60']:.3f}。",
                  f"5/20/60交易日涨跌：{stats['return5']:+.2%} / {stats['return20']:+.2%} / {stats['return60']:+.2%}。"]
        research = item.get("selector_research")
        if research:
            lines += ["选股研究复核：" + research["label"] + "；" + research.get("reason", ""),
                      "研究行业数据截止：" + str(research.get("data_at") or "不可用") + "；不代表当日日线或实时资料。"]
            if research.get("entry_condition"):
                lines.append("仍需人工核对的研究条件：" + research["entry_condition"])
    if positions:
        codes = {(s["asset_type"], s["symbol"].split(":")[1]) for s in packet["stocks"]}
        for position in positions:
            if (position["kind"], position["code"]) in codes:
                lines += ["", "本地持仓记录：" + local_summary(position),
                          f"持仓录入时间：{position['updated_at']}。实际成交后请更新。",
                          "尚未取得独立未复权估值价格，不将前复权研究价格用于持仓盈亏。"]
    sources = sorted({s["statistics"].get("provenance", {}).get("source", "AKShare/来源未标注") for s in packet["stocks"]})
    lines += ["", "现金、可卖数量和交易权限未核对，不计算买卖份额；未核验实时净值和折溢价。",
              "数据来源：" + "；".join(sources) + "。策略尚未完成独立收益验证。"]
    if any(s["statistics"].get("provenance", {}).get("fallback_reason") for s in packet["stocks"]):
        lines.append("本次因主源异常切换备用行情；已核验数据日期和字段，未拼接不同来源价格。")
    for s in packet["stocks"]:
        provenance = s["statistics"].get("provenance", {})
        if provenance.get("cache"):
            lines.append(f"{s['name']}：复用已校验缓存，原获取时间{provenance.get('fetched_at', '未标注')}。")
        if provenance.get("attempts"):
            states = {"success": "成功", "failed": "失败后换源", "cooldown": "冷却跳过", "unsupported": "不支持此标的"}
            lines.append("数据获取顺序：" + " → ".join(
                PROVIDER_LABELS.get(a["provider"], a["provider"]) + "（" + states.get(a["status"], a["status"]) + "）"
                for a in provenance["attempts"]))
    return "\n".join(lines)


def smtp_daily(text, subject, key, secrets):
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = secrets["SMTP_FROM"], secrets["SMTP_TO"], subject
    message["Message-ID"] = f"<{key}@aquant.local>"
    message.set_content(text)
    with smtplib.SMTP_SSL(secrets["SMTP_HOST"], int(secrets.get("SMTP_PORT", "465")),
                          timeout=30, context=ssl.create_default_context()) as client:
        client.login(secrets["SMTP_USER"], secrets["SMTP_PASSWORD"])
        if client.send_message(message):
            raise ValidationError("收件人未被全部接受")


def cycle(cfg, now, scheduled=True, preview=False, provider=None, synthesizer=None, sender=None, retry_failed=False,
          should_stop=None, monotonic=None):
    monotonic = monotonic or time.monotonic
    began = monotonic()
    should_stop = should_stop or (lambda: False)
    check_config(cfg)
    now = now.astimezone(SHANGHAI)
    day = now.date().isoformat()
    def check_sending_window():
        if should_stop():
            raise ValidationError("任务已取消，未发信")
        deadline = 900
        if scheduled:
            deadline -= (now - send_at).total_seconds()
        if monotonic() - began > deadline:
            raise ValidationError("处理耗时超过建议发送窗口，未发信")
    if scheduled:
        send_at = datetime.fromisoformat(f"{day}T{cfg['schedule'][0]}:00").replace(tzinfo=SHANGHAI)
        elapsed = (now - send_at).total_seconds()
        if elapsed < 0:
            return "未到发送时间，无操作"
        if elapsed > 900:
            return "已错过15分钟发送窗口，不补发过期建议"
    check_sending_window()
    root = Path(cfg["state_dir"])
    root.mkdir(parents=True, exist_ok=True)
    values = credentials(cfg)
    required = ["DEEPSEEK_API_KEY"] + ([] if preview else ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO"])
    if any(not values.get(k) for k in required):
        raise ValidationError("本机密钥或邮箱未配置，请先运行watch-setup；密钥无需发到聊天")
    provider = provider or AKDaily(root / "data")
    synthesizer = synthesizer or synthesize
    sender = sender or smtp_daily
    last_allowed = day if now.hour >= 18 else (now.date() - timedelta(days=1)).isoformat()
    calendar = provider.calendar()
    if not calendar or max(calendar) < day:
        raise ValidationError("交易日历未覆盖今天，暂停自动建议")
    if scheduled and day not in calendar:
        return "非交易日，无操作"
    cutoff = max((d for d in calendar if d <= last_allowed), default=None)
    if cutoff is None:
        raise ValidationError("找不到已完成交易日")
    key = hashlib.sha256(json.dumps(["daily-v2", day, cutoff, cfg["instruments"], values.get("SMTP_TO"), preview], sort_keys=True).encode()).hexdigest()[:24]
    db = sqlite3.connect(root / "watch.sqlite", timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,status TEXT,updated_at TEXT,error TEXT,attempts INTEGER NOT NULL DEFAULT 0)")
    try:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT status,updated_at,attempts FROM jobs WHERE id=?", (key,)).fetchone()
        if old and old[0] in ("accepted", "preview", "sending", "unknown", "running"):
            db.rollback()
            return f"已有任务状态{old[0]}，不重复调用或发信；异常中断需人工核对"
        if old and old[0] == "failed" and not retry_failed and (now - datetime.fromisoformat(old[1])).total_seconds() < 900:
            db.rollback()
            return "失败后冷却15分钟，避免连续消耗API"
        if old and old[2] >= 3:
            db.rollback()
            return "失败后已达当日3次尝试上限，请检查配置"
        db.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?)", (key, "running", now.isoformat(), "", old[2] + 1 if old else 1))
        db.commit()
        items = []
        stage = "数据采集"
        for instrument in cfg["instruments"]:
            code, name = provider.resolve(instrument["query"], instrument["type"])
            name_store = Positions(root)
            try:
                name_store.remember_name(code, instrument["type"], name.removesuffix("（名称沿用缓存）"))
            finally:
                name_store.close()
            frame = provider.history(code, instrument["type"], cfg["start_date"], cutoff)
            stats = summarize(frame)
            stats["provenance"] = frame.attrs.get("provenance", {})
            if stats["last_date"] != cutoff:
                raise ValidationError("行情未更新至最近已完成交易日，暂停推荐")
            items.append({"code": code, "name": name, "type": instrument["type"], "stats": stats})
        packet = packet_for(items, cutoff, now)
        from .research_link import attach_research
        attach_research(packet, cfg.get("research_project"))
        check_sending_window()
        stage = "DeepSeek综合"
        old_key = os.environ.get("DEEPSEEK_API_KEY")
        os.environ["DEEPSEEK_API_KEY"] = values["DEEPSEEK_API_KEY"]
        try:
            synthesis, audit = synthesizer(packet, cfg["model"], allow_api=True, system_prompt=DAILY_PROMPT)
            validate_synthesis(synthesis, packet)
        finally:
            if old_key is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = old_key
        stage = "本地报告生成"
        original_synthesis = copy.deepcopy(synthesis)
        synthesis, review = review_synthesis(packet, synthesis)
        position_store = Positions(root)
        try:
            positions = position_store.all()
        finally:
            position_store.close()
        # Holdings stay local: only public quantitative evidence enters DeepSeek.
        text = format_daily(packet, synthesis, positions)
        (root / f"{key}.json").write_text(json.dumps({"packet": packet, "synthesis": synthesis, "original_synthesis": original_synthesis,
                                                   "review": review, "guardrail_version": "daily-guard-v2-selector", "llm_audit": audit, "local_positions": positions}, ensure_ascii=False, indent=2), encoding="utf-8")
        (root / f"{key}.txt").write_text(text, encoding="utf-8")
        (root / f"{key}.html").write_text('<meta charset="utf-8"><pre style="white-space:pre-wrap">' + html.escape(text) + '</pre>', encoding="utf-8")
        state = "preview"
        if not preview:
            check_sending_window()
            with db:
                db.execute("UPDATE jobs SET status='sending' WHERE id=?", (key,))
            try:
                sender(text, f"[A-Quant] {day}买卖持有建议（行情截至{cutoff}）", key, values)
            except BaseException as exc:
                with db:
                    db.execute("UPDATE jobs SET status='unknown',error='SMTP结果未知，人工核对邮箱' WHERE id=?", (key,))
                if not isinstance(exc, Exception):
                    raise
                raise ValidationError("SMTP发送结果未知，请核对邮箱；程序不会自动重发") from None
            state = "accepted"
        with db:
            db.execute("UPDATE jobs SET status=?,updated_at=? WHERE id=?", (state, now.isoformat(), key))
        return f"日报任务{state}；报告保存在{root}，不在聊天或终端输出分析正文"
    except BaseException as exc:
        # Persist useful diagnostics without storing remote error bodies or credentials.
        reason = (f"{stage if 'stage' in locals() else '任务初始化'}失败：" + str(exc)
                  if isinstance(exc, ValidationError)
                  else f"{stage if 'stage' in locals() else '任务初始化'}失败或取消（{type(exc).__name__}）；未发信")
        with db:
            db.execute("UPDATE jobs SET status='failed',error=? WHERE id=? AND status='running'", (reason, key))
        raise
    finally:
        db.close()


def manual_result(directory, message):
    if message.startswith("日报任务accepted"):
        message = "SMTP服务器已接受邮件，请查收；最终送达请以收件箱为准。"
    elif message.startswith("已有任务状态accepted"):
        message = "同一份日报已发送，本次未重复发送。"
    elif message.startswith("已有任务状态"):
        message = "同一份日报已处理或发送结果待核对，本次未重复发送；请查看日志和邮箱。"
    target = Path(directory) / "manual-send-result.json"
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps({"at": datetime.now(SHANGHAI).strftime("%m-%d %H:%M:%S"),
                                "message": message}, ensure_ascii=False))
    temp.replace(target)


def manual_requested(directory, instance):
    path = Path(directory) / "manual-send-request.json"
    try:
        return json.loads(path.read_text()).get("instance") == instance
    except (OSError, ValueError, AttributeError):
        return False


def watch_main(path, once=False, preview=False, retry_failed=False):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    check_config(cfg)
    if retry_failed and not once:
        raise ValidationError("手动重试仅允许与--once一起使用")
    with WatchLock(cfg["state_dir"], "once" if once else "scheduled") as lock:
        if os.environ.get("AQUANT_PARENT_PIPE") == "1":
            def parent_pipe():
                try:
                    # The desktop parent holds the write side; EOF on app exit
                    # or crash cancels the watch. CLI launches never read stdin.
                    import sys
                    sys.stdin.buffer.read(1)
                finally:
                    lock.cancelled = True
            threading.Thread(target=parent_pipe, daemon=True).start()
        previous_signal = signal.signal(signal.SIGTERM, lambda *_: setattr(lock, "cancelled", True))
        try:
            print(f"自动任务已在当前终端启动（PID {os.getpid()}）；Ctrl+C停止。", flush=True)
            while not lock.should_stop():
                manual = once or manual_requested(cfg["state_dir"], lock.instance)
                try:
                    latest = json.loads(Path(path).read_text(encoding="utf-8"))
                    check_config(latest)
                    if Path(latest["state_dir"]).resolve() != Path(cfg["state_dir"]).resolve():
                        raise ValidationError("运行期间不能修改state_dir，请停止后重启")
                    result = cycle(latest, datetime.now(SHANGHAI), scheduled=not manual, preview=preview,
                                   retry_failed=retry_failed or manual, should_stop=lock.should_stop)
                    if manual:
                        manual_result(cfg["state_dir"], result)
                    if once or not result.startswith(("未到", "已错过", "非交易日", "已有任务", "失败后")):
                        print(datetime.now(SHANGHAI).isoformat(), result, flush=True)
                except Exception as exc:
                    print("自动日报异常：" + (str(exc) if isinstance(exc, ValidationError) else type(exc).__name__ + "，请检查网络或数据接口"), flush=True)
                    if manual:
                        message = str(exc) if isinstance(exc, ValidationError) else type(exc).__name__ + "，请查看日志"
                        manual_result(cfg["state_dir"], "未完成：" + message)
                    if once:
                        return 2
                finally:
                    if manual and not once:
                        (Path(cfg["state_dir"]) / "manual-send-request.json").unlink(missing_ok=True)
                if once:
                    return 0
                for _ in range(30):
                    if lock.should_stop() or manual_requested(cfg["state_dir"], lock.instance):
                        break
                    time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            signal.signal(signal.SIGTERM, previous_signal)
            print("本实例已停止；不会继续调用API或发送邮件。", flush=True)
    return 0


def check_data(path):
    """Public-data-only preflight; no API credentials or mail used."""
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    check_config(cfg)
    now = datetime.now(SHANGHAI)
    provider = AKDaily(Path(cfg["state_dir"]) / "data")
    dates = provider.calendar()
    if not dates or max(dates) < now.date().isoformat():
        raise ValidationError("交易日历未覆盖今天")
    allowed = now.date().isoformat() if now.hour >= 18 else (now.date() - timedelta(days=1)).isoformat()
    cutoff = max(d for d in dates if d <= allowed)
    for instrument in cfg["instruments"]:
        code, name = provider.resolve(instrument["query"], instrument["type"])
        frame = provider.history(code, instrument["type"], cfg["start_date"], cutoff)
        stats = summarize(frame)
        if stats["last_date"] != cutoff:
            raise ValidationError("数据日期未达预期")
        print(f"自动检索和下载通过：{code} {name}；{len(frame)}条；数据日期{cutoff}。未调用模型或发信。")

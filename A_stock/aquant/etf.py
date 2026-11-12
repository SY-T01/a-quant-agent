"""Download completed ETF daily bars; no portfolio edits or trade instructions."""
import argparse
import json
import math
import re
import socket
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def summarize(frame):
    required = ["日期", "开盘", "收盘", "最高", "最低", "成交额"]
    if frame.empty or any(c not in frame.columns for c in required):
        raise ValueError("接口没有返回所需日线字段")
    frame = frame.sort_values("日期").reset_index(drop=True)
    if frame["日期"].duplicated().any() or len(frame) < 61:
        raise ValueError("日线重复或不足61条")
    for _, row in frame.iterrows():
        if any(not math.isfinite(float(row[c])) or float(row[c]) <= 0 for c in required[1:]):
            raise ValueError("价格或成交额存在无效值")
        if not row["最低"] <= min(row["开盘"], row["收盘"]) <= max(row["开盘"], row["收盘"]) <= row["最高"]:
            raise ValueError("高低价关系不一致")
    close = frame["收盘"].astype(float)
    ma20 = float(close.tail(20).mean())
    ma60 = float(close.tail(60).mean())
    dd = close / close.cummax() - 1
    return {"bars": len(frame), "first_date": str(frame.iloc[0]["日期"]),
            "last_date": str(frame.iloc[-1]["日期"]), "last_close": float(close.iloc[-1]),
            "ma20": ma20, "ma60": ma60,
            "return5": float(close.iloc[-1] / close.iloc[-6] - 1),
            "return20": float(close.iloc[-1] / close.iloc[-21] - 1),
            "return60": float(close.iloc[-1] / close.iloc[-61] - 1),
            "max_drawdown_in_sample": float(dd.min()),
            "volatility20_annualized": float(close.pct_change().tail(20).std() * math.sqrt(252)),
            "trend": "收盘价高于20日及60日均线" if close.iloc[-1] > ma20 and close.iloc[-1] > ma60
            else "收盘价低于20日及60日均线" if close.iloc[-1] < ma20 and close.iloc[-1] < ma60
            else "价格与均线方向分歧"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="159567")
    p.add_argument("--start", default="20240101")
    p.add_argument("--output", default="runtime/etf")
    args = p.parse_args()
    if not re.fullmatch(r"\d{6}", args.symbol):
        p.error("symbol必须为6位基金代码")
    import akshare as ak
    # Exclude the current date even after close: this is completed-daily research.
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    end = (now.date() - timedelta(days=1)).strftime("%Y%m%d")
    socket.setdefaulttimeout(20)
    frame = ak.fund_etf_hist_em(symbol=args.symbol, period="daily", start_date=args.start,
                               end_date=end, adjust="qfq")
    frame = frame[frame["日期"].astype(str) < now.date().isoformat()].copy()
    stats = summarize(frame)
    stats.update({"symbol": args.symbol, "source": "AKShare/Eastmoney fund_etf_hist_em",
                  "adjust": "qfq", "fetched_at": now.isoformat(),
                  "daily_only": True,
                  "limitations": "前复权日频研究，非实时价格；未独立核验净值、溢价及跟踪误差；不生成交易股数"})
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / f"{args.symbol}.daily.qfq.csv", index=False, encoding="utf-8-sig")
    path = out / f"{args.symbol}.summary.json"
    path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"已保存：{path.resolve()}")


if __name__ == "__main__":
    main()

"""Generate explicit synthetic fixtures for CLI demonstrations."""
import copy
import json
from datetime import timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from aquant.data import demo_inputs
from aquant.strategy import baseline, evidence


def main():
    account, market, at = demo_inputs()
    cfg = json.loads((ROOT / "config/settings.json").read_text())
    directory = ROOT / "examples"
    directory.mkdir(exist_ok=True)
    next_market = copy.deepcopy(market)
    next_market["as_of"] = next_market["received_at"] = (at + timedelta(minutes=1)).isoformat()
    for row in next_market["stocks"]:
        row["execution_volume"] = 1_000_000
    files = {"account.demo.json": account, "market.demo.json": market,
             "synthesis.demo.json": baseline(evidence(market, account, cfg)),
             "replay.demo.json": {"account": account, "events": [market, next_market]},
             "fill.demo.json": {"id": "demo-execution-001", "symbol": "600000.SH",
                                "side": "BUY", "quantity": 200, "price": 10,
                                "fee": 5, "board": "MAIN", "industry": "演示行业A",
                                "at": at.isoformat()}}
    for name, body in files.items():
        (directory / name).write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成{len(files)}个虚构数据样例")


if __name__ == "__main__":
    main()

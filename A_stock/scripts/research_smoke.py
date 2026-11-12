"""Run the real selector with copied caches, blocked network and no personal writes."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "stock_selecter"


def worker(root):
    sys.path.insert(0, str(ROOT))
    from aquant.research_worker import run
    from aquant.selector_bridge import engine
    output = io.StringIO()
    with patch.object(socket.socket, "connect", side_effect=AssertionError("Unexpected network access")), redirect_stdout(output):
        run(root, "rescreen")
        report_path = root / "reports/sector_research/research.json"
        before = json.loads(report_path.read_text())
        load = engine(root)
        prefs = root / "configs/research_preferences.json"
        load("stock_selector.investment_horizon").save(12, prefs)
        run(root, "rescreen")
        after = json.loads(report_path.read_text())
        assert after["investment_horizon"]["months"] == 12
        assert after["decision_pipeline"]["review_current"] is False
        archives = list((root / "reports/desktop_runs").glob("*/previous-published/research.json"))
        assert len(archives) == 1
        assert json.loads(archives[0].read_text()) == before
        assert (report_path.parent / "index.html").stat().st_size > 1000
        assert (report_path.parent / "行业研究周报.pdf").stat().st_size > 1000
        run(root, "lab")
        run(root, "validation")
        assert (root / "reports/research_lab/index.html").exists()
        assert (root / "reports/research_validation/index.html").exists()
    return {"offline_rescreen": "passed", "changed_horizon": "passed", "previous_report_archived": True,
            "pdf_export": "passed", "lab_and_validation": "passed", "network_allowed": False,
            "paid_api_calls": 0, "candidate_count": len(after["stock_recommendations"]["stocks"])}


def main():
    if len(sys.argv) > 1:
        print(json.dumps(worker(Path(sys.argv[1])), ensure_ascii=False))
        return
    with tempfile.TemporaryDirectory(prefix="aquant-research-smoke-") as tmp:
        project = Path(tmp) / "selector"
        for relative in ("stock_selector", "configs", "data/normalized", "data/research"):
            shutil.copytree(SOURCE / relative, project / relative, ignore=shutil.ignore_patterns("__pycache__"))
        env = dict(os.environ)
        env.pop("DEEPSEEK_API_KEY", None)
        result = subprocess.run([sys.executable, __file__, str(project)], env=env, text=True, capture_output=True, timeout=120)
        if result.returncode:
            print(result.stderr[-5000:])
            raise SystemExit(result.returncode)
        data = json.loads(result.stdout)
        output = ROOT / "runtime/integration-qa"
        output.mkdir(parents=True, exist_ok=True)
        (output / "research-results.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()

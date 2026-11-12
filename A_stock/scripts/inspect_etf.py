"""Compatibility entry point; ETF formulas live in aquant.etf."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aquant.etf import main, summarize

if __name__ == "__main__":
    main()

"""Load the existing, neighbouring research engine without copying its data."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path


def engine(root):
    root = Path(root).resolve()
    if not (root / "stock_selector/__init__.py").is_file():
        raise ValueError("未找到选股研究程序，请保留与 A_stock 同级的 stock_selecter 文件夹")
    loaded = sys.modules.get("stock_selector")
    if loaded and Path(loaded.__file__).resolve().parent.parent != root:
        raise ValueError("选股研究程序路径冲突，请重新打开软件")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return importlib.import_module

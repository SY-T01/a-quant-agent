"""Export both applications as source, respecting all repository ignore rules."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_SUFFIXES = {".py", ".c", ".m", ".md", ".toml", ".json", ".sh", ".command", ".csv", ".txt"}
SPECIAL_NAMES = {".gitignore", ".gitattributes", ".env.example", "LICENSE", "NOTICE"}
TOKEN = re.compile(rb"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")


def source_files(root):
    # An empty temporary index lets Git apply root and per-project ignore rules
    # even before the user creates the actual repository.
    with tempfile.TemporaryDirectory(prefix="aquant-export-index-") as temporary:
        subprocess.run(["git", "init", "-q", temporary], check=True, capture_output=True)
        result = subprocess.run(["git", "--git-dir=" + str(Path(temporary) / ".git"),
            "--work-tree=" + str(root), "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=root, check=True, capture_output=True)
    for raw in sorted(result.stdout.split(b"\0")):
        if not raw:
            continue
        path = root / raw.decode("utf-8")
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix in ALLOWED_SUFFIXES or path.name in SPECIAL_NAMES:
            yield path


def local_private_values(root):
    """Compare locally configured credentials without copying or printing them."""
    config = root / "A_stock/runtime/watch-config.json"
    if not config.is_file():
        return []
    data = json.loads(config.read_text())
    secret = Path(data.get("secrets_file", root / "A_stock/runtime/watch-secrets.json"))
    if not secret.is_absolute():
        secret = root / "A_stock" / secret
    if not secret.is_file():
        return []
    values = json.loads(secret.read_text())
    return [str(values[key]).encode() for key in ("DEEPSEEK_API_KEY", "SMTP_PASSWORD", "SMTP_USER", "SMTP_FROM", "SMTP_TO")
            if values.get(key) and len(str(values[key])) >= 8]


def package(root=ROOT, output=None):
    root = Path(root).resolve()
    output = Path(output or root / "exports/a-quant-agent-source.zip").resolve()
    if output.exists():
        raise ValueError("输出文件已存在，请指定另一个 --output，避免覆盖已有备份")
    private_values = local_private_values(root)
    payloads, manifests, issues = [], [], []
    for path in source_files(root):
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        if TOKEN.search(payload) or any(value in payload for value in private_values):
            issues.append(relative)
            continue
        if path.suffix == ".py":
            ast.parse(payload, filename=relative)
        payloads.append((path, relative, payload))
        manifests.append({"path": relative, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    if issues:
        raise ValueError("发现疑似私人凭据，已停止打包；需检查文件：" + "、".join(issues))
    paths = {relative for _, relative, _ in payloads}
    for needed in ("README.md", ".gitignore", "A_stock/aquant/desktop.py", "A_stock/aquant/positions.py",
                   "A_stock/scripts/build_mac_app.py", "stock_selecter/stock_selector/sector_research.py"):
        if needed not in paths:
            raise ValueError("缺少必要源码：" + needed)
    for relative in paths:
        parts = Path(relative).parts
        if any(part in {"runtime", "data", "reports", ".venv", ".git"} or part.endswith(".app") for part in parts):
            raise ValueError("导出清单包含运行资料，停止打包：" + relative)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".aquant-export-", dir=output.parent) as temporary:
        staged = Path(temporary) / output.name
        with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, relative, payload in payloads:
                info = zipfile.ZipInfo.from_file(path, arcname="a-quant-agent/" + relative)
                archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("a-quant-agent/SOURCE_MANIFEST.json", json.dumps(manifests, ensure_ascii=False, indent=2))
        with zipfile.ZipFile(staged) as archive:
            if archive.testzip() is not None:
                raise ValueError("压缩包校验失败")
            for item in manifests:
                actual = archive.read("a-quant-agent/" + item["path"])
                if hashlib.sha256(actual).hexdigest() != item["sha256"]:
                    raise ValueError("文件内容校验失败：" + item["path"])
        staged.replace(output)
    return {"archive": str(output), "files": len(manifests), "bytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "private_state_included": False, "uploaded": False}


def main():
    parser = argparse.ArgumentParser(description="将两个项目导出为可上传 GitHub 的源码包")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(package(output=args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

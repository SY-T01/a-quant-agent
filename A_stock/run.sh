#!/bin/sh
set -eu
cd "$(dirname "$0")"
AQUANT_PYTHON="${AQUANT_PYTHON:-$(pwd)/.venv/bin/python}"
if [ ! -x "$AQUANT_PYTHON" ]; then
  AQUANT_PYTHON="/Users/shuoye_tan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
fi
if [ ! -x "$AQUANT_PYTHON" ]; then
  AQUANT_PYTHON="python3"
fi
exec "$AQUANT_PYTHON" -m aquant "$@"

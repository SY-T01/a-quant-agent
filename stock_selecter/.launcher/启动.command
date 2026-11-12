#!/bin/zsh
cd -- "$(dirname -- "$0")/.." || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo '未找到Python 3，请先安装Python后重新打开助手。'
  read 'reply?按回车关闭'
  exit 1
fi
python3 -B -m stock_selector.launcher

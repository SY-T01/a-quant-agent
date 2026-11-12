#!/bin/zsh
cd -- "$(dirname -- "$0")/../.." || exit 1
python3 -B -m stock_selector.weekly
if [[ $? -eq 0 ]]; then
  open reports/sector_research/index.html
else
  echo '本次更新未完成，请查看 reports/weekly/ 下最新日志；原报告已保留。'
fi

#!/bin/zsh
cd -- "$(dirname -- "$0")/../.." || exit 1
if [[ ! -f reports/sector_research/index.html ]]; then
  python3 -B -m stock_selector.sector_research || exit 1
fi
open reports/sector_research/index.html

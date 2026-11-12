#!/bin/zsh
cd -- "$(dirname -- "$0")/../.." || exit 1
python3 -B -m stock_selector.sector_research --refresh --enrich || python3 -B -m stock_selector.sector_research
open reports/sector_research/index.html

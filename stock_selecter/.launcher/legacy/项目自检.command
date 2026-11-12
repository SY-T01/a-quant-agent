#!/bin/zsh
cd -- "$(dirname -- "$0")/../.." || exit 1
python3 -B -m stock_selector.health

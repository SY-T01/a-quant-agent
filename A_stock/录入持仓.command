#!/bin/sh
cd "$(dirname "$0")" || exit 1
./run.sh positions
printf '\n按回车退出…'
read -r AQUANT_INPUT_DONE

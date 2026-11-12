#!/bin/zsh
cd -- "$(dirname -- "$0")/../.." || exit 1
python3 -B -c 'import getpass,os,sys; from stock_selector.sector_research import main; os.environ["DEEPSEEK_API_KEY"]=os.environ.get("DEEPSEEK_API_KEY") or getpass.getpass("请输入DeepSeek API密钥（不会显示或保存）："); print("正在请求DeepSeek解读，请稍候；本次仅调用一次，不自动重试。"); sys.argv=["sector_research","--ai"]; main()'
open reports/sector_research/index.html

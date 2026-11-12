# A Quant Agent

**0.4.0 已整合 stock_selecter：** 继续打开本目录的 **A-Quant.app**，在主窗口左侧选择“选股研究”。支持行业与候选股、研究期限和预算、模型复核、PDF 与前瞻验证，并可将候选加入日报关注；普通股票的日线买入倾向同时受研究复核约束。已有持仓和密钥沿用原位置。软件已打开时需完整退出后重开，自动日报需手动重新启动。详见[整合版使用说明](INTEGRATION_GUIDE.md)。

后续迭代方向：优先控制回撤，同时争取收益；从真实交易记录与复盘逐步推进到机器学习、深度学习及前瞻验证。详见[个人策略学习与迭代方案](PERSONAL_LEARNING_ROADMAP.md)，其中学习模块为规划功能。

**菜单栏版：双击项目中的A-Quant.app，右上角出现A图标，无需终端、不占程序坞。** 点击图标可查看日报状态、启动/停止日报、打开主窗口或退出。可管理持仓、发送时间、邮箱和模型，查看来源状态、历史报告和日志；打开不会自动发信，点击“启动自动日报”才运行。关闭窗口仅收起，菜单栏“退出”才停止本软件任务并完全关闭。详见[DESKTOP_GUIDE.md](DESKTOP_GUIDE.md)。这是复用本机项目及`.venv`的应用入口，暂非跨电脑独立安装包。

新增三源自动轮换：东方财富、腾讯、BaoStock。新下载成功后下次从后一家开始，失败/数据不合格立即换下一家，冷却状态跨重启保留；有效缓存优先复用。详见[SOURCE_POOL.md](SOURCE_POOL.md)。`./run.sh watch-status`可查看来源状态及下次优先顺序。

两轮迭代评估见[ITERATION_REVIEW.md](ITERATION_REVIEW.md)。现在可用`./run.sh menu`或双击`启动助手.command`进入统一菜单。`./run.sh doctor`离线自检，`./run.sh watch-status`查看状态，`./run.sh watch-stop`请求停止；不会另起后台实例。当前配置为交易日14:00，可从菜单栏启动或停止日报；普通打开应用不会自动启动日报。

个人 A 股量化研究助手，当前版本 0.1.0。已经实现离线运行、账户记账、规则策略、DeepSeek 综合接口、仓位风控、本地面板、快照回放和邮件接口。数据接入、模型收益和长期运行仍需分阶段验证；当前输出统一标为演示或研究，不能视为已通过实盘准入。

新增自动跟踪邮件入口：`./run.sh watch-setup` 本机配置，`./run.sh watch --once` 下载、分析并试发一次，`./run.sh watch` 持续运行。默认跟踪159567，每交易日14:20发送一次买卖持有方向建议。当前仍以此前最近已完成日线为依据，邮件注明数据截止日，不冒充实时盘中判断。详细说明见[AUTO_EMAIL_SETUP.md](AUTO_EMAIL_SETUP.md)。真实盘中决策仍需可靠分钟数据；密钥缺失、接口限流或数据未更新时不会宣称日报已送达。

## 直接试用

在项目目录运行，无需安装第三方依赖：

```sh
./run.sh demo
./run.sh dashboard --demo
```

第二条命令启动后，浏览器访问 http://127.0.0.1:8765 。可以生成报告、编辑演示账户、录入虚构成交、确认执行反馈。演示使用独立数据库，不修改真实账户；数据时间固定为2026年11月4日，不会伪装成今天行情。Ctrl+C停止面板。

`run.sh`优先使用这台机器已有的 Codex Python 运行时，其他机器自动尝试 `python3`，要求Python 3.11以上。也可自行指定：

```sh
AQUANT_PYTHON=/path/to/python3 ./run.sh demo
```

如当前终端的 `python3` 提示缺少 Xcode，请使用 `run.sh`。基础运行仅依赖Python标准库。面板仅绑定127.0.0.1，带CSRF和Host校验，不加载外部资源。它面向本机个人操作，不应代理到公网。

## 已实现的决策链

行情校验 → 量化证据 → 规则基线或DeepSeek综合 → 硬性风险退出 → 仓位和股数计算 → 最终风险限制 → HTML、文本和JSON报告 → 实际成交反馈。

目前策略是可解释的趋势、20日动量、行业相对强弱及ATR风险基线，评分不是收益预测概率。DeepSeek综合模式会读取上述独立信号和带来源的资讯，判断买、卖或不动；不是只润色报告。代码限制其仅引用证据包中股票，股数由本地算法计算。硬风控可覆盖模型意见。

账户版本、输入哈希、规则版本、综合模型请求和响应、建议及邮件状态保存在SQLite。建议不改变账户，成交录入才更新现金和持仓。金额和费用以分记账；买入当天不增加可卖数量。跨交易日要求重新核对券商快照，不自行猜测实际结算和冻结状态。

单股、行业、总仓位、现金储备、ATR风险预算及流动性约束作用于整个组合。未成交卖款和预计释放的风险额度不会用于提前买入。已有仓位超限会生成减仓要求；如果停牌、跌停或T+1无法执行，风险仍然计入。板块单位含主板/创业板100股、科创板200股起1股增量、北交所100股起1股增量及零股处理。当前仅研究连续竞价限价场景，不覆盖所有证券、特别状态和申报类型。

## 冻结模型响应与事件回放

已安装可选AKShare依赖时，可单独获取ETF历史行情并计算日频指标（不生成股数，不改变账户）：

```sh
.venv/bin/python scripts/inspect_etf.py --symbol 159567
```

输出保存在`runtime/etf`，仅使用今天之前的前复权日线。此工具独立于股票执行引擎；ETF的交易单位、价格精度、费用、回转交易资格和交易日安排尚未接入核心执行规则，不能将ETF伪装为MAIN股票生成委托。

```sh
./run.sh demo --mode replay --replay examples/synthesis.demo.json
./run.sh backtest examples/replay.demo.json
```

事件回放使用前一事件信号，在下一事件尝试成交；缺少下一事件成交量、涨跌停或价格超预算则不成交。回放包含费用、参与率限制和T+1，结果在 `runtime/backtest.json`。它用于核验机制，不是完整历史回测：尚不处理公司行动、盘口排队、历史退市股票池和财务时点还原，不用演示收益证明策略有效。

## 手动账户流程

先复制样例并填写真实账户数据，样例全部为虚构。cash表示扣除冻结后真正可用的现金，不能填总资产。permissions中填写已开通板块，holdings必须含板块和行业。

```sh
./run.sh account-import /absolute/path/account.json
./run.sh account-show
./run.sh fill /absolute/path/actual-fill.json --version 1
./run.sh confirm REPORT_ID --version 2
```

`--version`填写当前账户版本，防止旧页面覆盖新账户。实际成交id必须唯一；相同id相同内容重复提交不会重复入账，内容不同则拒绝。实际成交允许部分成交数量，不拿申报单位拒绝券商真实的部分成交。每次报告的实际成交录入完整后，再confirm；完全没有执行也需要确认。上午未确认的执行会阻止下午继续生成精确数量。

导入新快照不自动关闭待确认报告；确认时需逐份核对，避免遗漏券商挂单。补录时间早于快照的成交会被拒，需先对账而非倒序追加。程序不提供自动交易日结算；新交易日导入券商最新快照确认可卖数量。

## 真实行情和资讯输入

现有采集接口读取JSON文件，或从你授权的HTTPS行情适配服务获取同格式JSON：

```sh
./run.sh run --market /absolute/path/snapshot.json --slot am
./run.sh run --market-url https://YOUR_PROVIDER/snapshot --slot am
./run.sh dashboard --market /absolute/path/snapshot.json
```

`examples/market.demo.json`展示字段格式。真实数据必须设 `demo:false`，注明source、覆盖范围、kind=intraday、as_of、received_at和交易日历。每只股票提供交易所代码、板块、行业、价格、日频特征及其可获得时间、实际涨跌停价格和证券状态。`rules_verified:true`只应由完成规则校验的适配器提供，不能为了跳过检查手动伪造。

资讯news每项使用id、title、summary、url、published_at、received_at；链接必须为HTTPS，且两种时间均不晚于决策时刻。程序本身不自动抓取任意新闻网页。`daily_features`可以从至少61条已完成日线计算均线、动量、ATR及平均成交额。成交额单位为人民币元；价格和ATR必须在同一价格基准。供应方需处理复权、历史状态、分钟数据来源及覆盖质量。

默认行情最长120秒、账户快照最长8小时且必须为当日；日频特征必须来自此前交易日。历史特征最长间隔30天，供应方仍需额外校验交易日完整性。行情时间是供应方真实时间，不能用本次下载时间替换过期报价。

**尚未接入并实测AKShare、BaoStock或授权分钟行情供应商。** 这些将作为后续适配器开发；当前的HTTPS接口是输入契约，不意味着已有全市场数据服务。真实盘中数据缺失时不会输出看似实时的交易建议。

交易规则配置带有效起点及审核截止日。默认研究规则仅审核到2026年11月4日，真实运行前需根据最新规则复核并更新 `config/settings.json` 中的 `reviewed_through`；不能仅为绕过暂停随意延长。费率全部是研究样例，必须替换为实际券商费用。

## 接入DeepSeek

密钥只放环境变量。不要在聊天、JSON快照、代码或日志中填写密钥：

```sh
export DEEPSEEK_API_KEY='YOUR_KEY'
export DEEPSEEK_MODEL='YOUR_AVAILABLE_MODEL'
./run.sh run --market /absolute/path/snapshot.json --slot am --mode deepseek --allow-api
```

真实调用使用DeepSeek官方HTTPS接口、JSON输出和有限重试。单次最多40只，需在量化候选层缩小范围并保留全部持仓。默认模型名来自当前官方JSON示例，可通过环境变量改为账号实际可用模型。

提交给DeepSeek的字段：量化分项、股票代码、持仓比例、组合限制、行情覆盖说明、带来源的资讯。不会提交绝对现金、持仓股数、成本、姓名或账号。每次显式使用`--allow-api`表示允许提交此证据包并产生API费用。请求不自动联网搜索，资讯由数据适配器提供。API空响应、格式错误、引用不存在或限流失败时明确PAUSED，不静默改为“已完成综合”。

真实API尚未进行账号联调。离线桩和冻结响应不产生API费用；回放模式不代表真实调用成功。历史运行使用 `--at` 时禁止真实API及邮件，以免把今天LLM知识伪装成当时已知信息。

## 调度与邮件

程序提供单次tick入口，由系统定时器每分钟调用；仅在北京时间09:40、14:20且日历确认开市时运行。当前没有安装系统定时器、后台常驻任务或Codex自动化。

```sh
./run.sh tick --market-url https://YOUR_PROVIDER/snapshot --mode deepseek --allow-api
```

配置 `.env.example` 列出的SMTP环境变量后，在命令增加`--send`显式启用发送。程序不自动加载.env。SMTP使用SSL，默认465端口；目前不实现587 STARTTLS。未配置密钥时不发送，演示邮件禁止真实投递，过期或旧账户版本报告禁止发送。

同日、同批次slot、同mode只保存一份报告。重复运行返回原报告；需要重新评估时使用新slot，且不能绕过旧报告反馈确认。发送过程区分pending、sending、accepted和unknown；accepted仅表示SMTP接受，不保证进入收件箱。发送失败或结果未知时不自动重发，防止重复交易提醒，需人工核对邮箱及日志。

SQLite和报告包含私人持仓及模型记录，保存在已被.gitignore排除的runtime目录，未加密；只放在受信任的本地磁盘，不上传到公开仓库。

## 验证和后续任务

```sh
./run.sh --help
/Users/shuoye_tan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest discover -s tests -v
```

可用Python环境中也可直接运行 `python3 -m unittest discover -s tests -v`。测试覆盖账户原子记账、版本冲突、幂等、T+1、组合上限、真实卖款使用、暂停状态、模型输出证据校验和下一事件成交约束。

下一阶段仍需真实数据适配和质量验收、完整历史回测、LightGBM样本外研究、DeepSeek增益对照、可靠通知监控与前瞻模拟。开发计划详见 `TASK_PLAN.md`，本版本不能将未完成项目标成已验收。

接口依据：[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)。交易规则参考：[上交所2026年规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml)、[深交所2026年规则](https://www.szse.cn/lawrules/rule/trade/current/t20260424_620190.html)、[北交所申报数量说明](https://www.bse.cn/uploads/6/file/public/202509/20250916143055_4sxt0kkec4.pdf)。

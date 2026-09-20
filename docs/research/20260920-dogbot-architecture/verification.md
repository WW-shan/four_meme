# 调研与 P0 修复校验记录（2026-09-20）

范围：核对三份调研文档中的关键数字与来源、验证设计/计划引用的代码前提、记录 P0 修复与测试证据。
方法：重新抓取 DefiLlama API 与 MadeOnSol 页面做逐条比对；直接读仓库代码与 ABI；运行测试套件。

## 一、数字与来源复核（全部通过）

| 项目 | 文档值 | 复核结果 | 结论 |
|---|---|---|---|
| Axiom 24h/30d 协议费 | $2,292,458 / $49,650,908 | API 返回值完全一致 | 通过 |
| GMGN 24h/30d | $987,148 / $52,386,803 | 完全一致 | 通过 |
| Terminal（原 Padre） | $171,187 / $5,335,336 | 完全一致；methodology 确认 “Pump Trading Terminal (previously known as Padre)” | 通过 |
| Maestro | $40,571 / $1,993,334 | 完全一致 | 通过 |
| Trojan | $39,673 / $1,125,007 | 完全一致 | 通过 |
| Photon | $8,480 / $532,922 | 完全一致 | 通过 |
| BONKbot | $0 / $113,183 | 完全一致 | 通过 |
| MadeOnSol 首触回测 | 491,119 buys / 85,760 mints / 426 KOL / 72,549 events / 34.3% / 20.2% / 13.1% / 7.0% / 46.9% / 37.5% / 25.9% / 21.7% / 14.1% / 7.4% / 51.1% / 13.7% / 0.78 / 0.05 | 原文全部命中 | 通过 |
| MadeOnSol 8 月报告 | 533 KOL / 1,133,569 trades / 90,075 tokens / 2.11M SOL / $4,654 / 9 seconds / 1.8% / 1,032,440 / 18,202 / 223,339 / 1,170 / 40% | 原文全部命中 | 通过 |
| meme-radar 阈值 | $8,000 / dev 1% / tax 5% / spread 2pp / mcap $10k–500k / preferred $20k–80k / 6 audits / 60s cache | 原文命中（中文数字写法与检查脚本的 “10 万” 格式不同，属脚本误报） | 通过 |
| Hicarus 分发者模式 | 1,935 tokens/+$1,358/66.9%；658/+$1,485/87.5%；0.5–3.5 SOL 首买 | 原文命中 | 通过 |
| crypdev007 0 区块声明 | shreds 比 gRPC 快 100–150ms；首位买家胜率 70–80% | 本地 clone README 命中（raw main 分支 404，属抓取路径问题） | 通过（作者自报） |

## 二、代码前提验证（发现并修复的问题）

| 前提/问题 | 代码位置 | 验证结果 |
|---|---|---|
| Pancake 卖出 `amountOutMin = 0` | `src/core/trader.py` | 确认存在，已改为按 `getAmountsOut` + 滑点计算，quote 失败时才用紧急 `minOut=1` 并告警 |
| `buyMemeToken` 的 `minAmount = 1` | `src/core/trader.py` | 确认存在，已改为按已验证价格与 `BUY_SLIPPAGE_PERCENT` 计算；仅在无价格时用可配置 floor 并告警 |
| `c18aa711…` 被标成 `TokenSale` | `src/core/listener.py` | 确认存在（实为 `LiquidityAdded`），已用 ABI 派生注册表替换 |
| 一个 65 字符的伪造主题 `a78d55ae…` | `src/core/listener.py` | 确认存在且永不匹配，已随硬编码表移除 |
| v1 `TokenSale` 布局与 v2 不同 | `src/core/listener.py` 手写解码 | 确认 v1 会被解成 etherAmount/fee；已改为 ABI 严格解码，v1/v2 均正确 |
| `bot.py` 卖出主题集包含 `LiquidityAdded` | `src/trader/bot.py` | 确认存在，已改为从 ABI 派生 `TRADE_TOPICS` 的 sale 子集 |
| `tests/core/` 未被 `unittest discover` 发现 | `tests/core/__init__.py` 缺失 | 确认存在；已补 `__init__.py`，discover 覆盖从 1219 提升到 1471 项 |

## 三、P0 修复清单

- 主题注册表：`src/data/fourmeme_log_decoder.py` 新增 `EVENT_NAME_BY_TOPIC`、`canonical_trade_name`、`is_known_topic`、`LIQUIDITY_ADDED_TOPIC`。
- listener：已知主题走 ABI 严格解码；`TokenPurchase2/TokenSale2`（仅 origin）跳过；非交易事件（`LiquidityAdded`、`TokenCreate`、`TradeStop`）不再进入交易解码；所有事件带 `received_at`。
- bot：`FOURMEME_SALE_TOPICS` 由 ABI 派生；注册 `LiquidityAdded` 为毕业事件，并从 `base` 取 token。
- collector：`_event_provenance` 保留 `received_at`；`on_trade_stop` 兼容 `LiquidityAdded` 的 `base`/`quote`，记录 quote 资产与符号。
- 报价：新增 `src/data/fourmeme_quote.py`；trader 对非 BNB 报价给出明确资产名并拒绝；dataset builder 跳过已知非 BNB quote 的生命周期。
- 测试：新增/更新 `tests/model/test_fourmeme_topic_registry.py`、`tests/core/test_fourmeme_trade_guards.py`、`tests/core/test_collector_quote_provenance.py`、listener 的 v1/v2/LiquidityAdded/未知主题用例。

## 四、测试证据

- `PYTHON_DOTENV_DISABLED=1 python3 -m unittest discover`：**1471 tests, 1 skipped, 0 failures**。
- 其中 `tests/scanner/`：56 项通过（雷达、快照抓取、过滤器、钱包流、决策、影子、验收闸门、API、CLI）。
- 不设置 `PYTHON_DOTENV_DISABLED=1` 时，有 2 项既有失败，均来自本地 `.env` 的实验开关覆盖（`test_trading_config_exposes_action_policy_router_defaults`、`test_open_position_stores_continue_hold_route_metadata`），与本次改动无关。

## 五、仍未验证

- 真实 GoPlus / honeypot.is / DexScreener / GMGN 网络调用（缺个人 API key 或未在无人值守环境验证）。
- 2–4 周影子运行数据与实盘偏差（P7 闸门代码已实现，数据待积累）。
- Solana Geyser/Jito 实盘链路（仅完成归一化适配层）。
- BSC 私有交易通道（bloXroute/NodeReal）的真实收益与费用。

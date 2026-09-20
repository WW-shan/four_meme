# 打狗扫链机器人实现研究（2026-09-20）

范围：研究别人怎么实现“新币监听 → 安全过滤 → 抢买 → 退出”，并对照本项目给出可采纳/不可采纳的结论。
本轮只做研究，不改运行时代码、不改 `.env`、不启用交易、不提交推送。

## 一、结论先行

1. 认真做的机器人架构高度一致：**监听 → 解码 → 过滤 → 执行 → 退出 → 风控与记账**。差别不在“有没有 AI”，而在每一层是否真的实现了。
2. 五个环节里，**过滤与退出比“快”更决定生死**。速度只决定你能不能拿到票，过滤决定票是不是废纸，退出决定赢的那张能不能兑现。
3. 真正能用的开源实现很少：8 个仓库里，2 个达到“可直接学习的生产级参考”，其余存在空壳模块、假功能或危险写法。
4. **第三方热度/聚合数据不能当扳机**：Chainstack 的实测说明 PumpPortal 这类聚合流在 2026-09-15 的 Solana v1 交易格式下会漏币；监听链本身才是完整时钟。这直接支持我们“GMGN/X 只做候选发现、链上事件做触发”的分层。
5. 没有找到可信的“扫链机器人整体盈利率”公开数据。所有仓库里的收益声明都不可验证；唯一可靠的路径是自建影子执行 + 按过滤器归因的 P&L 统计。
6. 对我们最值得先补的四件事：**前置过滤器门禁、真实成本影子执行、退出阶梯与熔断、私有交易通道**。把 GMGN 榜单接成自动买入，是最不该先做的事。

> 个人打狗玩家/独立开发者（shreds 0 区块、KOL 首触回测、dev 钱包分发者、meme-radar 只读雷达、X/TG 喊单流）的做法与阈值，见同目录 `individual-operators.md`。

> 实盘在用的终端/机器人（Axiom、GMGN、Cielo、Maestro、Trojan、Photon、Terminal/Padre 等）的真实协议费量与功能拆解，见同目录 `live-products.md`（2026-09-20 补充）。

## 二、方法与证据限制

- `smart-search` 的主搜索上游（xAI Responses、Exa、Zhipu、AnySearch）当天全部故障：xAI 返回 HTTP 503，Exa/Zhipu 未配置 key，AnySearch 503。按 skill 规则没有静默切换搜索系统。
- 替代路径：GitHub 官方 API 找仓库 + `git clone` 读代码 + `smart-search fetch`（Tavily 抓取通道可用）抓官方文档。
- 因此本报告对“仓库实现”的结论依据是**直接读到的代码**；对“行业整体盈利率”没有结论，只列未验证项。

### 读过的仓库（研究时 commit、星标为抓取当时）

| 仓库 | 语言/星 | commit | 代码质量判断 |
|---|---|---|---|
| chainstacklabs/pumpfun-bonkfun-bot | Python / 999 | `13b9ec2` | **生产级参考**。四种监听器、零 RPC 抢买、退出策略、清理、限流都真实实现；README 明确 “NOT FOR PRODUCTION” |
| 0xfnzero/sol-trade-sdk | Rust / 341 | `0ba9ec5` | **生产级参考**。低延迟热路径规范、风险门、十余条提交通道、gRPC 解析示例 |
| vineetdev02/Memcoin-Sniper-Bot | TS / 1 | `a4ccdf0` | **设计文档与过滤器参考**。12 个过滤器多数已实现，带纸面执行、过滤器效果分析、回撤熔断 |
| BaileyOnBlockchain/gary-solana-sniper | Python / 1 | `3a33990` | 部分可用：Jito 动态小费、DRY_RUN 默认；但 Twitter 过滤是 stub、dev 钱包检查是 TODO、高级检测是轮询占位 |
| hexnome/grpc-copy-trading-sniper-bot | TS / 24 | `13bc318` | 混合：有 gRPC pump.fun 抢单骨架，但宣传的 rug-detection 模块是空 CLI 壳 |
| HarrierOnChain/Fourmeme-bot | TS / 31 | `67f9305` | 营销壳：所谓 sniper 只是读固定 target 列表买币，没有任何新币监听或私有交易 |
| bs7745-y/fourmef | TS / 54 | `996c776` | 危险样本：批量子钱包 volume bot，`minOut=0`，把 EVM tx hash 送进 bs58 校验，README 与代码不一致 |
| bigmacman1129/solana-rust-sniper | Rust / 133 | `7e6779c` | 模板项目：Geyser + Jito/Nozomi/ZeroSlot/NextBlock 模块齐全，`.env.example` 有 DRY_RUN、日亏损上限等安全项，需自行审计 |

星数不可信：GitHub 同主题中存在 4000+ 星但内容营销化的仓库。

## 三、统一架构与热路径

```
链上事件/流 ──▶ 解码(program + IDL/discriminator) ──▶ 去重/新鲜度
      │                                                   │
      └──────────────▶ 过滤器门禁(缓存快照) ◀─────────────┘
                              │
                     通过 ──▶ 构造交易(预热 ATA/ALT/blockhash/durable nonce)
                              │
                              ▼
                    并发提交(Jito / SWQoS / 私有 RPC) ──▶ 对账确认
                              │
                              ▼
                     持仓管理(TP 阶梯/止损/时间退出/rug watcher/熔断)
```

sol-trade-sdk 的 `docs/LOW_LATENCY_BOTS.md` 给出热路径定义，原文要点：

- 热路径只允许：`filter -> deduplicate -> reject stale event -> map post-trade state -> Simple*Params -> sign -> submit`
- 热路径里**禁止**初始化客户端、同步取 blockhash、查余额、找池子
- 远程风险数据必须后台取好、原子发布本地快照；缺失/过期策略要显式定义，**不允许在门禁里调 RPC 或审计 API**
- 提交/确认慢通常是 RPC 轮询问题：用 `wait_tx_confirmed=false` + 外部确认，或确认到 `processed/confirmed`
- 明确禁止把 `min_out = 0` 当日常错误处理

## 四、监听层：怎么最早发现

### 4.1 Solana（pump.fun 为例）

Chainstack 实现提供四种监听器，优缺点原文可查：

| 监听方式 | 说明 | 代价/坑 |
|---|---|---|
| `logsSubscribe` | 订阅 pump.fun program 日志，本地推导 bonding curve PDA | 每个 provider 都支持；是默认路径；需把 WS 消息上限调到 32MB 否则连接被 1009 关闭 |
| `blockSubscribe` | 订阅整块并按 create 指令过滤 | 不是所有 provider 支持；繁忙 program 上曾不稳定 |
| Geyser gRPC | 最低延迟 | 需要 Geyser 端点与凭据 |
| PumpPortal | 第三方聚合流 | **会漏币**：2026-09-15 起 v1 交易格式的创建事件不进该流；只能看到它索引的样本 |

其他关键点：

- `getProgramAccounts` 对 pump.fun 已不可用（链上账户超 1000 万，provider 直接拒绝或超时），必须用过滤订阅。
- 零 RPC 抢买（extreme fast mode）：对 geyser/logs/blocks 拿到的 CreateEvent 直接构造 `buy_v2`，事件里带 creator、quote mint、mayhem/cashback 标志，**检测到提交之间不调用任何 RPC**；事件字段不完整时宁可跳过，因为用猜测账户构造的交易会在链上 revert 且照样付手续费。
- buy_v2/sell_v2 的账户表在 IDL 里完整；**legacy buy/sell 的 IDL 账户表不完整**，必须对照最近成功链上交易，不能盲信 IDL。

延迟阶梯（Helius 官方文档）：

| 层级 | 形态 | 说明 |
|---|---|---|
| Preconfirmations | 交易进 validator 后、出 shred 前 | 文档给出延迟流程图，属于最早阶段 |
| Shred Delivery（Raw shreds / Preprocessed） | 预执行交易 | 预处理解码版约比 `processed` 提前 **8ms**；Raw Shreds 需自行 deshred；定价 **$1,000/月/IP**（Pro $800） |
| LaserStream gRPC（`processed`） | 交易+账户/程序更新 | 自动重连、最多 24 小时历史回放、多节点故障切换，wire-compatible 于 Yellowstone gRPC |
| LaserStream WebSocket | 标准订阅 + 扩展 | 无历史回放，需自行重连 |
| logs/blockSubscribe | 通用 | 延迟更高，但便宜、兼容性最好 |

### 4.2 BSC / Four.meme

- BSC 出块时间演进（官方公告索引）：3s → 1.5s（Lorentz）→ **0.75s（Maxwell）** → **0.45s（Fermi，2026-01）**。加上 EVN（亚秒块协调），网络层 RTT 下限约 AP↔US 80ms、US↔EU 35ms。
- BSC 已统一到 **PBS/Builder API（BEP322）**：searcher 把交易/捆绑交给 builder，builder 竞价出块。想稳定抢位必须走 builder/私有通道，公共 RPC 广播在 0.45s 出块下基本没有竞争力。
- 检测仍然靠 **launchpad 合约日志 + ABI**。本仓库已有 `config/TokenManager.lite.abi`、`config/TokenManager2.lite.abi`，listener 使用 `TokenCreate/TokenPurchase/TokenSale/LiquidityAdded` 等事件。第三方 ABI 仓库 `zrsm/fourmeme-abi` 的事件名是 `TokenCreated/ContributionReceived/TokensPurchased`，与当前 TokenManager 不一致——**ABI 漂移风险真实存在，必须以链上主题哈希与本地 decoder 为准**。
- 私有通道：
  - bloXroute BSC：`Private Transactions` 参数 `mev_builders` 可选 `bloxroute / all / 48club / blockrazor / jetbldr / nodereal`；`Bundle Submission` 走 Cloud API；还有 `Speed Boost`。
  - NodeReal：文档提供 `Send Bundle`、`Send PrivateTransaction`。
  - 这些通道解决的是“被夹/抢不到位”，不能解决“买错币”。

## 五、执行层：怎么把单打进去

### Solana

- Jito：bundle 最多 5 笔、原子执行、按 tip 竞价，拍卖约 **50ms 一次 tick**；支持 gRPC 与 JSON-RPC；全球多 region block engine。
- Helius Sender：不消耗 API credit，按笔付 tip。Sender Max 最低 **0.001 SOL**，同时走 Helius/Jito/Harmonic/Rakurai 等多路径并进入优先 tip 缓冲；SWQOS-only 最低 **0.000005 SOL**，单路径。
- bloXroute Solana `submit-snipe`：专为抢币设计——接收一对互相竞争的交易，**第一笔发 Jito、第二笔发 bloXroute staked connections**，两笔都需 ≥0.001 SOL bloXroute tip；如果只想成一笔，需自行用同一个 durable nonce 让两笔互斥。这直接说明头部 bot 的思路是“多路并发、先成先算”。
- sol-trade-sdk 支持 Jito/Nextblock/ZeroSlot/Temporal/bloXroute/FlashBlock/BlockRazor/Node1/Astralane/Glaive 等并发提交，`first accepted returns early`。
- 工程细节：预热 blockhash / durable nonce / ATA / ALT；提交后按签名对账；超时不能盲目重试，先确认旧交易是否已上链。

### BSC

- 走私有交易/捆绑到 builder，避免公开 mempool 被夹；手续费结构是 gas + builder 竞价。
- 与 Solana 不同，BSC 没有 shred 级公开数据；可用的加速是 bloXroute BDN、私有 RPC、Speed Boost。
- 对本项目：现有 `buyMemeToken`（内盘）与 Pancake V2 卖出路径直接使用公共 RPC + gas 倍率，且 Pancake 卖出 `amountOutMin=0`，属于最容易被夹的写法；需要在能交易前改造。

## 六、过滤层：真正的 alpha

参考实现 `Memcoin-Sniper-Bot` 的过滤器清单（多数已实现）：

| 过滤器 | 检查内容 | Safe 预设阈值（作者设定） |
|---|---|---|
| honeypot-sim | 用 Jupiter quote 模拟卖出，不能路由或卖出税过高即拒；新池 90s 内“无路由”视为未索引而 skip，避免误杀 | 卖出税 ≤5% |
| lp-locked | LP 是否打进 burn 地址或已知 locker；仍在 dev 钱包 → 拒 | 必须 |
| mint-authority | mintAuthority 是否为 None | 必须放弃 |
| freeze-authority | freezeAuthority 是否为 None | 必须放弃 |
| dev-wallet | 查 RugCheck 的 dev rugRate，本地缓存 30 分钟 | rugRate ≤0.2 |
| top-holders | Top1 / Top10 / 单个非 LP 钱包占比 | Top1 ≤12%，Top10 ≤30% |
| liquidity-min | 初始流动性上下限，太晚也不进 | $8,000 起 |
| bundled-launch | 前 4 秒前 20 笔里，单一非创建者付款人占比 >50% → 拒 | 开启 |
| insider-detection | 资金链上游是否内幕/团队钱包 | 开启 |
| anti-sniper-war | 前 6 秒交易数 >10 → 已被狙击手抢跑 | 拒绝或减半仓 |
| volume-velocity | 1–10 分钟后买/卖比、独立买家数、量能趋势 | 加权 |
| social-signal | X/Telegram/网站/boost 存在性 | 加权（可关） |

三档预设（作者设定）：`Safe Sniper`（minFilterScore 80）、`Aggressive`（55）、`Learning`（只留 honeypot 门禁，跑一周收集数据，再用 per-filter 胜率分析决定哪些过滤器真的有效）。**Learning 模式是最值得我们抄的思想**：先用最小门禁收集全量数据，再用数据决定过滤器，而不是凭感觉设阈值。

BSC 侧可用同类数据源：GoPlus Token Security API（honeypot、税率、权限，官方文档称 free/permissionless）与 honeypot.is（模拟买卖）。这些必须后台缓存快照，不能进热路径。

## 七、退出与风控

参考实现的默认退出阶梯（作者设定，非验证收益）：

```
+50%  卖 25%
+100% 卖 25%
+300% 卖 25%
+900% 卖 15%
剩 10% moonbag
止损 -40% 全平
+200% 后启用 30% 回撤追踪
30 分钟没到 +50% 时间退出
流动性单笔跌 >30% 立即市价卖（rug watcher）
```

风控（`Memcoin-Sniper-Bot` 已实现并带测试）：

- 日亏 >10% 停新仓 24h；周亏 >25% 停 72h；连败 20 笔停 24h；**熔断只拦新开仓，止损/止盈永远放行**
- 单笔 0.25%–5%（默认 1%），最多 10 个并发，60 秒内最多开 3 仓，同一池不加仓
- 储备模式：最多部署 50% 资金；翻倍后提走一半；资金腰斩则停机复查
- rug watcher：每 5 秒轮询 LP，跌破基线一定比例立即全平

对照本项目：我们已有 stop-loss、trailing、runner reserve、gas/滑点参数；缺的是 TP 阶梯部分减仓、实时 rug watcher、日/周熔断、按信号质量的仓位缩放。

## 八、纸面交易与测量（最重要的一步）

`Memcoin-Sniper-Bot` 的计划明确要求：**先纸面 2–5 周，再上小资金**。纸面引擎必须模拟：滑点、检测到成交之间的池子变化（1–2 秒）、失败交易、MEV/夹子损失、tip 与优先费；每笔记录入场时的全部过滤器结果、每个 TP/SL 触发、峰值与回撤；每天统计胜率、平均盈亏、每笔期望值、按过滤器分组的胜率，达标才实盘。

我们已有资本回放和成本参数（手续费、滑点、固定成本），但缺“实时影子执行 + 真实报价 + 过滤器归因”。

## 九、仓库真伪与安全（必须知道）

- `chainstacklabs/pumpfun-bonkfun-bot` README 顶部有 **SCAM ALERT**：该仓库 Issues 长期被钓鱼机器人刷屏，专门骗私钥；官方代码明确 “NOT FOR PRODUCTION”。
- `gary-solana-sniper` 的 `filters.py` 里 Twitter 检查直接 `return True`，dev 钱包检查是 TODO；`advanced_detection.py` 的 Raydium 监听是 `sleep(2)` 占位。
- `grpc-copy-trading-sniper-bot` 的 `rug-detection.ts` 只有 CLI 参数解析，没有任何检测逻辑。
- `Fourmeme-bot`（TS）的 “Sniper” 只是读 `config.sniper.json` 里写死的 token，逐个 `getAmountsOut` + 买；没有监听、没有私有通道。
- `fourmef` 是 volume bot：主钱包给批量子钱包打钱再买卖，`swapExactETHForTokensSupportingFeeOnTransferTokens(0, ...)` 用 `minOut=0`；把 EVM tx hash 丢进 `bs58()` 校验；README 让 TS 项目 `cargo build`。**不要运行这类仓库。**
- 通用安全规则：专用热钱包 + 小额；私钥只存在本机受保护文件；不要贴进聊天/截图/日志；不要 `minOut=0`；买入前先模拟卖出；审查 `approve`；对“免费开源机器人”默认怀疑。

## 十、对本项目的结论与建议顺序

| 层 | 我们现状 | 业界做法 | 建议 |
|---|---|---|---|
| 候选发现 | 新做的注意力榜（GMGN + X），只读 | 榜/社交只做候选，不做扳机 | 保留；榜单输出候选与叙事，**触发时钟改为链上事件** |
| 触发 | Four.meme `logsSubscribe` + HTTP 补块 + 本地 decoder（已有）；无 Solana | Geyser/logs + IDL 解码；零 RPC 构造 | BSC 先修 ABI/主题漂移与延迟测量；Solana 若要碰，单独适配器（pump.fun IDL + Geyser/logs） |
| 过滤 | 模型打分 + 风险参数；无显式 rug/honeypot 门禁 | 12 过滤器 + 预设 + 缓存快照 | 先实现只读 `TradeRiskGate`：GoPlus/honeypot.is、持仓集中度、dev 历史、早期捆绑/狙击计数；热路径 0 RPC |
| 执行 | 公共 RPC + gas 倍率；Pancake 卖出 `minOut=0` | 私有/多路提交；绝不 minOut=0 | 先改成安全 `minOut` 与对账；再评估 bloXroute/NodeReal；Solana 用 Jito/Sender |
| 退出 | 止损/追踪/保留仓 | TP 阶梯 + rug watcher + 熔断 | 加部分止盈、LP 监控、日/周熔断（只拦新仓） |
| 测量 | 离线资本回放 | 纸面 2–5 周 + 过滤器归因 | 加影子执行与 per-filter P&L，达标才谈实盘 |
| 安全 | 有 `ENABLE_TRADING=false` 默认 | 专用热钱包、小额、密钥隔离 | 保持默认关闭；实盘前单独做密钥与限额清单 |

**明确不做**：不追毫秒级 colocation、不订阅 $1,000/月 shreds、不上来就接私有小费通道、不把 GMGN 榜单直接接自动买入、不运行来源不明的“免费抢币机器人”。

## 十一、尚未查清 / 未验证

- 没有可信的机器人整体盈利率、胜率、成交率公开数据；仓库中的收益数字均为作者声明，未独立验证。
- Helius/Chainstack/bloXroute/NodeReal 的真实延迟与费用需要账号实测；本报告只引用官方文档定价与机制。
- Four.meme 当前合约的完整事件集与主题哈希需以链上为准；第三方 ABI 与本地 ABI 不一致已记录。
- 真实滑点、失败率、被夹概率需要影子执行数据才能确定。

## 十二、证据来源

代码（研究时 commit）：

- https://github.com/chainstacklabs/pumpfun-bonkfun-bot `13b9ec2`
- https://github.com/0xfnzero/sol-trade-sdk `0ba9ec5`
- https://github.com/vineetdev02/Memcoin-Sniper-Bot `a4ccdf0`
- https://github.com/BaileyOnBlockchain/gary-solana-sniper `3a33990`
- https://github.com/hexnome/grpc-copy-trading-sniper-bot `13bc318`
- https://github.com/HarrierOnChain/Fourmeme-bot `67f9305`
- https://github.com/bs7745-y/fourmef `996c776`
- https://github.com/bigmacman1129/solana-rust-sniper-bot `7e6779c`

官方文档：

- https://docs.chainstack.com/docs/solana-creating-a-pumpfun-bot
- https://www.helius.dev/docs/laserstream.md
- https://www.helius.dev/docs/sending-transactions/sender.md
- https://docs.jito.wtf/lowlatencytxnsend/
- https://docs.bnbchain.org/bnb-smart-chain/validator/mev/overview/
- https://docs.bnbchain.org/bnb-smart-chain/validator/evn/best-practice/
- https://docs.bloxroute.com/bsc/submit-transactions/bsc-private-transactions.md
- https://docs.bloxroute.com/bsc/submit-bundles/bsc-bundle-submission.md
- https://docs.bloxroute.com/solana/trader-api/api-endpoints/transaction-submisson/submit-snipe.md
- https://docs.nodereal.io/docs/send-bundle.md
- https://docs.nodereal.io/docs/send-privatetransaction.md
- https://docs.gopluslabs.io/reference/api-overview
- https://pumpportal.fun/data-api/real-time

原始抓取缓存（本轮临时目录，不随仓库提交）：`/tmp/meme-dogbot-research/`。

## 十三、Scoreboard 收口

`docs/model_scoreboard.md` 已追加 2026-09-20 条目：本轮为架构研究，改变了下一步实现方向（过滤门禁、影子执行、退出与风控优先于速度），但没有模型结论、收益承诺或实盘风险解释变化；未改 `.env`、阈值、仓位、bot 进程与运行开关。

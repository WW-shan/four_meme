# 实盘在用的扫链/交易机器人研究（2026-09-20 补充）

范围：研究当前真实在运营、有公开交易量与功能文档的扫链交易产品，回答“它们怎么发现币”和“到底看不看热度”。
本轮只做研究，不改代码、不改配置、不启用交易。

## 一、方法与可信度

- 搜索上游当天仍故障：xAI Responses HTTP 503，Exa/Zhipu 未配置 key，AnySearch 503。没有静默切换到别的搜索系统。
- 采用三条可核查路径：
  1. 产品官方文档（Axiom、GMGN、Cielo、Maestro、BONKbot、Ave、Nova、Padre docs 索引、Trench Radar 等）；
  2. DefiLlama 官方 API 的协议费数据（含 methodology 字段）；
  3. 2026 年的第三方对比/评测与 awesome 列表（作为产品认知，不作为收益证据）。
- 定义：“实盘在用”指有持续收费/交易量的公开产品与当前版本功能文档，不代表我能审计其私有代码或验证用户盈利。
- DefiLlama 费用是**协议费口径**（用户为交易付给机器人的费用），是活跃度代理，不是机器人用户的净利润。方法论原文示例：
  - GMGN：“All trading fees paid by users while using GMGN AI bot (**1% per trade**), collected in both the native gas token and USDC, tracked via Dune.”
  - Terminal：“Trading fees paid by users while using **Pump Trading Terminal (previously known as Padre)**… retained by Pump.fun after cashback/referral payouts.”（印证 Padre 已被 Pump.fun 收购/并入）
  - Axiom：“Every trading fee Axiom users pay, measured as the SOL that lands in Axiom's fee wallets on swaps routed through Axiom…”

## 二、真实用量（DefiLlama，2026-09-20 抓取）

| 产品 | 类型 | 链 | 24h 协议费 | 30d 协议费 | 备注 |
|---|---|---:|---:|---:|---|
| Axiom | Web 终端 | Solana / BSC / Robinhood Chain | ~$2,292,000 | ~$49,650,000 | 当前量级最大；社区榜单称其约占 Solana 终端 ~56% 份额（第三方说法） |
| GMGN | Web + Telegram | Solana/BSC/Base/ETH 等 10 链 | ~$987,000 | ~$52,390,000 | 1%/笔；多链；聪明钱/跟单/X Tracker |
| Terminal（原 Padre） | Telegram/Web | Solana/Ethereum/BSC/Base | ~$171,000 | ~$5,335,000 | 已被 Pump.fun 收购；Trenches 起家 |
| Maestro | Telegram | BSC/ETH/Arb/Solana/Base/Tron 等 9 链 | ~$40,600 | ~$1,993,000 | BSC 明确支持 **FourMeme / SpringBoard** |
| Trojan | Telegram + Web | Solana | ~$39,700 | ~$1,125,000 | 第三方报道称 $25B+ 累计交易量、2M+ 用户（未审计） |
| Photon | Web 终端 | Solana | ~$8,500 | ~$533,000 | 口碑老牌，但当前协议费低于 Axiom/GMGN（费用模型/统计口径可能不同） |
| BONKbot | Telegram/Telemetry | Solana | ~0 | ~$113,000 | 正在从 TG 迁移到 Telemetry 终端 |
| Pepeboost | Telegram | Solana | ~$879 | ~$55,967 | |
| SolTradingBot | Telegram | Solana | ~$604 | ~$40,108 | 官网自称 $5.2B 累计交易量、1.2M 用户、33M 笔（自报） |
| MEVX | Telegram/Web/插件 | Solana | ~$557 | ~$53,047 | 主打 MEV-aware sniping |
| Banana Gun | Telegram | EVM 多链 | ~$253 | ~$9,858 | EVM 时代的头部，当前 Solana 为主的市场里已边缘化 |

结论：**当前 Solana 生态的真实头部是 Axiom 和 GMGN**，Terminal（Padre）、Maestro、Trojan 是第二梯队，Photon 的“名气”与当前费用量不匹配。

## 三、它们到底怎么发现币

| 产品 | 触发/发现层（不看热度） | 市场热度层 | 社交热度层 | 资金热度/跟单 | 安全与执行 |
|---|---|---|---|---|---|
| **Axiom** | Pulse：New Creations / Final Stretch（快毕业）/ Migrated；Similar Tokens + OG Mode（最老 + 最高市值） | Explore Tokens：可选时间窗的 trending + 过滤器 | **Tweet Monitor**：实时扫关键加密推特账号，官方称有 ≥5 SOL 交易量即可免费用；**Twitter Preview Popup** 直接挂在 Pulse 上，官方解释“meme 基于 narratives，来自推文，必须快速反应” | 钱包跟踪 + Monitor Wallets + **Trader Scan**（任意 token 的逐钱包买卖、余额、已实现 PnL、持有时长） | Bundle Checker（同区块 ≥4 笔 + 过滤非持续 bundle 钱包）；Migration Actions（官方称“不再需要第三方狙击机器人”）；Turnkey MPC 钱包 |
| **GMGN** | NEW PAIR（新交易对/新币） | TRENDING 表：Age/Liq-MC/Holders/1h TXs/1h Vol/Price/1m-5m-1h%/Degen Audit；另有 **Hot Searches**（按站内访问/搜索热度） | **X Tracker**：监控 2000+ 精选账号的推文/回复/转发/引用/改简介，支持 Quick Buy；**SnipeX**：监控推特新合约并毫秒级自动买入（当前限 Solana + Telegram 钱包）；Trenches 热点内嵌推特信息 | **Follow/Monitor Square**：被跟钱包 1m–24h 资金流入榜 + 社交信息 + 一键买入；**Zero-Latency Wallet Tracking Bot**（私节点推送，SOL/BSC）；Copy Trade（1% 手续费 + gas）；聪明钱 10K+ 钱包（第三方说法） | Degen Audit（CA 安全检查）、anti-MEV、自动止盈止损/追踪止盈、Dev Sell 自动卖 |
| **Cielo** | Pulse：新池 → 即将迁移 → 已迁移 | **Trending 按 Mindshare 排名**，官方定义：*“how many wallets tracked by Cielo users are buying a token. Volume can be faked. Mindshare can't.”*（用被跟踪钱包的买入数衡量热度，而不是转发量） | 主要不做推文流；以钱包行为为核心 | Feed（被关注钱包的实时交易，可按金额/链/市值过滤）、Agents（自动跟单 + 链上模式研究 agent）、Wallet Discovery/PnL 排行榜；免费 300 个钱包（250 EVM + 50 Solana） | 30+ 链；Alerts/机器人推送 |
| **Maestro** | **Auto Snipe**：ETH/BSC 监听 mempool 在 Block-0 抢，Solana 同样支持；BSC 支持 FourMeme/SpringBoard | 依赖 DEX/launchpad 与 Trade Monitor，不做统一热榜 | **Signals**：接入 TG call channels 自动买入，**频道按 Maestro 用户跟踪数排序**；还提供 Scraper，可抓任意指定 TG 频道 | Copy Trade：每条链免费跟 5 个钱包（Premium 12 个）；官方强调“跟接收代币的钱包，不要跟发起交易的钱包”；ETH/BSC 可 mempool 抢跑/匹配 gas | Trade Monitor：实时仓位、限价买卖、多钱包同步；限价单、资金归集/分发 |
| **Trojan** | 快速 swap + web terminal（第三方评测） | 有热门/新币视图（评测描述） | 未查到官方社交监控文档 | Copy Trade 最多跟 40 个钱包；多钱包；Arena 激励 | 限价单、meme 币专用设置；第三方报道 $25B+ 累计量 |
| **Photon** | 官网主打 Snipe and sell；第三方称有 Memescope | 有 trending/memescope（第三方） | 未查到官方推文监控 | 有跟单/钱包跟踪（第三方） | 官网/第三方称 Smart MEV via Jito、fast/secure 模式、sub-0.3s 执行（厂商/第三方声称） |
| **Padre / Terminal** | **Trenches**（内盘/毕业前扫描）、pre-trade simulation | 有热门视图（产品页） | 未查到官方推文流 | Pump.fun 体系内钱包/跟单 | 被 Pump.fun 收购；Turnkey MPC |
| **Trench Radar** | 不交易，只做 bundle 扫描 | — | — | — | 槽位级 bundle 扫描：示例“24 个钱包在 0.4 秒内买入并持有 76% 供应”是极端危险信号；提示 2 钱包低占比可能是误报，要看 **Current Held %**，不能只看 Total Bundle% |
| **Vector** | 社交 feed 式 meme 交易终端（awesome 列表描述） | 有 | 以社交 feed 为核心 | — | — |

## 四、回答“是不是要看热度”

要，但必须拆成四层，而且真正下单前依赖的是第四层：

1. **触发层（事件，不看热度）**：新池/内盘/即将毕业/已迁移事件。Axiom Pulse 的 New Creations/Final Stretch/Migrated、GMGN NEW PAIR、Cielo Pulse、Padre Trenches、Maestro Auto Snipe 都是这个结构。它决定你能不能第一批看到。
2. **市场热度层（要看）**：时间窗内的成交、涨幅、holder、流动性、成交笔数。Axiom Explore Tokens、GMGN TRENDING、各终端的 trending 页都是这个口径。它决定“已经起量、值不值得继续看”。
3. **社交热度层（要看，但要防伪）**：
   - Axiom Tweet Monitor + Twitter Preview Popup：扫关键账号推文，挂在 Pulse 上，支持一键买。
   - GMGN X Tracker + SnipeX：推文/转发/引用/改简介监控，SnipeX 能按账号监听新 CA 并自动买（限 Solana）。
   - Maestro Signals：把 TG call channel 变成自动买入信号，并按 Maestro 用户跟踪数给频道排序；支持抓任意频道。
   - 结论：社交热度在头部产品里是**发现与叙事层**，通常配 Quick Buy 或自动买，但它建立在账号白名单/频道选择之上，不是全网情绪。
4. **资金热度层（最关键）**：被跟踪钱包/聪明钱的流入、跟单、Wallet Radar、GMGN Monitor Square、Cielo Mindshare、Axiom Trader Scan。Cielo 的说法最直白：“Volume can be faked. Mindshare can't.” 它用**被跟踪钱包的买入数**而不是推文数来定义热度。这是最接近“真实接力”的指标。

补充事实：

- 我在这些产品的官方文档里，**没有看到任何一家把“纯 X 热度自动买入”当作核心主策略**；最接近的是 GMGN SnipeX，但它的定位是“监控指定 Twitter 账号的新合约并自动买”，且当前限 Solana + Telegram 钱包。
- 免费/低门槛限制：Axiom Tweet Monitor 需要账户有 ≥5 SOL 交易量；GMGN X Tracker 非 Top 订阅账号只监控 5 个；Maestro 免费跟单每条链 5 个钱包。说明**“大 V 监控”本身是产品差异化功能，但要真正用起来有门槛或付费**。
- 成本：GMGN 官方文档写明跟单交易 = 买卖金额 + gas/priority + **1% 手续费**，并提醒用户很多跟单盈利被高 priority fee 吃掉，建议 0.002–0.006 SOL；第三方评测也指出主流 TG 机器人约 1%/笔。对高频策略，手续费就是生死线。

## 五、对我们的直接启示

1. **榜单要三栏并列，不要合成一个黑盒分**：市场热度（成交/涨幅/holder/流动性）、社交热度（X 提及/大 V/账号）、资金热度（聪明钱流入/跟单）。买入前必须有资金热度确认。
2. **照抄主流终端的生命周期结构**：新创建 → 即将毕业 → 已迁移，三段视图 + 每段自己的指标。这比“一个热榜”更符合实际交易节奏。
3. **X 监控要按账号白名单做**：Axiom 的 Tweet Monitor、GMGN 的 X Tracker、Maestro 的 call channels 都不是全网情绪，而是“精选账号/频道 + 一键买”。我们也应该先把白名单账号 + 精确 CA 关联 + 原帖链接做扎实。
4. **资金热度指标**：参考 Cielo 的 Mindshare（被跟踪钱包的买入数）和 GMGN Monitor Square（被跟钱包资金流入榜）。这比“转发总量”更难刷，也更接近我们上一轮说的“真实接力”。
5. **bundle/安全要独立成栏**：Trench Radar 的槽位级检测（0.4 秒内 24 钱包、持有 76%）和 Axiom 的 Bundle Checker（同区块 ≥4 笔 + 排除非持续 bundle 钱包）都说明：**先看有没有团队自留盘，再看热度**。
6. **先影子执行**：所有头部产品都提供限价/止盈止损/跟单，但没有一家承诺盈利；第三方评测明确写“大多数 meme 交易仍然亏钱”。我们的下一阶段仍应是过滤器 + 影子 P&L，而不是直接接自动买。

## 六、局限

- 没有 X/Reddit 的实时抓取权限（搜索上游故障），无法引用当天社媒原帖；产品口碑部分只能引用官方文档与 2026 年第三方评测。
- DefiLlama 是协议费口径，能证明产品有真实使用，不能证明用户盈利；Axiom 的“~56% 份额”与 Trojan 的 “$25B 累计量”均为第三方/媒体数据，未审计。
- Photon/BullX/Trojan/Padre 的官方文档站不可直接抓取，相关描述来自官网文案、awesome 列表与第三方评测，已标注。
- 具体延迟、成交率、跟单滑点仍需自建影子执行实测。

## 七、来源

- DefiLlama API：`https://api.llama.fi/summary/fees/{axiom,gmgn,trojan,terminal,maestro,photon,bonkbot}`（含 methodology，2026-09-20 抓取）
- Axiom 文档：`https://docs.axiom.trade/llms.txt`、`/readme.md`、`/faqs.md`、`/axiom/finding-tokens/explore-tokens.md`、`/axiom/finding-tokens/pulse.md`、`/axiom/finding-tokens/similar-tokens.md`、`/tweet-monitor.md`、`/twitter-preview-popup.md`、`/trader-scan.md`、`/wallet-tracking/monitor-wallets.md`
- GMGN 文档：`https://docs.gmgn.ai/index/trending.md`、`/new-pair.md`、`/follow-monitor-square-follow-watchlist.md`、`/zero-latency-wallet-tracking-bot.md`、`/copy-trade-copy-smart-money-automatically-earn-sol.md`、`/x-tracker.md`、`/snipex.md`
- Cielo 文档：`https://docs.cielo.finance/readme.md`
- Maestro 文档：`https://docs.maestrobots.com/sniper/index.md`、`/sniper/getting-started.md`、`/sniper/auto-snipe.md`、`/sniper/copytrade.md`、`/sniper/signals/index.md`、`/sniper/trade-monitor.md`
- BONKbot：`https://docs.bonkbot.io/overview.md`；Ave：`https://docs.ave.ai/quick-start.md`；Nova：`https://docs.nova.trade/introducing-nova-plus.md`
- Trench Radar：`https://docs.trench.bot/bundle-tools/bundle-scanner-guide.md`
- 第三方评测：`https://madeonsol.com/best/telegram-bots`、`https://solanasniperbot.net/best-solana-trading-bots/`
- 2026 工具总览：`https://github.com/buddies2705/awesome-memecoin-trading`（含终端/Telegram/狙击/跟单/聪明钱/bundle 检测分类表）
- Photon 官网：`https://photon-sol.tinyastro.io/`

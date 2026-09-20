# 个人打狗玩家 / 独立开发者的真实做法（2026-09-20）

范围：不研究官方终端，而是找**靠扫链/打狗实际做交易的个人与独立开发者**：他们自己搭什么、看什么指标、用什么阈值、怎么验证。
本轮只做研究；不改代码、不改配置、不启用交易。

## 一、样本与方法

- 搜索上游仍故障（xAI 503 / Exa、Zhipu 无 key / AnySearch 503），未静默切换搜索系统。
- 路径：GitHub 官方 API 找个人仓库 + `git clone` 读代码；直接抓独立开发者公开的数据站/回测页；用公开的产品页面与第三方评测做交叉验证。
- 这些人的数据分为三类，可信度不同：
  1. **代码可读**：仓库里的实现与阈值（如 meme-radar、Hicarus、X-monitor、TG-MemeGPT）。
  2. **作者/厂商自报**：如 0 区块胜率 70–80%、KOL 首触回测（MadeOnSol 是卖 API 的一方）。
  3. **链上可查**：Kolscan 的排行榜钱包 PnL、Solscan 交易哈希。
- 没有 X/Reddit 实时抓取权限，因此不能用当天推文佐证“口碑”；文中只引用可抓到的页面。

## 二、四种真实玩家画像

### A. 延迟流玩家：Jito Shredstream + gRPC 做“0 区块”

代表：`crypdev007/pumpfun-sniper`（Rust，63★，2026-08 更新，作者 TG @0xcrypoops）。

- 做法：Jito Shredstream 监听 → 与 gRPC 结合；因为 **shreds 不含交易 meta**，单用 shreds 拿不到完整交易信息，所以用 shreds 抢时间、用 gRPC 补齐数据。
- 作者声称：shreds 比 gRPC 快约 **100–150ms**；稳定实现“0 区块”狙击；作为**首位买家**胜率 **70–80%**（自报，未独立审计）。
- 代码包含：`auto_trader`、`blockhash_cache`、Redis 缓存、可配置低 tip 阈值、买卖策略；README 给出 create/snipe 的 Solscan 交易对示例。
- 结论：这是最激进的个人路线，核心是**付费/自建低延迟数据 + 抢首位**；成本和竞争都在这一层。

### B. 钱包流玩家：跟对 KOL / 找到“分发者”dev

**MadeOnSol（比利时一人公司，自建数据栈）** 公开了一份可直接引用的回测：
- 样本：38 天、**491,119 笔 KOL 买入、85,760 个不同 mint、426 个 KOL 钱包**；得到 **72,549 次“首个 KOL 触达”事件**。
- 原始信号是噪声：30 分钟内出现 ≥1 个跟随者 34.3%；60 分钟内 ≥2 个 20.2%、≥3 个 13.1%；4 小时内 ≥5 个 7.0%；平均跟随者 1.08 → **约 80% 首触没有后续**。
- 条件化后才有效：按首触 KOL 的 7 日胜率分桶，**60–70% 胜率是甜点**（60 分钟内 ≥2 跟随者 46.9%、≥3 为 37.5%、4 小时 ≥5 为 25.9%）；70%+ 反而降到 21.7%/14.1%/7.4%（顶级短线玩家不广播）。
- 更好的指标是 **per-KOL scout score**：最高者（Jijo）首触后 ≥3 个 KOL 跟随率 51.1%，基线 13.7%，约 3.7 倍提升。
- **样本量就是置信度**：n≥30 次首触时，split-half Pearson r=0.78（±4.3pp）；n<10 时 r=0.05（纯噪声）。他们要求至少 30 次才做 S/A/B/C 分级。
- **延迟**：第二个 KOL 到达的 p25/p50/p75 = **4/12/41 秒**。人工看盘来不及，必须用 WebSocket 推送程序化处理（官方直接说“看着 UI 等提示的人已经输了”）。
- **2026-08 报告**：533 个 KOL、113 万笔交易、90,075 个代币、211 万 SOL；**首次 KOL 买入时的市值中位数 $4,654**；第二个 KOL 平均 9 秒内跟随。
- **关键反直觉结论**：收敛度最高的 10 个币里，**9 个 KOL 净流出为负**，只有 CATE +281 SOL；“KOL 数量衡量的是注意力，不是信念”。
- 相关数据：Pump.fun 2026-08 共 **103 万次发币、18,202 次毕业（1.8%）**、223,339 个 deployer 钱包；**1,170 个钱包发了当月 40% 的代币**；中位毕业时间 1 分钟；毕业生中位峰值市值 $31K。
- 他们还提供 Deployer Hunter：给 deployer 打 tier / 毕业率 / 历史；免费检查 + API 200 calls/day。免费层实时流延迟 5 分钟，付费 <3s，说明**数据延迟本身就是商品**。

**Hicarus（rad1zly）**：用 GMGN API 找“The Distributor”型 dev 钱包。
- 模式：反复 ① 发币 → ② 以 0.5–3.5 SOL 首买 → ③ 秒级 1–3 笔卖出 → ④ 稳定盈利。
- 种子钱包示例：`8inTY66...` 发币 1,935 次、已实现 +$1,358、胜率 66.9%；`6WM3V5...` 发币 658 次、+$1,485、胜率 87.5%。
- 实现：每小时用 `gmgn-cli portfolio activity` + `token traders`，按 `maker_token_tags = creator/dev_team` 过滤，按出现次数 × PnL 排序，推送到 Telegram。README 明确：**只找钱包，不做实时提醒、不做跟单**。

**Kolscan**：公开展示被跟踪钱包的实盘 PnL（如 Pain +1,168 SOL、LJC +576、japbitch +263、decu +110 等），是“个人打狗者确实在赚钱”的社会证据（但榜单只展示赢家，存在幸存者偏差）。

### C. 社媒/喊单流玩家：把 X 和 TG 变成信号

- `simons-freedom/X-monitor`（187★）：Twitter API / webhook / Telegram 推送 → 大模型（支持图片）解析推文 → 提取代币 → 查价格与流动性（默认最小流动性 $10,000）→ 在 ETH/BSC/Solana 自动交易 → 钉钉通知。
- `gaoqiantu/TG-MemeGPT-Trader-Basic`：监控 TG 社群推广（默认 MomentumTrackerCN）→ 要求 **≥3 个社群同时推广**或短时间快速传播 → GPT 打分 0–100（市值越低、聪明钱越多、创建越新、5m/1h/4h 正向、meme 传播力越强分越高）→ **≥80 分自动通过 `@GMGN_sol_bot` 下单并设置止盈止损**；可选 Phantom 私钥直连。
- 官方产品对应物：Axiom Tweet Monitor / Twitter Preview Popup、GMGN X Tracker + SnipeX、Maestro Signals（TG 频道按 Maestro 用户跟踪数排序，支持 Scraper）。这些是“喊单流”的成熟版本。
- 结论：社媒流个人玩家靠**大模型解析 + 多社群交叉验证 + 分数阈值**，但阈值（≥80、≥3 群）都是拍脑袋假设，缺少公开验证。

### D. 只读雷达 + 人工复核玩家（离我们最近）

代表：`nhovongoc0-max/meme-radar`（379★，2026-09-20 仍更新，作者 DeFi狙击手 @bi_9527zx，中文）。
- 定位：**本地只读、多链 Meme 候选扫描 + 人工复核**；开源版明确“不含私钥、签名、swap 或下单模块”，是从自用版本隔离出来的只读部分。
- 覆盖 7 条链（Solana、BNB、Base、ETH、Robinhood Chain、Arc、Stable），可选择 1–3 条轮询，共享请求预算。
- 结构：**“即时发现”读 1 分钟活跃榜、约 20 秒刷新；严格深度审计独立运行**，明确“不用即时热度冒充安全结论”。
- 硬性拒绝线（README 明示）：已知流动性 <$8,000、DEV 持仓 >1%、买/卖税任一 >5% 或相差 >2 个百分点、明确近 5 分钟零成交。
- 展示区间：市值 $10k–500k，可按 $20k–80k 优先、1 分钟成交额或“新进榜”查看；**新进榜不等于新发币**，榜单缺失不填演示数据。
- 审计规则：默认每轮最多深审 6 个币，按端点权重串行请求，明确的安全拒绝提前结束；短期缓存最长 60 秒；遇到限流等服务端冷却，不绕限制。
- 交叉验证：GMGN 做发现与标签，GoPlus 做合约风险，DexScreener 做市值/流动性/官网；未知字段不假装通过。
- 输出：收藏、备注、桌面提醒、筛选记录导出、**5 分钟–24 小时影子表现跟踪**、中文语音提醒；AVE 页面一键打开 K 线与买卖页，由用户确认交易。
- 另一个只读代表 `yksanjo/pumpmetrics`：用免费 PumpPortal 流做本地 SQLite 统计，输出毕业率、毕业时间、deployer 排行榜。

**辅助工具**：
- `nirholas/kol-quest`（23★）：开源 KOL/聪明钱追踪，聚合 KolScan 利润榜 + GMGN 聪明钱 + 胜率过滤 + 实时交易流。
- `hahzterry/solana-meme-monitor`：60 分钟内 ≥50 笔交易标记 trending、单笔 ≥10 SOL 关注、≥1000 SOL 视为巨鲸、20% 涨幅视为泵——都是“热度阈值”型实现。

## 三、他们的共同点（回答“看不看热度”）

1. **不看“纯热度”。** 真正在打狗的人把信号拆成：
   - 链上事件/新币（必须，第一时间）；
   - 硬性安全（流动性、税、dev 持仓、权限、LP、bundle）；
   - 钱包流（dev 自买自卖、KOL 首触、scout score、净流入）；
   - 热度（X/TG/1 分钟成交/新进榜）用于发现与排序。
2. **最常见的可量化阈值**（个人实现）：
   - 流动性：已知 <$8,000 拒绝；社媒流实现常见最小 $10,000；
   - dev 持仓 ≤1%（meme-radar 硬线）；
   - 税：任一 ≤5%，买卖差 ≤2pp；
   - 市值区间：$10k–500k，优先 $20k–80k；
   - trending：60 分钟 ≥50 笔交易（solana-meme-monitor）；
   - AI 喊单流：≥3 个社群同时推广、评分 ≥80 才自动买；
   - 首触信号：仅 scout tier + n≥30 的 KOL 有意义；80% 首触无后续；第二 KOL 中位 9–12 秒到达。
3. **赚钱点不在“看热度”，在四件事**：
   - 比别人早拿到事件（shreds/gRPC/私有节点）；
   - 选对钱包（scout score + 样本量）；
   - 避开 dev/bundle（dev 自买自卖、bundle current held）；
   - 纪律性退出（TP/SL、时间退出、rug watcher）。
4. **热度本身经常骗人**：官方口径也一样——Cielo 明确说“Volume can be faked. Mindshare can't.”；MadeOnSol 数据显示收敛度最高的 10 个币里 9 个 KOL 净流出为负。
5. **个人玩家的现实约束**：没人能同时做到最快、最好钱包、最全安全——所以他们分层订阅（MadeOnSol €400/mo embed、Cielo 300 钱包免费、GMGN/Maestro 跟单 1% 手续费），或者像 meme-radar 一样只做只读雷达 + 人工复核。

## 四、对我们的直接启示

1. **meme-radar 的形态最接近我们应该做的第一版**：只读、多链、硬阈值、未知不通过、深审与即时发现分离、影子跟踪 5m–24h、人工确认下单。
2. 我们已有的注意力榜（GMGN + X）应定位为“即时发现”，再加一个独立的“严格深审”队列（每轮限量、带权重、限流感知），两者不要混。
3. 增加钱包流层：deployer 质量、scout score（n≥30）、首触 + 跟随者、净流入；这比加更多社交媒体源更有价值。
4. 阈值先用个人玩家公开值做初始假设（$8k 流动性、1% dev、5% 税、$10k–500k 市值、60 分钟 50 笔），全部进影子验证后再调。
5. bundle 与 dev 行为独立成栏：current held %、dev 卖出、秒级自买自卖。
6. 延迟升级排在最后：先用数据证明入场延迟是主要亏损来源，再考虑 shreds/私有通道。

## 五、局限

- MadeOnSol 的回测是**厂商自报**（卖 API），38 天窗口，未独立复现；crypdev007 的 70–80% 胜率是作者声明；meme-radar 的自用（可交易）版本未公开。
- 没有 X/Reddit 实时权限，无法引用当天个人玩家推文/帖子；文中只引用可抓取页面与代码。
- 部分中文项目（X-monitor、TG-MemeGPT、solana-meme-monitor）更新时间在 2025 年，代码可读但可能过时。
- 公开榜单（Kolscan）有幸存者偏差，只能证明“有人赚”，不能证明“整体赚”。

## 六、来源

- 个人仓库：`crypdev007/pumpfun-sniper`、`nhovongoc0-max/meme-radar`、`simons-freedom/X-monitor`、`gaoqiantu/TG-MemeGPT-Trader-Basic`、`hahzterry/solana-meme-monitor`、`nirholas/kol-quest`、`rad1zly/Hicarus`、`yksanjo/pumpmetrics`
- 独立数据站：`https://madeonsol.com/blog/scout-signal-first-kol-touch-backtest-solana`、`/blog/solana-kol-trading-report-august-2026`、`/kol-tracker`、`/deployer-hunter`、`https://kolscan.io/leaderboard`
- 交叉引用：`docs/research/20260920-dogbot-architecture/live-products.md`（官方终端）、`summary.md`（开源实现）

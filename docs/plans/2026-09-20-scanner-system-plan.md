# 扫链交易系统实施计划（2026-09-20）

状态：plan 已写入，尚未开始执行。
Owner：Codex。执行方式：串行阶段（P0 → P9），每个阶段完成或记录阻塞后再进入下一阶段。
关联文档：
## 执行状态（2026-09-20，按阶段更新）

| 阶段 | 状态 | 已完成 | 待完成/阻塞 |
|---|---|---|---|
| P0 数据口径 | ✅ 完成 | 主题注册表、v1/v2 严格解码、LiquidityAdded 毕业、报价分类、买卖 minOut、received_at、dataset quote 守卫、tests/core discover 修复 | — |
| P1 雷达+快照 | 🟡 运行时就绪 | `src/radar/`（store/events/collector/api/solana/pipeline）、`src/safety/fetchers.py`、`src/safety/snapshot.py`、CLI `radar/serve`、`tools/collect_continuous.py` 的 `SCANNER_ENABLED` 只读接入 | 真实 provider 抓取需个人 key |
| P2 安全过滤 | 🟡 代码完成 | `src/safety/filters.py`（13 个过滤器）、orchestrator、ScannerConfig 阈值、fail-closed、学习模式 | 用真实快照填充并跑 2–4 周归因 |
| P3 钱包流 | 🟡 代码完成 | `src/walletflow/`（scoring/pipeline/registry/gmgn）、scout score n≥30、净流入、deployer、bundle cohort | GMGN 凭据下的实盘摄取 |
| P4 决策层 | ✅ 完成 | `src/decision/engine.py` 规则、reason codes、过期、风险预算 | — |
| P5 影子执行 | 🟡 代码完成 | `src/shadow/`（executor/exits/tracker/report）、TP 阶梯/止损/追踪/时间/rug/熔断 | 2–4 周影子运行数据 |
| P6 看板/API | ✅ 完成 | `src/radar/api.py` 只读 API + 三视图看板（即时发现/严格深审/影子跟踪）、CLI `serve` | — |
| P7 验证闸门 | 🟡 数据待积累 | `src/shadow/report.py` 指标与闸门判定、`records_from_store`、CLI `gate --db` | 2–4 周真实影子数据 |
| P8 实盘闸门 | 🟡 代码完成 | `src/decision/live_gate.py` 默认关闭、需影子通过+操作者确认 | 用户明确授权与小额实盘 |
| P9 多链 | 🟡 适配层 | `src/radar/solana.py` 归一化与校验 | Geyser/Jito 实盘接入与凭据 |

- 设计依据：`docs/plans/2026-09-20-scanner-system-design.md`
- 海外实盘终端：`docs/research/20260920-dogbot-architecture/live-products.md`
- 个人玩家打法：`docs/research/20260920-dogbot-architecture/individual-operators.md`
- 开源实现阅读：`docs/research/20260920-dogbot-architecture/summary.md`

## 0. 目标与非目标

**目标**：把现有 Four.meme 监听/交易骨架和新做的注意力榜，重构成一套可审计的扫链系统：
链上事件触发 → 硬性安全过滤 → 钱包资金确认 → 三栏热度排序 → 影子执行与过滤器归因 → 有限实盘。

**非目标**（本计划明确不做）：
- 不把 GMGN/X 热度直接接自动买入。
- 不追 0 区块 shreds/colocation；延迟升级必须由测量数据触发。
- 不运行来源不明的“免费抢币机器人”。
- 不使用 `minOut=0`，不在聊天/日志/仓库暴露私钥。
- 不在影子验收通过前启用真实资金。
- 不修改 `docs/goals/`；不提交/推送，除非用户明确要求。

## 1. 架构与模块地图

```
注意力榜（已有）──┐
                   ├──▶ 决策层 decision/ ──▶ 影子执行 shadow/ ──▶ 退出与风控 exits/risk
链上雷达 radar/ ───┤                              │
安全层 safety/ ────┤                              ▼
钱包流 walletflow/ ┘                        有限实盘 trader/（已有骨架）
```

| 模块 | 路径 | 状态 | 职责 |
|---|---|---|---|
| 注意力榜 | `src/attention/` | 已有（只读） | GMGN + X 候选与热度；市场/社交/资金三栏 |
| 链上雷达 | `src/radar/`（新） | 待建 | BSC TokenManager 事件；产出 `TokenLaunch`；后续 Solana |
| 安全层 | `src/safety/`（新） | 待建 | GoPlus/honeypot/LP/权限/税/持仓/bundle/dev 历史 |
| 钱包流 | `src/walletflow/`（新） | 待建 | 钱包注册、scout score、首触/跟随者、净流入、deployer 质量 |
| 决策层 | `src/decision/`（新） | 待建 | 汇总证据生成带过期时间的 `Decision` |
| 影子执行 | `src/shadow/`（新） | 待建 | 真实报价模拟成交；每过滤器 P&L 归因 |
| 实盘执行 | `src/trader/` | 已有 | 修 `minOut`、报价、私有通道、对账；默认关闭 |
| 回放 | `src/backtest/` + `src/pipeline/` | 已有 | 修数据口径后复用 |
| 看板/API | `src/attention/web/` + 新 API | 已有 + 扩展 | 即时发现 / 严格深审 / 影子跟踪 |

## 2. 数据契约（先定契约，再写实现）

所有事件同时保存 **链上时间 `chain_time`** 与 **本地接收时间 `received_at`**；回放只允许使用当时可见的数据。

```text
TokenLaunch:
  chain, token_address, creator, deployer, quote_asset,
  initial_liquidity, create_tx, block_number, log_index,
  chain_time, received_at, source, schema_version

TokenSnapshot:
  token, observed_at, mcap_usd, liquidity_usd, holders,
  top1_pct, top10_pct, non_lp_max_pct,
  buy_tax_pct, sell_tax_pct,
  mint_authority, freeze_authority, owner_renounced, blacklist, pausable,
  lp_locked_pct, lp_burned,
  bundle_cohort_current_pct, bundle_cohort_total_pct,
  dev_holding_pct, dev_sold, early_sniper_count,
  source_status: {goplus, honeypot, dexscreener, gmgn, rpc},
  unknown_fields: [...], schema_version

WalletEvent:
  chain, wallet, label(dev/deployer/kol/smart/unknown), token, side,
  quote_amount, token_amount, first_touch(bool),
  followers_30m, followers_60m, followers_4h,
  scout_score, n_first_touches_30d,
  chain_time, received_at, source, schema_version

Decision:
  chain, token, action(buy/watch/reject), size_quote, mode(shadow/live),
  reason_codes:[...], safety_report_id, wallet_evidence_ids:[...],
  heat_snapshot:{market, social, funding},
  risk_budget:{per_trade_pct, open_positions, daily_loss_pct},
  expires_at, created_at, schema_version
```

规则：
1. 地址链限定：EVM 小写；Solana 保留大小写并校验 32 字节 base58。
2. 名称/符号匹配永远不能建立合约身份；只有精确地址或官方链接可以。
3. `unknown` 字段在决策层按“未通过”处理（fail-closed），除非显式配置容忍并记录原因。
4. 存储 append-only；`observed_at` 排序；禁止回填未来数据。
5. 每个阶段有独立 `schema_version`，破坏性变更才升级。

## 3. 配置与环境契约

新增配置（写入 `.env.example` 与契约测试）：

```text
SCANNER_ENABLED=true                 # 只读扫描总开关
SCANNER_DB=data/scanner/evidence.sqlite
SCANNER_CHAINS=bsc                    # P1 只开 BSC
SCANNER_DEEP_AUDIT_PER_ROUND=6        # 每轮深审上限
SCANNER_CACHE_TTL_SECONDS=60
GOPLUS_API_KEY=
HONEYPOT_API_URL=https://api.honeypot.is/v2
DEXSCREENER_BASE_URL=https://api.dexscreener.com
SHADOW_ENABLED=true
LIVE_TRADING_ENABLED=false            # 与现有 ENABLE_TRADING 分离
```

- 私钥只允许本机受保护文件；不写入 DB、日志、报告。
- 每个数据源必须有请求预算与冷却；限流时不绕过。
- `.env.example` 变更必须同步契约测试（仓库既有约定）。

## 4. 冷热路径规则

热路径只允许：去重 → 新鲜度检查 → 读本地快照 → 决策 → 构造 → 签名 → 提交。
禁止：热路径内初始化客户端、同步取 blockhash、查余额、查池子、调用外部风控 API。
所有外部数据必须后台预取为本地快照；快照缺失或过期按配置 fail-closed 或显式跳过。
每次事件记录：`chain_time → received_at → decision_at → submit_at → landed_at`，用于延迟分位统计。

## 5. 阶段计划（串行）

### P0：数据口径修复（必须先做）

任务：
- [ ] 修 ABI/主题漂移：核对 TokenManager/TokenManager2 ABI 与链上主题；确保 `c18aa711…` 只作为 `LiquidityAdded` 处理，绝不标成卖出；主题哈希注册表带 ABI 版本。
- [ ] 报价归一：区分 BNB/USDT/GMEB 等 quote asset；历史 FX 单独记录；禁止统一乘 BNB/USD 计算市值。
- [ ] 迁移处理：内盘买入前检查是否已毕业；已迁移的币不进内盘买入假设，改走 DEX 路径或明确拒绝。
- [ ] `minOut` 修复：Pancake 卖出改为按滑点计算 `amountOutMin`；买入 `minAmount` 不再用 1 兜底，评估链上合约最小值。
- [ ] 时间边界：回放使用 `received_at`，禁止使用未来数据；重复事件去重。
- [ ] 回归测试：主题映射、报价归一、迁移、`minOut`、时间边界。

涉及文件：`src/core/listener.py`、`src/data/fourmeme_log_decoder.py`、`src/trader/bot.py`、`src/backtest/*`、`config/*.abi`、`tests/core/*`、`tests/model/*`。

验收：回放不再产生假成交；现有 `python -m unittest discover` 通过；新增契约测试覆盖上述五类修复。

### P1：链上雷达 + 快照服务

任务：
- [ ] 新建 `src/radar/`：从现有 listener 抽取 BSC `TokenLaunch` 事件；append-only 存储；来源健康与背压。
- [ ] 新建 `src/safety/fetchers/`：GoPlus、honeypot.is、DexScreener、GMGN（有凭证时）、RPC（LP/持有人/权限）快照抓取。
- [ ] 请求预算：每源限速、冷却、每日上限；限流时降速等待，不绕过。
- [ ] 短缓存：≤60 秒；缓存命中/过期/缺失状态显式记录。
- [ ] CLI：`scripts/run_scanner.py watch|snapshot|audit|shadow|serve`。
- [ ] 深审队列：每轮最多 6 个候选，按来源权重串行/并发受控。

验收：每个新 Four.meme 币产生一个 `TokenLaunch`；6 个候选的深审在预算内完成；每个字段有来源与时间；unknown 不被写成 pass。

### P2：安全过滤层

过滤器清单（每个独立模块，返回 pass/fail/skip/error + 证据 + 耗时）：

| 过滤器 | 初始阈值（假设，待影子验证） | 来源 |
|---|---|---|
| liquidity_min | 已知 <$8,000 拒 | meme-radar |
| dev_holding | 已知 >1% 拒 | meme-radar |
| taxes | 任一 >5% 或买卖差 >2pp 拒 | meme-radar |
| recent_trades | 明确近 5 分钟零成交拒 | meme-radar |
| mcap_band | 展示 $10k–500k，优先 $20k–80k | meme-radar |
| permissions | mint/owner/blacklist/pause 任一危险拒；Solana mint/freeze 必须放弃 | GoPlus/通用 |
| holders | Top1 ≤12–15%、Top10 ≤30–35%、单非 LP ≤8% | Memcoin 预设 |
| lp_lock | 未锁/未销毁拒或降级 | 通用 |
| honeypot_sim | 模拟卖出失败或税超限拒；新池未索引 skip 而非 pass | Memcoin |
| bundle_cohort | 同区块 ≥4 笔 + 单一非创建者主导拒；重点看 current held % | Axiom/Trench Radar |
| dev_history | deployer 毕业率/rug 率；重复 rug 拒 | MadeOnSol |
| early_snipers | 前 6 秒 >10 笔拒或减半仓 | Memcoin |
| social_signal | 只排序与提醒，不硬否决 | 官方终端一致 |
| funding_flow | 资金确认项（见 P3） | MadeOnSol/Cielo |

任务：
- [ ] 实现过滤器注册表与编排器（并行 + 超时预算）。
- [ ] 关键过滤器 fail-closed；非关键过滤器可 skip 但必须记录。
- [ ] Learning 模式：只保留 honeypot 门禁，收集全量过滤器结果，用于后续归因。
- [ ] 每个过滤器的阈值只出现在配置文件，代码不含魔法数。
- [ ] 单元测试 + 真实响应 fixture 测试。

验收：同一输入决策可复现；unknown 不被当成 pass；学习模式与安全模式可切换；限流与错误路径有测试。

### P3：钱包流层（资金确认）

任务：
- [ ] 钱包注册表：链限定；标签 dev/deployer/kol/smart/unknown；来源与首次发现时间。
- [ ] 钱包事件：GMGN `portfolio activity`、`token traders`、`maker_token_tags`（如可用）；链上监听被跟踪钱包（BSC 先做）。
- [ ] scout score：swarm rate = P(60 分钟内 ≥3 个跟随者 | 首触)；要求 30 天样本 n≥30；split-half 稳定性检查；S/A/B/C 分级。
- [ ] 首触与跟随：首触事件、30m/60m/4h 跟随者数；净流入（买入−卖出）。
- [ ] deployer 质量：毕业率、rug 率、历史 token 数；重复 rug 钱包黑名单。
- [ ] "Distributor" 检测：发币→首买→秒级卖出模式（Hicarus 模式）。
- [ ] bundle cohort：同槽位买入钱包集合 + **current held %**。
- [ ] 推送：首触用 WebSocket/推送，不轮询；记录 event→handler 延迟。

验收：scout score 可由存储事件复现；n<30 明确标记“样本不足”；不使用未来数据；合成队列测试通过。

### P4：决策层

任务：
- [ ] 入场条件：链上事件完整 + 硬过滤全过 + 资金确认至少一条 + 市值/流动性/报价可接受 + 风险预算允许。
- [ ] 资金确认至少一条：deployer 质量达标 / scout tier 首触且 n≥30 / 多钱包持续净流入。
- [ ] `Decision` 记录：reason codes、证据 ID、过期时间（建议 20–60 秒）、模式 shadow/live。
- [ ] 风险预算：单笔 0.25–5%（默认 1%）、最多 10 并发、60 秒内最多 3 仓、同叙事暴露上限、最多部署 50% 资金。
- [ ] 熔断：日亏 10% 停 24h、周亏 25% 停 72h、连败 20 笔停 24h；只拦新开仓。

验收：同一输入生成完全相同的 Decision；过期后不执行；没有 safety_report_id 的 Decision 不允许进入执行。

### P5：影子执行 + 退出与风控

任务：
- [ ] 影子执行：真实报价（router `getAmountsOut` / DEX API）+ 滑点、失败、tip、优先费、延迟模拟；不加载私钥。
- [ ] 退出引擎：TP 阶梯（+50% 卖 25%、+100% 卖 25%、+300% 卖 25%、+900% 卖 15%、留 10%）、止损 −40%、+200% 后 30% 回撤追踪、30 分钟未到 +50% 时间退出。
- [ ] rug watcher：LP 单笔跌 >30–50% 或 dev 卖出 → 立即退出/降级。
- [ ] 影子记录：每个过滤器的 pass/fail、入场价、出场价、费用、峰值、回撤、退出原因。
- [ ] 对账：影子成交与后续真实价格核对，输出偏差报告。

验收：影子 P&L 可按过滤器归因；偏差报告可解释；不产生任何链上交易。

### P6：看板与只读 API

任务：
- [ ] 三视图：即时发现（1 分钟活跃榜、约 20 秒刷新）、严格深审（每轮 ≤6、按权重）、影子跟踪（5m/15m/1h/6h/24h）。
- [ ] 栏目：市场热度、社交热度、资金热度、安全/bundle、unknown/过期状态、原帖/来源链接。
- [ ] 中文语音提醒（可选）、收藏/备注/导出（参考 meme-radar）。
- [ ] API：GET-only `/api/v1/candidates`、`/audits`、`/wallets`、`/decisions`、`/shadow`、`/health`；默认 localhost；`trading_enabled=false` 字段保留。

验收：桌面与移动浏览器 smoke 通过；无控制台错误；未知字段不被显示为通过；不泄露密钥。

### P7：验证闸门（2–4 周影子）

指标：
- [ ] 净期望（扣除手续费、tip、优先费、滑点、失败）为正且跨周稳定。
- [ ] 收益不依赖单一幸运币/单一钱包。
- [ ] 过滤器归因：资金确认显著提升期望；无用的过滤器被降权或移除。
- [ ] 延迟分布：p95 在策略窗口内；被夹率与失败率可接受。
- [ ] 最大回撤在预算内；熔断演练通过。
- [ ] 学习模式 vs 安全模式对比报告。

实盘闸门：以上全部满足才允许进入 P8；任何一项不满足则继续影子或调整过滤器。

### P8：BSC 小资金实盘

任务：
- [ ] 专用热钱包、小额、单独配置；私钥隔离。
- [ ] 限额：单笔上限、并发上限、日/周亏损熔断、总资金上限。
- [ ] 私有通道：影子数据证明被夹/抢不到是瓶颈后，接 bloXroute/NodeReal 私有交易或 bundle。
- [ ] 对账：链上 tx hash 与 Decision/影子记录一一对应；偏差报告。
- [ ] Kill switch：一键停止新开仓；退出仍可执行。
- [ ] systemd/memectl 运行说明与日志脱敏。

验收：实盘与影子偏差可解释；熔断演练成功；没有密钥泄漏；`ENABLE_TRADING` 默认仍为 false。

### P9：多链扩展

任务：
- [ ] Solana 适配器：pump.fun IDL + logsSubscribe/Geyser；Jito/Sender 提交；独立钱包与限额。
- [ ] Base/EVM：复用安全层与钱包流层。
- [ ] 每链独立开关；跨链资金不自动搬移。

验收：单链故障不影响其他链；每链有独立影子报告与闸门。

## 6. 成本与外部依赖

| 项 | 用途 | 成本/限制 | 备注 |
|---|---|---|---|
| GMGN API | 发现、标签、钱包事件 | 1%/笔（跟单）；API 配额按账号 | 只读发现可用 |
| X API recent search | 社交热度 | 约 $0.005/帖（当前定价） | 不是全网实时流 |
| GoPlus Token Security | 合约风险 | 免费/按额度 | 需缓存 |
| honeypot.is | 模拟卖出 | 免费接口 | 新池未索引时 skip |
| DexScreener | 市值/流动性/官网 | 免费限速 | 交叉校验 |
| RPC/私有通道 | 监听与提交 | 按 provider | BSC 先用公共 RPC，后评估私有 |
| MadeOnSol 等 | 钱包流（可选） | 免费层延迟 5 分钟；付费 <3s | 先自建，后评估 |

## 7. 测试与验收标准

- 测试框架：`python -m unittest discover`（仓库约定，不用 pytest）。
- 每个阶段新增测试：契约、过滤器 fail-closed、限流、时间因果、决策可复现、影子费用、熔断。
- 浏览器：playwright skill，桌面 + 移动，0 console errors。
- 关键数字：影子期报告必须列出过滤器归因、净期望、回撤、延迟分位、被夹/失败率。
- 关键边界：unknown、过期、限流、断流、迁移、非 BNB 报价、dev 卖出、bundle current held。

## 8. 风险与未验证项

- 个人玩家/厂商的数字（MadeOnSol 回测、crypdev007 70–80%、meme-radar 阈值）均为自报或小样本；必须用我们的影子数据重新验证。
- BSC 私有通道收益、Solana gRPC/Geyser 延迟、Four.meme 当前 ABI 完整性未验证。
- 第三方标签（KOL/聪明钱）可能滞后或错误；需要样本量与稳定性门槛。
- 影子成交无法完全模拟真实排队与 MEV；实盘闸门必须包含偏差容忍度。
- 公开搜索上游故障时，研究类工作必须显式记录证据来源限制。

## 9. 执行日志

- 2026-09-20：plan 写入。
- 2026-09-20：P0 完成并验证：修复 listener 主题漂移/伪造主题/v1 布局、`minOut=0`、`minAmount=1`、报价分类、毕业事件、received_at 与 dataset quote 守卫；`tests/core/__init__.py` 补齐后 `unittest discover` 覆盖 1471 项。
- 2026-09-20：P1–P5 代码层与闸门代码完成：radar/snapshot fetchers、安全过滤器、钱包流、决策引擎、影子执行与退出、验收闸门、只读 API、Solana 归一化；`tests/scanner/` 56 项通过。
- 待运行/阻塞：真实 provider 凭证、2–4 周影子数据、BSC 私有通道收益验证、Solana Geyser/Jito 实盘接入、用户明确授权的小额实盘。
- 2026-09-20：补齐运行时接线：`tools/collect_continuous.py` 增加 `SCANNER_ENABLED` 只读扫描开关（默认关闭）；`src/radar/pipeline.py` 串起雷达→快照→安全报告→决策→影子；scanner 看板三视图与 `/api/v1/scanner/{launches,graduations,safety,decisions,shadow}`；`records_from_store` + `gate --db` 直接用影子库评估闸门。
- 验证：`PYTHON_DOTENV_DISABLED=1 python3 -m unittest discover` = 1474 tests, 1 skipped, 0 failures；`tests/scanner/` = 59 tests。

## 10. Scoreboard 收口

`docs/model_scoreboard.md` 已记录本方向的研究与设计条目；本计划本身不产生模型结论或收益承诺。执行 P0 起，每个阶段完成后在仓库约定位置记录证据与 scoreboard 决策（更新或明确不更新）。

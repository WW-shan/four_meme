# 扫链交易系统设计（2026-09-20）

状态：设计提案，未实施。可执行实施计划见 `docs/plans/2026-09-20-scanner-system-plan.md`（P0–P9）。依据：`docs/research/20260920-dogbot-architecture/summary.md`（开源实现）、`live-products.md`（实盘终端）、`individual-operators.md`（个人玩家打法）。本轮不改代码、不改 `.env`、不启用交易。

## 一、设计原则

1. **链上事件是扳机，热度和社交是排序与提醒。** Axiom Pulse、GMGN NEW PAIR、Cielo Pulse、Padre Trenches、Maestro Auto Snipe 都是事件驱动；没有一家把纯 X 热度当唯一触发。
2. **钱包资金是确认，不是热度。** Cielo 的 Mindshare 用被跟踪钱包的买入数衡量热度；MadeOnSol 的回测证明“哪个 KOL 先买”比“有多少人讨论”更有信息量。
3. **过滤和退出比速度更决定生死。** pump.fun 2026-08 的毕业率约 1.8%（103 万次发币、18,202 次毕业）；先把买错币的概率降下来，再谈抢多快。
4. **未知等于未验证，不等于通过。** 个人项目 meme-radar 的规则：未知字段不假装通过，缺失的持仓不当作零。我们的过滤器必须 fail-closed。
5. **影子执行先于实盘。** 头部个人玩家（meme-radar）公开版只有只读扫描 + 人工复核；Memcoin-Sniper-Bot 要求纸面 2–5 周。我们先做可复现的影子 P&L。
6. **延迟升级由数据触发。** 先测本地 receive→submit→land 分布；只有证明“入场延迟是主要亏损来源”时才上 gRPC/Geyser/shreds/私有交易通道。

## 二、总体架构

```
 ┌────────────────────┐   ┌────────────────────┐   ┌────────────────────┐
 │ A. 发现层           │   │ B. 安全层           │   │ C. 资金层           │
 │ attention/ 已有     │   │ 新增 safety/        │   │ 新增 walletflow/    │
 │ GMGN + X 候选/热度  │   │ GoPlus/honeypot/LP  │   │ 聪明钱/KOL/dev 钱包  │
 │ 市场/社交/资金三栏   │   │ 权限/税/持仓/bundle │   │ scout score/首触/流入│
 └─────────┬──────────┘   └─────────┬──────────┘   └─────────┬──────────┘
           │                        │                        │
           ▼                        ▼                        ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ D. 决策层 decision/：候选 → 硬过滤 → 资金确认 → 仓位/风险预算 → Decision│
 └───────────────┬───────────────────────────────┬─────────────────────┘
                 │                               │
                 ▼                               ▼
        ┌─────────────────┐             ┌─────────────────┐
        │ E. 影子执行      │             │ F. 实盘执行(后)   │
        │ shadow/ 真实报价 │             │ trader/ 已有骨架  │
        │ 记录每个过滤器    │             │ 私有通道/限额/对账│
        └────────┬────────┘             └────────┬────────┘
                 │                               │
                 └──────────────┬────────────────┘
                                ▼
                    ┌────────────────────────┐
                    │ G. 退出与风控 exits/risk│
                    │ TP 阶梯/追踪/时间/rug   │
                    │ 日周熔断/只拦新开仓      │
                    └────────────────────────┘
```

关键点：**发现层不直接下单**。它只产出候选、热度和证据；决策层必须拿到安全层与资金层的结论才生成 `Decision`。实盘执行只消费带签名的 `Decision`，并且可以被风控一键停掉。

## 三、模块与职责

| 模块 | 状态 | 职责 | 关键产出 |
|---|---|---|---|
| `src/attention/` | 已有（只读） | GMGN 多链热搜 + X 主题轮询；三栏指标；SQLite 证据；只读 API/看板 | 候选、热度时间序列、来源健康 |
| `src/radar/`（新） | 待建 | 链上事件监听：BSC TokenManager 日志（复用现有 listener 能力）；后续 Solana pump.fun（IDL + Geyser/logs） | `TokenLaunch` 事件（含本地接收时间） |
| `src/safety/`（新） | 待建 | 合约安全、LP、税、权限、持仓集中度、dev 历史、bundle cohort；后台缓存快照，热路径零 RPC | `SafetyReport`（pass/fail/unknown + 证据） |
| `src/walletflow/`（新） | 待建 | dev/KOL/聪明钱钱包追踪；scout score；首触事件；跟随者数量；净流入 | `WalletEvent`、`WalletScore` |
| `src/decision/`（新） | 待建 | 汇总候选 + 安全 + 资金 + 仓位规则，生成 `Decision`（含过期时间与原因） | `Decision`（可审计） |
| `src/shadow/`（新） | 待建 | 用真实报价模拟成交（滑点、失败、tip、优先费），记录过滤器归因 | 影子成交与 P&L |
| `src/trader/` | 已有 | 实盘执行；BSC 买入/卖出骨架；需修 `minOut`、私有通道、对账 | 链上成交 |
| `src/backtest/` | 已有 | 离线回放；需修数据口径（报价、迁移、时间边界） | 回放报告 |
| `src/attention/web/` | 已有 | 看板：即时发现 / 严格深审 / 影子跟踪 | 人工复核界面 |

## 四、数据契约

所有事件必须同时保存 **链上时间** 与 **本地接收时间**，回放只允许使用当时可见的数据：

```text
TokenLaunch: chain, token_address, creator, deployer, quote_asset,
             initial_liquidity, create_tx, block, log_index,
             chain_time, received_at, source (radar/attention)
TokenSnapshot: token, observed_at, mcap, liquidity, holders,
               top1_pct, top10_pct, buy_tax, sell_tax,
               mint_authority, freeze_authority, lp_locked,
               bundle_cohort_current_pct, bundle_cohort_total_pct,
               dev_holding_pct, early_sniper_count, source_timestamps
WalletEvent: wallet, label(dev/kol/smart/unknown), token, side,
             sol_amount, token_amount, first_touch(bool),
             follower_count_30m/60m/4h, scout_score, n_first_touches,
             chain_time, received_at
Decision: token, action(buy/watch/reject), size, reason_codes,
          safety_report_id, wallet_evidence, heat_snapshot,
          risk_budget, expires_at, mode(shadow/live)
```

规则：
- 地址必须链限定（EVM 小写、Solana 保留大小写并校验 32 字节）。
- 名/符号匹配永远不能建立合约身份；只有精确地址或官方链接才允许关联。
- 任何 `unknown` 字段在决策层按“不可通过”处理，除非显式配置为可容忍并记录原因。
- 一切 append-only；不允许回填未来数据。

## 五、冷热路径与延迟预算

**热路径（收到事件后到提交）只允许**：去重 → 新鲜度检查 → 读本地快照 → 决策 → 构造 → 签名 → 提交。禁止在热路径做 RPC 查询、余额查询、外部风控 API 调用。

BSC 现状与目标：

| 阶段 | 监听 | 提交 | 备注 |
|---|---|---|---|
| 现在 | Four.meme 日志（logsSubscribe + HTTP 补块） | 公共 RPC + gas 倍率 | 先修 ABI/主题漂移与 `minOut` |
| P2 | 同上 + 本地快照 | bloXroute / NodeReal 私有交易或 bundle | 只在影子数据证明被夹/抢不到是瓶颈后启用 |
| 目标 | 事件延迟测量：链上时间→本地接收→提交→上链 | 记录 p50/p95 | 用数据决定是否升级 |

Solana 若启动，单独适配器：pump.fun IDL + Geyser/logs 监听，Jito/Helius Sender 提交；**不追 shreds/0 区块**，除非我们的数据证明延迟是主要亏损来源。

## 六、过滤器与初始阈值（假设，待影子验证）

参考个人实现（meme-radar、Memcoin-Sniper-Bot）与官方终端（Axiom/Trench Radar）：

| 过滤器 | 初始规则 | 依据 |
|---|---|---|
| 流动性 | 已知 <$8,000 直接拒；深审补齐未知值 | meme-radar 硬拒绝线 |
| dev 持仓 | 已知 >1% 拒；缺失不当零 | meme-radar |
| 买卖税 | 任一 >5% 拒；买/卖差 >2 个百分点拒 | meme-radar |
| 近 5 分钟成交 | 明确零成交拒 | meme-radar |
| 市值区间 | 展示 $10k–500k；优先 $20k–80k | meme-radar |
| 权限 | BSC：mint/owner/blacklist/暂停；Solana：mint/freeze authority 必须放弃 | GoPlus + 通用 |
| 持仓集中度 | Top1 ≤12–15%；Top10 ≤30–35%；单非 LP 钱包 ≤8% | Memcoin 预设 |
| LP | 未锁/未销毁拒（或按锁仓比例降级） | 通用 |
| honeypot | 模拟卖出失败或卖出税超限拒；新池未索引时 skip 而非 pass | Memcoin honeypot-sim |
| bundle | 同区块 ≥4 笔 + 单一非创建者主导 → 拒；重点看 **current held %** 而非 total | Axiom/Trench Radar |
| dev 历史 | deployer 毕业率/rug 率；重复 rug 钱包拒 | MadeOnSol Deployer Hunter |
| 早期狙击 | 前 6 秒 >10 笔 → 拒绝或减半仓 | Memcoin anti-sniper-war |
| 社交 | 作为排序与提醒，不作硬否决 | 官方终端一致做法 |
| 资金流 | 作为确认项（见第七节） | MadeOnSol/Cielo |

阈值全部配置化，并标记为 **hypothesis**；影子期结束后按每过滤器 P&L 归因调整，不凭感觉。

## 七、钱包流层（最关键的确认）

个人玩家的回测给出可直接复用的方法（MadeOnSol，2026-08，491k KOL 交易 / 38 天 / 426 个 KOL）：

- **原始“任意 KOL 首触”是噪声**：30 分钟内 ≥1 个跟随者 34.3%；60 分钟内 ≥2 个 20.2%、≥3 个 13.1%；4 小时内 ≥5 个 7.0%；平均跟随者 1.08 → 约 80% 没有后续。
- **必须按“哪一个 KOL”条件化**：7 日胜率 60–70% 的 KOL 是甜点区（60 分钟内 ≥2 跟随者 46.9%，≥3 为 37.5%，4 小时 ≥5 为 25.9%）；70%+ 反而下降（21.7%/14.1%/7.4%），因为顶级短线玩家不广播。
- **per-KOL scout score 优于胜率分桶**：最高者首触后 ≥3 个 KOL 跟随率 51.1%，是基线 13.7% 的 3.7 倍。
- **样本量决定置信度**：n≥30 次首触的 split-half Pearson r=0.78（±4.3pp）；n<10 时 r=0.05（纯噪声）。低于 30 次不进入 S/A/B/C 分级。
- **延迟窗口**：第二个 KOL 到达的 p25/p50/p75 = 4/12/41 秒；人工看盘来不及，必须程序化推送（WebSocket，不轮询）。
- **“KOL 数量”衡量的是注意力，不是信念**：2026-08 收敛度最高的 10 个币里 9 个 KOL 净流出为负，只有 1 个为正。

落地设计：
1. 维护 `walletflow` 表：钱包、标签、首触、30m/60m/4h 跟随者数、scout score、样本量。
2. 只有 scout tier 达标且 `n_first_touches ≥ 30` 的钱包首触才触发“资金确认”。
3. 记录 **净流入**（买入 SOL − 卖出 SOL）与“跟随者是否持续买入”，净流出转负即降级。
4. dev/发币人单独一层：deployer 历史毕业率、rug 率、是否“自买自卖”（Hicarus 的 Distributor 模式：发币→0.5–3.5 SOL 首买→1–3 笔内秒卖；示例钱包 1,935 次发币、+$1,358、胜率 66.9%；658 次、+$1,485、胜率 87.5%）。
5. bundle cohort：记录同槽位买入钱包集合及其 **当前持有占比**；current held 高才是真雷。

## 八、热度层：三栏，不合成黑盒分

| 热度类型 | 指标 | 用途 |
|---|---|---|
| 市场热度 | 1m/5m/1h 成交、涨幅、holder 数、流动性、成交笔数 | 排序与候选分层 |
| 社交热度 | X 提及/独立作者/大 V 账号/原帖链接；TG 喊单频道 | 叙事发现与提醒 |
| 资金热度 | 被跟踪钱包净流入、首触、跟随者、scout score、deployer 质量 | **买入前确认** |

规则：
- 三者独立展示，保留原始来源与时间，不合并成单个“热度分”。
- 社交热度只在白名单账号/频道范围内统计；缺失数据不当作零。
- 资金热度的净流入转负、bundle current held 高、dev 开始卖出 → 直接降级/退出。

## 九、决策与退出规则

入场必须同时满足：
1. 链上事件已确认（合约地址、链、创建信息完整）。
2. 硬过滤全过（未知视为未过）。
3. 资金确认至少一条：deployer 质量达标 / scout tier 首触 + n≥30 / 多钱包持续净流入。
4. 市值与流动性在区间内，目标仓位能拿到可接受报价。
5. 风险预算允许（并发数、单日亏损、同叙事暴露）。

退出（参考 Memcoin/Trench 做法）：
- TP 阶梯：+50% 卖 25%、+100% 卖 25%、+300% 卖 25%、+900% 卖 15%，留 10% moonbag（参数化）。
- 止损 −40% 全平；+200% 后 30% 回撤追踪；30 分钟未到 +50% 时间退出。
- rug watcher：LP 单笔跌 >30–50% 立即市价卖；dev 卖出立即降级。
- 熔断：日亏 10% 停 24h、周亏 25% 停 72h、连败 20 笔停 24h；**只拦新开仓，止损/止盈永远放行**。
- 仓位：默认 1%（0.25–5%），最多 10 并发，60 秒内最多开 3 仓，同一池不加仓，最多部署 50% 资金。

## 十、影子执行与验收

影子期至少 2–4 周（BSC 单链），记录：
- 每个候选的全部过滤器结果（pass/fail/skip/error + 耗时 + 来源时间）。
- 影子成交：真实报价、滑点、失败、tip、优先费、延迟分布。
- 结果：每个过滤器的胜率/期望值、整体净期望（扣全部成本）、最大回撤、对单一币/单一钱包的依赖度。

实盘闸门（全部满足才上小资金）：
1. 影子净期望为正且跨周稳定；
2. 收益不依赖单一幸运币；
3. 过滤器归因显示资金确认确实提升期望；
4. 延迟分布可接受（p95 仍在策略窗口内）；
5. 熔断、退出、对账、密钥隔离全部演练通过。

## 十一、分阶段路线图

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0（现在） | 修数据口径：ABI/主题漂移、报价归一、`minOut=0`、时间边界；建 `TokenLaunch`/`Snapshot` 契约 | 回放不再产生假成交；单元测试通过 |
| P1 | 安全层 + 钱包流层 + 决策层（只读），接入注意力榜与 BSC 监听 | 每个候选都有完整证据链与 unknown 标记 |
| P2 | 影子执行 + 过滤器归因 + 看板三视图（即时/深审/影子） | 2–4 周影子报告，净期望与过滤器归因可审查 |
| P3 | BSC 小资金实盘：限额、私有交易通道、对账、熔断 | 实盘 P&L 与影子偏差在可解释范围 |
| P4 | 多链：Solana 适配器（pump.fun + Geyser/logs + Jito），Base/EVM 复用 | 单链可独立开关；不共享私钥 |

## 十二、明确不做 / 未验证

- 不把 GMGN/X 热度直接接自动买入。
- 不追 0 区块 shreds/colocation；延迟升级必须由数据触发。
- 不运行来源不明的“免费抢币机器人”；不 `minOut=0`；不在聊天/日志暴露私钥。
- 未验证：MadeOnSol/crypdev007/meme-radar 的数字均为作者/厂商自报或小样本；我们的阈值必须用影子数据重新验证。
- 未验证：BSC 私有通道的实际收益、各 provider 延迟与费用、Four.meme 当前 ABI 的完整事件集。

## 十三、与现有代码的映射

| 现有资产 | 复用方式 |
|---|---|
| `src/core/listener.py` + `src/data/fourmeme_log_decoder.py` | 作为 BSC 雷达的事件源；先修 ABI/主题漂移 |
| `src/trader/bot.py` | 作为实盘执行骨架；修 `minOut`、非 BNB 报价、对账、私有通道 |
| `src/attention/` | 作为发现/热度层；增加资金热度栏与钱包流接口 |
| `src/backtest/` + `src/pipeline/` | 作为回放与训练基础设施；数据口径修好后复用 |
| `docs/research/20260920-dogbot-architecture/` | 本设计的证据来源 |

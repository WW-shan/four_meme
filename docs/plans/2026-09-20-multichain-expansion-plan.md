# 多链扫链扩展计划（2026-09-20）

目标：把雷达从“BSC 单 Four.meme 合约”扩展为 **GMGN 9 链发现 + 可配置链适配器**，先补全 BSC，再 Robinhood、Solana、Base/ETH/Arbitrum/HyperEVM，Arc 只读观察，Stable 暂缓。
依据：`docs/research/20260920-dogbot-architecture/chain-coverage.md`、`scanner-system-plan.md`。

## 0. 原则

- 发现层统一：GMGN hot searches / trending / trenches + 平台过滤 + X/社交排序。
- 扫描层分链：EVM 适配器参数化（chain_id/RPC/工厂/ABI），Solana 单独适配（Geyser/logs）。
- 统一定义：链限定地址、`TokenLaunch`、`TokenSnapshot`、`WalletEvent`、`Decision`；执行按链独立。
- 未知字段继续 fail-closed；影子验证先于实盘；不跨链自动搬资金。
- 不追 shreds/私有通道，除非影子数据证明延迟是主要亏损来源。

## 1. 阶段计划

### X10.1 适配器框架与配置（先做）

- [ ] `src/radar/adapters/base.py`：`ChainAdapter` 接口（`discover()` / `normalize()` / `snapshot()` / `healthcheck()`）。
- [ ] `src/radar/adapters/registry.py`：按 chain 注册、启停、预算与健康状态。
- [ ] `config/chains.json`：每链 chain_id、RPC、工厂/launchpad 地址、DEX、报价资产、预算。
- [ ] `SCANNER_CHAINS` 环境契约 + 契约测试。
- 验收：注册表可加载 9 链配置；未启用链不产生请求；单链失败不影响其他链。

### X10.2 BSC 全平台（第一个完整闭环）

- [ ] Four.meme 现有适配器迁入 `adapters/evm_fourmeme.py`。
- [ ] Flap / four_xmode_agent / cubepeg / likwid / goplus / openfour 等 BSC launchpad 适配器。
- [ ] PancakeSwap V2/V3 `PairCreated` 新池适配器。
- [ ] 多源去重 + 统一 `TokenLaunch` + 真实链上 E2E（扩展 `scripts/e2e_scanner_smoke.py`）。
- 验收：BSC 采样窗口内能同时发现 Four.meme 与其他平台新币；重复事件不重复计数。

### X10.3 Robinhood（新链优先）

- [ ] GMGN 发现：robinhood 链 hot searches / trending / trenches，平台过滤（trench/pons/flap/bags/clanker 等 27 个）。
- [ ] 链上适配器：RPC + 工厂/平台地址（需要 RPC 与平台 ABI）。
- [ ] 条件单支持：GMGN 文档确认 robinhood 支持 condition orders（TP/SL）。
- 验收：只读发现 + 影子；Shadow gate 通过前不接实盘。

### X10.4 Solana

- [ ] `adapters/solana.py`：pump.fun / bonk / bags 事件（Geyser/logs）。
- [ ] 地址/账户校验、ATA/ALT/blockhash 预热、失败重试与对账。
- [ ] 执行通道：Jito / Helius Sender（凭证后接入）。
- 验收：单链独立影子报告；不共享 BSC 私钥。

### X10.5 Base / ETH / Arbitrum / HyperEVM

- [ ] 复用 EVM 适配器：只替换 chain_id、RPC、工厂与平台地址。
- [ ] 每条链独立安全快照（GoPlus/DexScreener 多链）+ 预算。
- 验收：每链可单独开关、单独影子报告、单独健康状态。

### X10.6 Arc 观察 / Stable 暂缓

- [ ] Arc：只读发现 + 影子；GMGN 仅支持普通 swap/limit order，不支持 TP/SL。
- [ ] Stable：当前探测 0 token，标记 `watch_only`，不投入开发。
- 验收：配置与看板明确显示“观察/暂缓”，不产生交易决策。

### X10.7 看板与 API 多链化

- [ ] 过滤：chain / platform / 时间窗；每链来源健康与预算。
- [ ] 三视图不变：即时发现 / 严格深审 / 影子跟踪；跨链不合并热度分。
- 验收：桌面/移动浏览器通过；单链断流显示 stale，不显示为热度下降。

### X10.8 验证与上线闸门

- [ ] 每链 2–4 周影子：净期望、过滤器归因、延迟 p95、最大回撤、单一币依赖。
- [ ] 通过后仅该链进入小资金实盘；`ENABLE_TRADING` 保持默认 false。
- 验收：实盘与影子偏差可解释；熔断演练通过；无密钥泄漏。

## 2. 复用与新增文件

- 复用：`src/radar/`（store/collector/pipeline/api）、`src/safety/`、`src/walletflow/`、`src/decision/`、`src/shadow/`、`src/attention/`（GMGN/X 发现）。
- 新增：`src/radar/adapters/`、`config/chains.json`、各链适配器与测试、扩展 `scripts/e2e_scanner_smoke.py`、`scripts/run_scanner.py` 增加 `--chain`。

## 3. 风险 / 未验证

- 新链平台清单会变化（Robinhood 允许列表可能滞后）。
- Arc/Stable 功能受限（无 TP/SL；Stable 无量）。
- Solana/BSC 私有通道、RPC 配额与费用需要实测。
- 多链并行会稀释质量：按 X10.2 → X10.3 → X10.4 → X10.5 串行推进。

## 4. 执行状态

- 2026-09-20：plan 写入。
- 2026-09-20：X10.1 完成（适配器注册表 + `config/chains.json` 9 链）。
- 2026-09-20：BSC 完成 Four.meme / Pancake V2 / Pancake V3 / Flap / OpenFour 的实盘核实（见 `docs/research/20260920-live-chain-verification/`）。
- 2026-09-20：Robinhood 拿到首个有来源的发射台（Doppler，官方部署表），实测 2000 区块内 4 次 `Create`。
- 2026-09-20：Solana 完成 pump.fun / letsbonk / boop / moonshot 程序核实；virtuals 程序休眠。
- 2026-09-20：Base 完成 Clanker v4 / Flaunch / Doppler 核实；ETH 与 Arbitrum 完成 Uniswap V2/V3 核实。
- 待办：BSC 其余发射台（cubepeg/likwid/goplus/lunafun 等）、Base basememe/virtuals_v2/klik、ETH trench/klik/livo/stroid/printr、Solana bags 发射程序、HyperEVM 工厂，均因缺少有来源地址而 blocked，禁止猜地址。
- 待办：X10.7 看板多链过滤；X10.8 每链 2–4 周影子。
- 实盘：`ENABLE_TRADING=false`，未启用。

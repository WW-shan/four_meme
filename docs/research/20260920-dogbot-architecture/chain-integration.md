# 多链接入事实核查（2026-09-20）

原则：只写有来源的链级事实；合约地址、ABI、事件签名必须逐链从浏览器已验证源码/官方仓库核实，未核实的明确标注 UNKNOWN，不猜。

## 一、链级事实（Chainlist 官方数据，2026-09-20 抓取）

| 链 | chainId | RPC（首个） | 浏览器 | 原生币 | GMGN 支持 |
|---|---:|---|---|---|---|
| BSC | 56 | 项目配置内多个 | bscscan.com | BNB | ✅ |
| Ethereum | 1 | 项目配置内多个 | etherscan.io | ETH | ✅ |
| Base | 8453 | https://mainnet.base.org | basescan.org | ETH | ✅ |
| Arbitrum One | 42161 | 项目配置（Infura 等） | arbiscan.io | ETH | ✅ |
| Solana | — | 项目配置 | solscan.io | SOL | ✅ |
| Robinhood Chain | 4663 | https://rpc.mainnet.chain.robinhood.com | robinscan.io | ETH | ✅ |
| Robinhood Chain Testnet | 46630 | https://rpc.testnet.chain.robinhood.com/rpc | explorer.testnet.chain.robinhood.com | ETH | — |
| Arc | 5042 | https://rpc.mainnet.arc.io | explorer.arc.io | USDC | ✅ |
| Arc Testnet | 5042002 | https://rpc.testnet.arc.network | testnet.arcscan.app | USDC | — |
| Stable | 988 | https://rpc.stable.xyz | stablescan.xyz | USDT0 | ✅（当前 0 token） |
| Stable Testnet | 2201 | https://rpc.testnet.stable.xyz | testnet.stablescan.xyz | USDT0 | — |
| Hyperliquid EVM Testnet | 998 | https://api.hyperliquid-testnet.xyz/evm | — | HYPE | — |
| Monad | 143 | https://rpc.monad.xyz | monadvision.com | MON | ❌（GMGN CLI 注释掉） |
| MegaETH | 4326 | https://mainnet.megaeth.com/rpc | mega.etherscan.io | ETH | ❌（GMGN 400） |
| X Layer | 196 | https://rpc.xlayer.tech | oklink.com/xlayer | OKB | ❌（GMGN 400） |
| Sonic | 146 | https://rpc.soniclabs.com | sonicscan.org | S | ❌（GMGN 400） |

待核实：HyperEVM **主网** chainId/RPC（Chainlist 只返回测试网 998；需从 Hyperliquid 官方文档确认主网 999 与 RPC，不在此处猜）。

## 二、GMGN 平台枚举（CLI 文档，逐链）

- **Solana**：Pump.fun / pump_mayhem / pump_agent / letsbonk / bonkers / bags / memoo / liquid / bankr / zora / surge / anoncoin / moonshot_app / heaven / token_mill / believe / trendsfun / jup_studio / boop / xstocks / ray_launchpad / meteora_virtual_curve / pool_ray / pool_meteora / pool_pump_amm / pool_orca。
- **BSC**：fourmeme / fourmeme_agent / bn_fourmeme / four_xmode_agent / cubepeg / likwid / goplus_creator / goplus_skills / openfour / flap / flap_stocks / flap_aioracle / clanker / lunafun / pool_uniswap / pool_pancake。
- **Base**：clanker / bankr / flaunch / zora / zora_creator / baseapp / basememe / virtuals_v2 / klik。
- **ETH**：trench / clanker / klik / livo / stroid / pool_uniswap_v2 / pool_uniswap_v3 / printr。
- **Robinhood**（27 个）：trench / noxa / dyorswap / apestore / printr / virtuals / bankr / clanker / klik / livo / flap / flap_stocks / flap_pve / bags / bowfun / o1 / circus / arrowfinance / longxyz / motion / pons / stoxes_tax / stoxes / holoworld / pewfun / dyorfun_v3 / noxafi。
- **Arc / Stable**：无默认 allow-list，返回全部平台；**不支持 condition orders / smart_trade**，只支持普通 swap 与 limit_order。
- **Robinhood**：GMGN 文档确认支持 condition orders（TP/SL）。

## 三、每链接入所需的“最小事实集”

在写任何适配器前，必须逐链核实以下五项，缺一不写代码：

1. **工厂/launchpad 合约地址**：浏览器已验证源码（Verified）或官方仓库；事件签名与 topic0。
2. **事件布局**：字段顺序与类型（参考我们 Four.meme 的教训：v1/v2 布局不同、ABI 会漂移）。
3. **RPC 限制**：`eth_getLogs` 最大区块范围、是否支持 `eth_subscribe`、是否有归档数据。
4. **数据源覆盖**：GMGN（9 链）✅、GoPlus/DexScreener 是否覆盖该链（需逐链实测）。
5. **执行通道**：该链是否支持条件单、是否有私有交易/加速通道；Arc/Stable 只支持 limit_order。

## 四、已知且已核实的程序/合约 ID

- Solana pump.fun program：`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`（来源：chainstacklabs/pumpfun-bonkfun-bot README）。
- BSC Four.meme TokenManager：项目 `config/contracts.json`（本地已验证，配合 `config/TokenManager*.lite.abi`）。
- BSC PancakeSwap V2 factory：`0xca143ce32fe78f1f7019d7d551a6402fc5350c73`（项目配置/公开已知，接入前用 BscScan verified 源码再确认）。

## 五、UNKNOWN（必须后续逐链核实，禁止猜测）

- Flap / four_xmode_agent / cubepeg / likwid / goplus / openfour 等 BSC launchpad 的合约地址与事件 ABI。
- Clanker / Klik / Livo / Pons / Trench / Noxa / Bags 等 Robinhood/Base/ETH 平台的合约地址与事件 ABI。
- HyperEVM 主网 chainId/RPC 与 launchpad（HyperSwap、KittenSwap、LiquidLaunch）地址。
- Arc/Stable 上的 launchpad 与 DEX 地址（当前 GMGN 显示 Stable 0 token，优先级最低）。
- GoPlus / DexScreener 对 Robinhood/Arc/Stable/HyperEVM 的覆盖情况（需逐链 API 实测）。

## 六、接入顺序（按事实可得性，而不是按热度）

1. **BSC**：已有 Four.meme；下一步用 BscScan verified 源码核实 Flap、Pancake V2/V3 等，再写适配器。
2. **Robinhood**：先用 GMGN 发现（27 平台过滤）做只读；链上合约需从 robinscan.io verified 源码核实。
3. **Solana**：pump.fun program 已核实；用官方 IDL + logsSubscribe/Geyser 接。
4. **Base/ETH/Arbitrum/HyperEVM**：EVM 适配器参数化，逐链核实 Uniswap/Aerodrome/HyperSwap 工厂。
5. **Arc/Stable**：只读观察；Stable 暂缓。

## 七、来源

- Chainlist：`https://chainid.network/chains.json`（2026-09-20 抓取）。
- GMGN CLI：`docs/cli-usage.md`、`src/validate.ts`（本地 clone `/tmp/meme-gmgn-upstream-20260916`）。
- GMGN 只读探测（demo key，2026-09-20）：robinhood/arc/hyperevm/stable 有响应，monad/megaeth/xlayer/sonic 400。
- pump.fun program ID：`https://github.com/chainstacklabs/pumpfun-bonkfun-bot`。
- 项目本地：`config/contracts.json`、`config/TokenManager*.lite.abi`。

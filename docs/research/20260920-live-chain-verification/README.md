# 链与发射台实盘核实（2026-09-20）

目标：回答“每条链、每个发射台到底能不能用”，判定必须来自真实链上/真实 RPC 数据，不靠记忆或推测。
工具：`scripts/verify_live.py` + `src/radar/liveverify/`；目标清单：`config/live_targets.json`。
原始证据：[`evidence.json`](./evidence.json)；自动汇总：[`report.md`](./report.md)。

## 判定口径

| 判定 | 含义 |
|---|---|
| `VERIFIED-LIVE` | 合约/程序存在（有代码或可执行程序）**且**窗口内抓到真实事件或真实签名（附区块、交易哈希/签名） |
| `CODE-ONLY` | 地址有代码，但窗口内没有事件（低频、休眠，或地址不对） |
| `FAILED` | 探测失败（RPC 限制、限流、参数错误等），会记录原始错误 |
| `blocked` | 没有有来源的地址，明确写阻塞原因与下一步，不猜地址 |

补充规则：

- `topic0` 一律由 harness 用 keccak 从事件签名本地计算，再与链上日志比对；不硬编码“听说过的 topic”。
- ABI 优先用 Sourcify（字节码匹配的已验证源码）；本地 ABI 只用于 Four.meme 这类已有合约。
- 代理合约自动读 EIP-1967 实现槽（Flap Portal 就是这么拿到 `Portal` 实现的 259 条 ABI 的）。
- 地址来源必须写进 `source`；`config/chains.json` 里 `fact_status=verified` 的条目现在强制要求 `source`。
- 公共 RPC 的区块范围/结果数限制会自适应缩小窗口，报告里记录 `requested_span` 与 `span_blocks`，不虚报窗口。

## 链级结论（全部 9 条链实测）

| 链 | chainId | 实测最新区块 | 无过滤 getLogs | 可用窗口 | 实测端点 |
|---|---:|---:|---|---:|---|
| BSC | 56 | 123,012,852 | 不支持（要求地址过滤） | 按地址，单查询 ≤2 万条结果 | `bsc-rpc.publicnode.com` |
| Ethereum | 1 | 26,019,610 | 支持 | 2,000 区块（1rpc/mevblocker） | `1rpc.io/eth`、`rpc.mevblocker.io` |
| Base | 8453 | 51,564,356 | 支持 | 2,000 区块（`mainnet.base.org`） | `mainnet.base.org` |
| Arbitrum | 42161 | 507,148,819 | 支持 | 100,000 区块 | `arb1.arbitrum.io/rpc` |
| Robinhood | 4663 | 68,045,860 | 支持 | ≤10,000 条日志/查询；有 429 限流 | `rpc.mainnet.chain.robinhood.com` |
| Arc | 5042 | 21,860,043 | 支持 | ~30 区块（结果数上限 2,000） | `rpc.mainnet.arc.io` |
| Stable | 988 | 40,094,902 | 支持 | 1,024 区块 | `rpc.stable.xyz` |
| HyperEVM | 999 | 46,422,743 | 支持 | 响应体 1MB 上限（实测 5 区块可用） | `rpc.hyperliquid.xyz/evm` |
| Solana | — | slot 实时 | 不适用 | 50 条签名窗口 | `solana-rpc.publicnode.com` |

被网络环境挡住的端点（已实测超时，不再当作可用）：`bsc-dataseed.binance.org`、`ethereum.publicnode.com`、`api.mainnet-beta.solana.com`、`api.dexscreener.com`、`api.geckoterminal.com`、`raw.githubusercontent.com`、`robinscan.io/api/*`（返回 404）。

## 发射台结论

### BSC（5 个全部 VERIFIED-LIVE）

| 平台 | 地址 | 来源 | 实测窗口内事件 |
|---|---|---|---|
| Four.meme TokenManager | `0x5c952063c7fc8610ffdb798152d69f0b9550762b` | 本地 `config/contracts.json` + v2 ABI | 5,000 区块：`TokenCreate=612`、`TokenPurchase=167`、`TokenSale=33`（v2 布局解码成功） |
| PancakeSwap V2 | `0xca143ce32fe78f1f7019d7d551a6402fc5350c73` | Sourcify `PancakeFactory` | 5,000 区块：`PairCreated=580`，`allPairsLength≈3,071,026` |
| PancakeSwap V3 | `0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865` | 公开标签 + Sourcify（记录名为 `PlunderV3Factory`，名称待证） | 5,000 区块：`PoolCreated=2`；`feeAmountTickSpacing(100)=1`；`getPool(WBNB,USDT,100)=0x172fcd41e0913e95784454622d1c3724f546f849` |
| Flap Portal（代理） | `0xe2ce6ab80874fa9fa2aae65d277dd6b8e65c9de0`（实现 `0xab8ec926b6e113c2212af152b086ea62d1fdced9`） | docs.flap.sh Deployed Contract Addresses（v5.23.1） | 2,500 区块：18,351 条日志，含 `TokenCreated=212`、`TokenBought=1956`、`TokenSold=1597` |
| OpenFour Registry | `0x912cef0c3ae9ab6eb3ec87cab69371cfb317ab94` → Core `0xebe7b6c1089d9f72ad07f34e36d898e44e5e27f3` | OpenFour 官方文档 Known Registry addresses + 官方 ABI | Core 在 5,000 区块：`TokenCreated=57–64`（topic0 `0x7dae4dcd…` 与官方 ABI 一致） |

仍未核实（无有来源地址）：cubepeg、likwid、goplus_creator、lunafun、four_xmode_agent、flap_stocks、flap_aioracle、bn_fourmeme、fourmeme_agent。

### Ethereum（2 个 VERIFIED-LIVE，2 个 CODE-ONLY）

| 平台 | 地址 | 实测 |
|---|---|---|
| Uniswap V2 | `0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f` | 2,000 区块：`PairCreated=30` |
| Uniswap V3 | `0x1f98431c8ad98523631ae4a59f267346ea31f984` | 2,000 区块：`PoolCreated=1–2` |
| Doppler Deployer / HookInitializer | `0xb35469ee…e421` / `0xbdf93814…6544` | 有代码；2,000 区块 0 事件（部署器低频） |

未核实：trench、klik、livo、stroid、printr。

### Base（3 个 VERIFIED-LIVE，其余 CODE-ONLY）

| 平台 | 地址 | 实测 |
|---|---|---|
| Clanker v4 | `0xe85a59c628f7d27878aceb4bf3b35733630083a9` | 2,000 区块：`TokenCreated=15`（另有 `ExtensionTriggered=27`） |
| Flaunch | `0x516af52d0c629b5e378da4dc64ecb0744ce10109` | 2,000 区块：21 条日志（`Transfer`/`Approval` 铸造路径） |
| Doppler HookInitializer（Bankr 的 Base 发币通道） | `0xbdf938149ac6a781f94faa0ed45e6a0e984c6544` | 2,000 区块：`Create=11`、`Swap=953`、`ModifyLiquidity=274` |
| Uniswap V2 工厂 | `0x8909dc15e40173ff4699343b6eb8132c65e18ec6` | Sourcify `UniswapV2Factory`；2,000 区块内 `PairCreated=33` |
| Uniswap V3 / Aerodrome PoolFactory | `0x33128a8f…fdfd` / `0x420dd381…40da` | 有代码，窗口内 0–1 条 `PoolCreated`（低频），`allPoolsLength≈29,308` |
| Doppler Deployer / UniswapV4Initializer | `0xb35469ee…e421` / `0x53b4c21a…e8ad` | 有代码，窗口内 0 事件 |

未核实：basememe、klik、zora meme（`0x777777…` 是 Zora1155Factory，NFT 合约，不是 meme 发射台）。
Virtuals(Base)：第三方 bot 给出的 `bondingProxy 0xf66dea7b…`（Sourcify `TransparentUpgradeableProxy`，2,000 区块 0 事件）、
`creatorVault 0xdad68629…`（**链上无代码**）、`sellExecutor 0xf8dd39c7…`（Sourcify 实为 `Multicall3`）三个地址经实测对不上，
因此 Virtuals Base 仍按未核实处理；这正说明第三方地址必须上链复核。
Bankr 本身不是发射台：官方文档（docs.bankr.bot）说明 Base 上通过 `provider: "doppler"` 部署，所以本轮核实的是 Doppler。

### Arbitrum（1 个 VERIFIED-LIVE）

| 平台 | 地址 | 实测 |
|---|---|---|
| Uniswap V3 | `0x1f98431c8ad98523631ae4a59f267346ea31f984` | 20,000 区块：`PoolCreated=2` |
| Camelot V2 | `0x6eccab422d763ac031210895c81787e87b43a652` | 有代码（Sourcify `CamelotFactory`），20,000 区块 0 事件（疑似停用） |
| Doppler HookInitializer | `0xaa7f809bb3752f715fa2e418230667c382a56544` | 有代码，20,000 区块 0 事件 |

### Robinhood（8 个平台 VERIFIED-LIVE，本轮最大突破）

来源：`whetstoneresearch/doppler` 官方 `Deployments.md` + `0xfnzero/rbh-trade-sdk` 的 `rbhtrade/addresses.go` 地址簿；
每个地址都用 Robinhood RPC 复核代码，并在 2,000 区块窗口抓真实日志（窗口约 5–6 分钟）。

| 平台 | 地址 | 实测（2,000 区块） |
|---|---|---|
| Doppler HookInitializer | `0x4e3468951d49f2eea976ed0d6e75ffcb44a9a544` | Sourcify `DopplerHookInitializer`；`Create=5`、`Swap=589`、`ModifyLiquidity=179` |
| Pons V2 工厂 | `0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e` | Sourcify `PonsV2LaunchFactory`；`TokenLaunched=38`、`CreatorFeeRecipientUpdated=7` |
| Pons V2 一键发币买入 | `0xe33e9e479df8802cb0866d5d05258bec4cf62948` | Sourcify `PonsV2LaunchAndBuy`；`Launched=28` |
| Pons V2 Hook | `0xe5e702641ea86f4ae6cc3cdaed2b886f976be044` | Sourcify `PonsV2MemeHook`；`HookFeeCollected=1355`、`PoolFeesSwept=220` |
| Long Airlock / Rehype Hook | `0xeb7c0347…0862` / `0x6f02324d…0f77` | 6 条 / 3 条事件（有代码，低频） |
| o1 LaunchHook / FeeEscrow | `0x0310cfeb…2acc` / `0xc5444b41…e5ef` | Sourcify `LaunchHook` / `FeeEscrow`；`Trade=6`、`Credited=12` |
| Flap Controller（代理） | `0x26605f322f7ff986f381bb9a6e3f5dab0beaeb09` | Sourcify `TransparentUpgradeableProxy`；130 条事件 |
| GMGN Router | `0x65050a9b7e5075a2ba5ced7b1b64ee66262c40dc` | 3,124 条事件（执行路由，不是发射台） |

有代码但窗口内 0 事件（地址已核实、活动待观察）：Doppler Deployer/UniswapV4Initializer、o1 Factory（Sourcify `RWAERC20LaunchpadFactory`）、Bags Factory/Hook/Vault（`BagsV4Hook`）、LetsCash Factory/Hook（`CashCatHookV2`）、Pools Entry/TokenFactory（`LiquidityLauncher`）、PAIR Launchpad、Varo Launchpad、Virtuals Launchpad/Factory。
链上发现（20 区块）另见 `PoolManager`（Uniswap V4，Sourcify 已验证）、`RelayRouterV3`、`Permit2`、`WETH/USDG` 代理。
仍未核实：trench、noxa、dyorswap、apestore、printr、bankr（Robinhood 侧）、clanker、klik、livo、bowfun、circus、arrowfinance、longxyz、motion、stoxes、holoworld、pewfun、dyorfun_v3、noxafi —— `robinscan.io` 的 Blockscout API 路径全部 404，缺有来源地址。

### Arc / Stable / HyperEVM（无已核实发射台）

- Arc：20 区块 943 条日志；`PoolManager 0x8366a39c…e40951`（Sourcify 已验证，Uniswap V4 Swap topic0 `0x40e9cecb…`）是 DEX 池管理器，不是发射台。
- Stable：500 区块 377 条日志；`PumperTokenStandalone 0xd86800d5…bf82`（Sourcify）是代币实现，发射台未核实；GMGN 显示 0 token。
- HyperEVM：5 区块 203 条日志；`WHYPE9 0x5555…5555`（Sourcify 已验证）等，工厂地址未找到官方来源。
- 结论：三条链只做只读观察，配置里不写未核实地址。

### Solana（4 个 VERIFIED-LIVE，1 个休眠）

| 平台 | Program ID | 实测 |
|---|---|---|
| Pump.fun | `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` | executable，50 条签名/10 秒；指令日志含 `Buy`/`SellV2`/`Swap`/`GetFeesWithQuoteMint` |
| letsbonk（Raydium LaunchLab） | `LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj` | executable，50 条签名/10 秒 |
| Boop | `boop8hVGQGqehUK2iVEMEnMrL5RbjywRzHKBmBE7ry4` | executable，12 条签名 |
| Moonshot | `MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG` | executable，50 条签名 |
| Virtuals（Solana） | `5U3EU2ubXtK84QcRjWVmYt9RaDyA8gKxdUrPFXmZyaki` | 程序存在，窗口内 0 条签名（休眠，不投入） |

未核实：Bags 的发射程序（只找到 Fee Share V2 `FEE2tBhCKAt7shrod19QttSVREUYPiyMzoku1mL1gqVK`，不是发射程序）。
注：pump.fun 的 `create` 指令没有出现在最近 60 笔采样里（采样窗口内主要是买卖），这是采样事实，不是“没有创建”。

## 本轮纠正的既有错误

1. `config/chains.json` 原来把 pancake_v3 标为 `verified` 却没有来源；本轮补上来源，并记录 Sourcify 名称不一致（`PlunderV3Factory`）这一事实。地址本身通过 `feeAmountTickSpacing`/`getPool` 与真实池子验证可用。
2. Robinhood 之前因为浏览器 API 404 被判“无法核实”；本轮换路径（官方 Doppler 部署表 + 第三方地址簿 + 链上日志）核实了 8 个平台，其中 Pons V2 实测 `TokenLaunched=38`、`Launched=28`。
3. Bankr 被当成 Base 发射台；官方文档显示它是 Doppler 的客户端，核实对象应是 Doppler 合约。
4. 第三方地址簿不能直接信：Robinhood 的 `rbh-trade-sdk` 地址簿经链上复核后 8 个平台有实时事件才采用；Base 的 Virtuals 第三方地址 3 个里 2 个明显错误（一个是 `Multicall3`，一个无代码），全部不写入配置。
5. 公共 RPC 的限制被写进工具：BSC 必须带地址过滤且单查询 ≤2 万条结果；Arc ~30 区块；Stable 1,024 区块；Robinhood ≤1 万条日志并有 429 限流（已加退避重试）。

## 复现方式

```bash
# 全部 9 条链（含链上发现）
python3 scripts/verify_live.py --all

# 单链 / 单目标
python3 scripts/verify_live.py --chain bsc
python3 scripts/verify_live.py --chain robinhood --discovery-only

# 只看发现结果，不写正式证据文件
python3 scripts/verify_live.py --chain arc --out-dir /tmp/liveverify-arc
```

输出：`evidence.json`（逐目标原始数据、尝试记录、样本区块/交易哈希）、`report.md`（表格汇总）。
实盘状态不变：`ENABLE_TRADING=false`，本轮没有启用任何交易。

---

# 第二轮补齐（2026-09-21）

第一轮之后仍有 38 个 GMGN 平台“看不到”。本轮改为“先网络检索（anysearch/Tavily）→ 官方文档 → 上链核实”，新增来源与结果如下。
最新全量跑：**52 个目标 VERIFIED-LIVE，69 个 code-only/失败，共 121 个目标**（`evidence.json` / `report.md` 已重生成）。

## 新拿到的官方来源（节选）

| 平台/链 | 来源 | 结果 |
|---|---|---|
| Four.meme V1/Helper3/AgentIdentifier（BSC） | four-meme-community/fourmeme-docs + four-meme-ai | `TOKEN_MANAGER()=0xEC4549ca…`、`TOKEN_MANAGER_2()=0x5c952063…` 链上读取成功；`AgentIdentifier.nftCount()=0` |
| Flap AI Oracle / Stocks Vault / VaultPortal / Trigger / Candy Box（BSC） | docs.flap.sh 官方部署表 | AI Oracle、Trigger Service（窗口内 6700 条事件）、VaultPortal 已 live；Stocks 三个工厂与 Candy Box 代码存在、窗口内无事件 |
| Clanker（BSC + ETH） | clanker.gitbook.io 官方部署表 | BSC `0xea30438E…` 与 ETH `0x6C859977…` 均 Sourcify 命中 `Clanker`，`owner()/TOKEN_SUPPLY()` 读取成功 |
| Klik（ETH + Base + Robinhood） | klik.finance/docs + Mobula | ETH 工厂 live（`ERC20TokenCreated`、`TokenPurchased`）；Base/Robinhood 工厂地址与 Sourcify `Factory`/`UniversalKlikHook` 一致 |
| Robinhood 全套发射台 | docs.mobula.io/almanac/robinhood-launchpads | apestore / bottom.fun / bow.fun / dyor.fun / LaunchProof / noxa.fun / Pons v1+legacy / printr / realfun / robinfun 的工厂地址全部拿到，Sourcify 名称逐一对应 |
| Pons V2 Locker | docs.bitquery.io/docs/blockchain/robinhood | `PonsV2LaunchLocker 0x267444D0…`，窗口内 `FeesClaimed` 事件 |
| Zora Factory（Base） | docs.zora.co/coins/contracts/creating-a-coin | `0x777777751622c0d3…` live（窗口内 40 条事件） |
| Uniswap Liquidity Launchpad（ETH/Base/Robinhood/Arc） | developers.uniswap.org 官方部署表 | `LiquidityLauncher 0x0000FffF…` 用 `permit2()` 读取成功（read-verified），LBPStrategy v3.3.0 各链地址 |
| HyperSwap V3（HyperEVM） | docs.hyperswap.exchange | 工厂 `0xB1c0fa0B…`：`feeAmountTickSpacing(3000)=60`、`owner()` 读取成功 |
| Bags（Solana） | docs.bags.fm/principles/program-ids | 官方程序：Fee Share V1/V2、**Meteora DBC（代币创建/联合曲线）**、Meteora DAMM v2，全部可执行且有实时签名 |
| Raydium / PumpSwap / Meteora / Orca（Solana 池子） | docs.raydium.io、docs.meteora.ag、docs.orca.so、solanatracker | AMM v4 / CPMM / CLMM / PumpSwap / DLMM / Whirlpool 六个程序全部 live |
| Virtuals（Base/Robinhood/Solana） | whitepaper.virtuals.io 官方合约页 | 确认 creator vault / sell wall / sell order 与 VIRTUAL 代币地址；Base 联合曲线地址官方页留空，仍标 unverified |
| Four.meme 合作方（lunafun / goplus_skills） | 官方 Helper3 `getTokenInfo` | LUNA.FUN 与 SafuSkill 代币实测 `version=2`、`tokenManager=0x5c952063…`（TokenManager2）→ 由 four.meme 通道覆盖 |

## 仍然看不到的（无公开合约来源）

BSC：`cubepeg`（候选 `0x60a2dfa7…` 只命中一个泛用 `Launchpad` 合约，0 事件，不采用）、`goplus_creator`（GoPlus 侧没有公开合约；SafuSkill 已证明走 four.meme）。
Base：`basememe`、`baseapp`、`virtuals_v2`（官方页联合曲线地址为空）。
ETH：`trench`、`livo`、`stroid`（官方站点无合约页）。
Robinhood：`trench`、`virtuals`（联合曲线未公开）、`livo`、`motion`、`stoxes`、`holoworld`、`pewfun`、`dyorswap`、`circus`、`arrowfinance`、`clanker`（官方部署表无 Robinhood）。
Solana：`bonkers`（没有可核实的官方程序 ID）。
Stable：GMGN 0 token，链上只发现 `PumperTokenStandalone` 代币实现，没有发射台工厂。

这些条目继续保持 blocked，不写猜测地址；一旦出现官方合约页或 GMGN API key，可以按同一套 harness 直接补测。

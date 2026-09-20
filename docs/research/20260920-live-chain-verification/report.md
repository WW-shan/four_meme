# 链与发射台实盘核实报告

- 生成时间：2026-09-20 23:31:24 +0800
- 链数量：9
- 判定口径：`VERIFIED-LIVE` = 合约有代码 + 真实链上事件（含区块/交易哈希）；`CODE-ONLY` = 有代码但窗口内无事件；`FAILED` = 探测失败。

## 链级连通性

| 链 | chainId | 期望 | 最新区块 | 区块时间 | 日志模式 | 可用跨度 | 端点 |
|---|---:|---:|---:|---:|---|---:|---|
| bsc | 56 | 56 | 123013660 | 1789918286 (1.0s 前) | requires_address_filter | None | https://bsc-rpc.publicnode.com |
| eth | 1 | 1 | 26019641 | 1789918307 (19.9s 前) | unfiltered | 20 | https://1rpc.io/eth |
| base | 8453 | 8453 | 51564566 | 1789918479 (1.7s 前) | unfiltered | 20 | https://mainnet.base.org |
| arbitrum | 42161 | 42161 | 507150503 | 1789918517 (1.7s 前) | unfiltered | 20 | https://arb1.arbitrum.io/rpc |
| robinhood | 4663 | 4663 | 68050099 | 1789918525 (1.9s 前) | unfiltered | 20 | https://rpc.mainnet.chain.robinhood.com |
| arc | 5042 | 5042 | 21860881 | 1789918551 (1.1s 前) | unfiltered | 20 | https://rpc.mainnet.arc.io |
| stable | 988 | 988 | 40095504 | 1789918568 (1.4s 前) | unfiltered | 100 | https://rpc.stable.xyz |
| hyperevm | 999 | 999 | 46423181 | 1789918588 (0.9s 前) | unfiltered | 20 | https://rpc.hyperliquid.xyz/evm |
| sol | None | None | None | - (-s 前) | - | - | - |

## 合约级核实

| 链 | 目标 | 地址 | 代码字节 | Sourcify 名称 | 窗口事件数 | 期望 topic 命中 | 判定 |
|---|---|---|---:|---|---:|---|---|
| bsc | fourmeme_token_manager | 0x5c952063c7fc8610ffdb798152d69f0b9550762b | 170 | ERC1967Proxy | 815 | TokenCreate=602, TokenPurchase=175, TokenSale=38, LiquidityAdded=0 | VERIFIED-LIVE |
| bsc | pancake_v2_factory | 0xca143ce32fe78f1f7019d7d551a6402fc5350c73 | 19084 | PancakeFactory | 571 | PairCreated=571 | VERIFIED-LIVE |
| bsc | pancake_v3_factory | 0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865 | 5151 | PlunderV3Factory | 3 | PoolCreated=3 | VERIFIED-LIVE |
| bsc | flap_portal | 0xe2ce6ab80874fa9fa2aae65d277dd6b8e65c9de0 | 2882 | TransparentUpgradeableProxy | 11251 | - | VERIFIED-LIVE |
| bsc | openfour_registry | 0x912cef0c3ae9ab6eb3ec87cab69371cfb317ab94 | 209 | - | 0 | - | VERIFIED-LIVE |
| eth | uniswap_v2_factory | 0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f | 13859 | UniswapV2Factory | 29 | PairCreated=29 | VERIFIED-LIVE |
| eth | uniswap_v3_factory | 0x1f98431c8ad98523631ae4a59f267346ea31f984 | 24535 | UniswapV3Factory | 1 | PoolCreated=1 | VERIFIED-LIVE |
| eth | doppler_deployer | 0xb35469ee64a87afd19b31615094fe3962d73e421 | 24087 | - | 0 | - | CODE-ONLY |
| eth | doppler_hook_initializer | 0xbdf938149ac6a781f94faa0ed45e6a0e984c6544 | 24384 | - | 0 | - | CODE-ONLY |
| base | uniswap_v3_factory | 0x33128a8fc17869897dce68ed026d694621f6fdfd | 24535 | UniswapV3Factory | 0 | PoolCreated=0 | CODE-ONLY |
| base | aerodrome_pool_factory | 0x420dd381b31aef6683db6b902084cb0ffece40da | 3516 | PoolFactory | 0 | PoolCreated=0 | CODE-ONLY |
| base | clanker_v4 | 0xe85a59c628f7d27878aceb4bf3b35733630083a9 | 12375 | Clanker | 47 | - | VERIFIED-LIVE |
| base | flaunch | 0x516af52d0c629b5e378da4dc64ecb0744ce10109 | 9760 | Flaunch | 24 | - | VERIFIED-LIVE |
| base | doppler_deployer | 0xb35469ee64a87afd19b31615094fe3962d73e421 | 24087 | DopplerDeployer | 0 | - | CODE-ONLY |
| base | doppler_hook_initializer | 0xbdf938149ac6a781f94faa0ed45e6a0e984c6544 | 24384 | DopplerHookInitializer | 1355 | - | VERIFIED-LIVE |
| base | doppler_uniswap_v4_initializer | 0x53b4c21a6cb61d64f636abbfa6e8e90e6558e8ad | 2698 | UniswapV4Initializer | 0 | - | CODE-ONLY |
| arbitrum | uniswap_v3_factory | 0x1f98431c8ad98523631ae4a59f267346ea31f984 | 24535 | UniswapV3Factory | 2 | PoolCreated=2 | VERIFIED-LIVE |
| arbitrum | camelot_factory | 0x6eccab422d763ac031210895c81787e87b43a652 | 23646 | CamelotFactory | 0 | - | CODE-ONLY |
| arbitrum | doppler_deployer | 0x4389ad34938b14f25cff7ed983c53f5a42a2573f | 24063 | - | 0 | - | CODE-ONLY |
| arbitrum | doppler_hook_initializer | 0xaa7f809bb3752f715fa2e418230667c382a56544 | 24039 | DopplerHookInitializer | 0 | - | CODE-ONLY |
| robinhood | doppler_deployer | 0x4389ad34938b14f25cff7ed983c53f5a42a2573f | 24063 | - | 0 | - | CODE-ONLY |
| robinhood | doppler_hook_initializer | 0x4e3468951d49f2eea976ed0d6e75ffcb44a9a544 | 25533 | DopplerHookInitializer | 713 | - | VERIFIED-LIVE |
| robinhood | doppler_uniswap_v4_initializer | 0x6cce158b6d1747617fc218592b4d60b239b957ea | 2685 | - | 0 | - | CODE-ONLY |
| sol | pump_fun_program | 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P | - | - | - | - | VERIFIED-LIVE |
| sol | letsbonk_program | LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj | - | - | - | - | VERIFIED-LIVE |
| sol | boop_program | boop8hVGQGqehUK2iVEMEnMrL5RbjywRzHKBmBE7ry4 | - | - | - | - | VERIFIED-LIVE |
| sol | moonshot_program | MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG | - | - | - | - | VERIFIED-LIVE |
| sol | virtuals_solana_program | 5U3EU2ubXtK84QcRjWVmYt9RaDyA8gKxdUrPFXmZyaki | - | - | - | - | FAILED |

## 未核实（明确阻塞，不猜地址）

| 链 | 平台 | 阻塞原因 | 下一步 |
|---|---|---|---|
| bsc | cubepeg | 缺少有来源的工厂地址 | 同上 |
| bsc | likwid | 缺少有来源的工厂地址 | 同上 |
| bsc | goplus_creator | 缺少有来源的工厂地址 | 同上 |
| bsc | lunafun | 缺少有来源的工厂地址 | 同上 |
| bsc | four_xmode_agent | 缺少有来源的工厂地址 | 同上 |
| eth | trench | 缺少有来源的工厂地址 | 从官方渠道核实合约后加入 |
| eth | klik | 缺少有来源的工厂地址 | 同上 |
| eth | livo | 缺少有来源的工厂地址 | 同上 |
| eth | stroid | 缺少有来源的工厂地址 | 同上 |
| eth | printr | 缺少有来源的工厂地址 | 同上 |
| base | zora | 0x777777... 为 Zora1155Factory（NFT），meme 发射台合约未核实 | 从 Zora 官方文档核实 meme 发射合约 |
| base | basememe | 缺少有来源的工厂地址 | 同上 |
| base | virtuals_v2 | 缺少有来源的工厂地址 | 同上 |
| base | klik | 缺少有来源的工厂地址 | 同上 |
| base | bankr | Bankr 本身不发币；官方文档说明 Base 上通过 Doppler provider 部署（docs.bankr.bot） | 已改为核实 Doppler 合约；Bankr 前端/CLI 只是客户端 |
| robinhood | gmgn_robinhood_discovery | GMGN openapi 需要个人 API key（本地未配置） | 用户提供 GMGN_API_KEY 后可只读验证 27 个平台 |
| robinhood | doppler (Robinhood) | 已找到官方部署表并列入 targets；本轮验证结果见 report | 若窗口内无事件，需扩大窗口或换 RPC |
| arc | arc_launchpads | GMGN 只有通用 swap/limit order；无默认平台清单，未发现已核实的发射台地址 | 用链上日志发现活跃合约后核实 |
| stable | stable_launchpads | getLogs 上限 1024 区块；GMGN 探测 0 token | 只读观察，不投入开发 |
| hyperevm | hyperevm_dex | 主网 RPC 对无过滤 getLogs 有响应体 1MB 限制；工厂地址未核实 | 先按地址核实 HyperSwap/KittenSwap 工厂，再做日志探测 |
| sol | bags | 程序 ID 未核实 | 同上 |

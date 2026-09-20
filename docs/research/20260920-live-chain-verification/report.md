# 链与发射台实盘核实报告

- 生成时间：2026-09-20 23:51:02 +0800
- 链数量：9
- 判定口径：`VERIFIED-LIVE` = 合约有代码 + 真实链上事件（含区块/交易哈希）；`CODE-ONLY` = 有代码但窗口内无事件；`FAILED` = 探测失败。

## 链级连通性

| 链 | chainId | 期望 | 最新区块 | 区块时间 | 日志模式 | 可用跨度 | 端点 |
|---|---:|---:|---:|---:|---|---:|---|
| bsc | 56 | 56 | 123016274 | 1789919463 (0.8s 前) | requires_address_filter | None | https://bsc-rpc.publicnode.com |
| eth | 1 | 1 | 26019739 | 1789919495 (4.8s 前) | unfiltered | 20 | https://rpc.mevblocker.io |
| base | 8453 | 8453 | 51565112 | 1789919571 (1.5s 前) | unfiltered | 20 | https://mainnet.base.org |
| arbitrum | 42161 | 42161 | 507154861 | 1789919610 (2.3s 前) | unfiltered | 20 | https://arb1.arbitrum.io/rpc |
| robinhood | 4663 | 4663 | 68061004 | 1789919618 (2.0s 前) | unfiltered | 20 | https://rpc.mainnet.chain.robinhood.com |
| arc | 5042 | 5042 | 21863089 | 1789919670 (1.9s 前) | unfiltered | 20 | https://rpc.mainnet.arc.io |
| stable | 988 | 988 | 40097095 | 1789919689 (2.4s 前) | unfiltered | 100 | https://rpc.stable.xyz |
| hyperevm | 999 | 999 | 46424318 | 1789919706 (0.8s 前) | unfiltered | 20 | https://rpc.hyperliquid.xyz/evm |
| sol | None | None | None | - (-s 前) | - | - | - |

## 合约级核实

| 链 | 目标 | 地址 | 代码字节 | Sourcify 名称 | 窗口事件数 | 期望 topic 命中 | 判定 |
|---|---|---|---:|---|---:|---|---|
| bsc | fourmeme_token_manager | 0x5c952063c7fc8610ffdb798152d69f0b9550762b | 170 | ERC1967Proxy | 893 | TokenCreate=563, TokenPurchase=241, TokenSale=89, LiquidityAdded=0 | VERIFIED-LIVE |
| bsc | pancake_v2_factory | 0xca143ce32fe78f1f7019d7d551a6402fc5350c73 | 19084 | PancakeFactory | 428 | PairCreated=428 | VERIFIED-LIVE |
| bsc | pancake_v3_factory | 0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865 | 5151 | PlunderV3Factory | 4 | PoolCreated=4 | VERIFIED-LIVE |
| bsc | flap_portal | 0xe2ce6ab80874fa9fa2aae65d277dd6b8e65c9de0 | 2882 | TransparentUpgradeableProxy | 15792 | - | VERIFIED-LIVE |
| bsc | openfour_registry | 0x912cef0c3ae9ab6eb3ec87cab69371cfb317ab94 | 209 | - | 0 | - | VERIFIED-LIVE |
| eth | uniswap_v2_factory | 0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f | 13859 | UniswapV2Factory | 30 | PairCreated=30 | VERIFIED-LIVE |
| eth | uniswap_v3_factory | 0x1f98431c8ad98523631ae4a59f267346ea31f984 | 24535 | UniswapV3Factory | 2 | PoolCreated=2 | VERIFIED-LIVE |
| eth | doppler_deployer | 0xb35469ee64a87afd19b31615094fe3962d73e421 | 24087 | - | 0 | - | CODE-ONLY |
| eth | doppler_hook_initializer | 0xbdf938149ac6a781f94faa0ed45e6a0e984c6544 | 24384 | - | 0 | - | CODE-ONLY |
| base | uniswap_v3_factory | 0x33128a8fc17869897dce68ed026d694621f6fdfd | 24535 | UniswapV3Factory | 1 | PoolCreated=1 | VERIFIED-LIVE |
| base | aerodrome_pool_factory | 0x420dd381b31aef6683db6b902084cb0ffece40da | 3516 | PoolFactory | 0 | PoolCreated=0 | CODE-ONLY |
| base | clanker_v4 | 0xe85a59c628f7d27878aceb4bf3b35733630083a9 | 12375 | Clanker | 51 | - | VERIFIED-LIVE |
| base | flaunch | 0x516af52d0c629b5e378da4dc64ecb0744ce10109 | 9760 | Flaunch | 24 | - | VERIFIED-LIVE |
| base | doppler_deployer | 0xb35469ee64a87afd19b31615094fe3962d73e421 | 24087 | DopplerDeployer | 0 | - | CODE-ONLY |
| base | doppler_hook_initializer | 0xbdf938149ac6a781f94faa0ed45e6a0e984c6544 | 24384 | DopplerHookInitializer | 1336 | - | VERIFIED-LIVE |
| base | doppler_uniswap_v4_initializer | 0x53b4c21a6cb61d64f636abbfa6e8e90e6558e8ad | 2698 | UniswapV4Initializer | 0 | - | CODE-ONLY |
| base | virtuals_bonding_proxy | 0xf66dea7b3e897cd44a5a231c61b6b4423d613259 | 1159 | TransparentUpgradeableProxy | 0 | - | CODE-ONLY |
| base | virtuals_sell_executor | 0xf8dd39c71a278fe9f4377d009d7627ef140f809e | 7503 | Multicall3 | 0 | - | CODE-ONLY |
| base | virtuals_creator_vault | 0xdad686299fb562f89e55da05f1d96fabeb2a2e32 | 0 | - | - | - | FAILED |
| base | uniswap_v2_factory_base | 0x8909dc15e40173ff4699343b6eb8132c65e18ec6 | 13859 | UniswapV2Factory | 33 | - | VERIFIED-LIVE |
| arbitrum | uniswap_v3_factory | 0x1f98431c8ad98523631ae4a59f267346ea31f984 | 24535 | UniswapV3Factory | 0 | PoolCreated=0 | CODE-ONLY |
| arbitrum | camelot_factory | 0x6eccab422d763ac031210895c81787e87b43a652 | 23646 | CamelotFactory | 0 | - | CODE-ONLY |
| arbitrum | doppler_deployer | 0x4389ad34938b14f25cff7ed983c53f5a42a2573f | 24063 | - | 0 | - | CODE-ONLY |
| arbitrum | doppler_hook_initializer | 0xaa7f809bb3752f715fa2e418230667c382a56544 | 24039 | DopplerHookInitializer | 0 | - | CODE-ONLY |
| robinhood | doppler_deployer | 0x4389ad34938b14f25cff7ed983c53f5a42a2573f | 24063 | - | 0 | - | CODE-ONLY |
| robinhood | doppler_hook_initializer | 0x4e3468951d49f2eea976ed0d6e75ffcb44a9a544 | 25533 | DopplerHookInitializer | 885 | - | VERIFIED-LIVE |
| robinhood | doppler_uniswap_v4_initializer | 0x6cce158b6d1747617fc218592b4d60b239b957ea | 2685 | - | 0 | - | CODE-ONLY |
| robinhood | pons_factory | 0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e | 24177 | PonsV2LaunchFactory | 24 | - | VERIFIED-LIVE |
| robinhood | pons_launch_and_buy | 0xe33e9e479df8802cb0866d5d05258bec4cf62948 | 4416 | PonsV2LaunchAndBuy | 17 | - | VERIFIED-LIVE |
| robinhood | pons_meme_hook | 0xe5e702641ea86f4ae6cc3cdaed2b886f976be044 | 15167 | PonsV2MemeHook | 1145 | - | VERIFIED-LIVE |
| robinhood | long_airlock | 0xeb7c034704ef8dcd2d32324c1545f62fb4ad0862 | 5695 | - | 8 | - | VERIFIED-LIVE |
| robinhood | long_rehype_hook | 0x6f02324d20cc679d0e585290caa6b16bacbc0f77 | 14564 | - | 11 | - | VERIFIED-LIVE |
| robinhood | o1_factory | 0xce9c48cfa068947f77738c81be406b53338e5b0d | 24466 | RWAERC20LaunchpadFactory | 0 | - | CODE-ONLY |
| robinhood | o1_hook | 0x0310cfebe1d7a69f2414f6595bbe9d17c5342acc | 15732 | LaunchHook | 6 | - | VERIFIED-LIVE |
| robinhood | o1_fee_escrow | 0xc5444b417a04a7e1b9c1e327c7d499803c14e5ef | 1873 | FeeEscrow | 4 | - | VERIFIED-LIVE |
| robinhood | bags_factory | 0xe8cc4431adf8b5a847c113ef0c6af9043219cb37 | 130 | ERC1967Proxy | 0 | - | CODE-ONLY |
| robinhood | bags_hook | 0x2380abf72c17aabab76480244759ac7e2932eecc | 6628 | BagsV4Hook | 0 | - | CODE-ONLY |
| robinhood | bags_vault | 0x4861446aa7ffd9e67a83cbbacb1a4b70540b83aa | 130 | ERC1967Proxy | 0 | - | CODE-ONLY |
| robinhood | letscash_factory | 0x5bd1fbe78a78fe8236fa00cf48fbeba74ae34661 | 176 | ERC1967Proxy | 0 | - | CODE-ONLY |
| robinhood | letscash_hook | 0x75a54357d9c78a2db19004a5fdc76c50f9242aec | 13927 | CashCatHookV2 | 4 | - | VERIFIED-LIVE |
| robinhood | pools_entry | 0x0000ffffbe8efe702c8703ae3477ff5de3d319c0 | 4127 | LiquidityLauncher | 6 | - | VERIFIED-LIVE |
| robinhood | pools_token_factory | 0x000000e200088d55c39a11f609e5f667729ad49b | 13380 | - | 2 | - | VERIFIED-LIVE |
| robinhood | pair_launchpad | 0x8660a7f019c7943b0b0a91b8e39aff3b6db6ae62 | 145 | PairERC1967Proxy | 0 | - | CODE-ONLY |
| robinhood | flap_controller | 0x26605f322f7ff986f381bb9a6e3f5dab0beaeb09 | 2840 | TransparentUpgradeableProxy | 98 | - | VERIFIED-LIVE |
| robinhood | varo_launchpad | 0x851153fe84239c2dc55fa191ac2f099e20a6d0b8 | 141 | UUPSProxy | 0 | - | CODE-ONLY |
| robinhood | virtuals_launchpad | 0xd4ccbfa37e2f35611b3042e4096ad7a3459bd007 | 1167 | - | 0 | - | CODE-ONLY |
| robinhood | virtuals_factory | 0xfc2e4da3edb2e18100473339c763705d263d20a9 | 1167 | - | 0 | - | CODE-ONLY |
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

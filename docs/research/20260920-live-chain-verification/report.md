# 链与发射台实盘核实报告

- 生成时间：2026-09-21 05:11:25 +0800
- 链数量：9
- 判定口径：`VERIFIED-LIVE` = 合约有代码 + 窗口内有真实链上事件（含区块/交易哈希）；`HISTORICAL` = 有代码且 from-genesis 证明历史上有真实事件，但最近窗口内没有；`CODE-ONLY` = 有代码但从未观察到事件；`FAILED` = 探测失败。

## 链级连通性

| 链 | chainId | 期望 | 最新区块 | 区块时间 | 日志模式 | 可用跨度 | 端点 |
|---|---:|---:|---:|---:|---|---:|---|
| bsc | 56 | 56 | 123058987 | 1789938686 (1.4s 前) | requires_address_filter | None | https://bsc-rpc.publicnode.com |
| eth | 1 | 1 | 26021336 | 1789938743 (4.1s 前) | unfiltered | 5 | https://ethereum-rpc.publicnode.com |
| base | 8453 | 8453 | 51574733 | 1789938813 (1.3s 前) | unfiltered | 20 | https://mainnet.base.org |
| arbitrum | 42161 | 42161 | 507230167 | 1789938853 (1.9s 前) | unfiltered | 20 | https://arb1.arbitrum.io/rpc |
| robinhood | 4663 | 4663 | 68252872 | 1789938869 (1.4s 前) | unfiltered | 20 | https://rpc.mainnet.chain.robinhood.com |
| arc | 5042 | 5042 | 21901272 | 1789939040 (1.3s 前) | unfiltered | 20 | https://rpc.mainnet.arc.io |
| stable | 988 | 988 | 40124817 | 1789939060 (2.3s 前) | unfiltered | 100 | https://rpc.stable.xyz |
| hyperevm | 999 | 999 | 46444011 | 1789939077 (1.3s 前) | unfiltered | 20 | https://rpc.hyperliquid.xyz/evm |
| sol | None | None | None | - (-s 前) | - | - | - |

## 合约级核实

| 链 | 目标 | 地址 | 代码字节 | Sourcify 名称 | 窗口事件数 | 最近事件(区块/距今) | 期望 topic 命中 | 判定 |
|---|---|---|---:|---|---:|---|---|---|
| bsc | fourmeme_token_manager | 0x5c952063c7fc8610ffdb798152d69f0b9550762b | 170 | ERC1967Proxy | 872 | 123053988 / 38 分钟前 | TokenCreate=634, TokenPurchase=164, TokenSale=74, LiquidityAdded=0 | VERIFIED-LIVE |
| bsc | pancake_v2_factory | 0xca143ce32fe78f1f7019d7d551a6402fc5350c73 | 19084 | PancakeFactory | 195 | 123054033 / 37 分钟前 | PairCreated=195 | VERIFIED-LIVE |
| bsc | pancake_v3_factory | 0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865 | 5151 | PlunderV3Factory | 2 | 123058674 / 3 分钟前 | PoolCreated=2 | VERIFIED-LIVE |
| bsc | flap_portal | 0xe2ce6ab80874fa9fa2aae65d277dd6b8e65c9de0 | 2882 | TransparentUpgradeableProxy | 8354 | 123053987 / 38 分钟前 | - | VERIFIED-LIVE |
| bsc | openfour_registry | 0x912cef0c3ae9ab6eb3ec87cab69371cfb317ab94 | 209 | - | 0 | - | - | VERIFIED-LIVE |
| bsc | fourmeme_token_manager_v1 | 0xec4549cadce5da21df6e6422d448034b5233bfbc | 170 | - | 0 | - | - | CODE-ONLY |
| bsc | fourmeme_helper3 | 0xf251f83e40a78868fcfa3fa4599dad6494e46034 | 1101 | ERC1967Proxy | 0 | - | - | VERIFIED-LIVE |
| bsc | fourmeme_agent_identifier | 0x09b44a633de9f9ebf6fb9bdd5b5629d3dd2cef13 | 1101 | ERC1967Proxy | 0 | - | - | VERIFIED-LIVE |
| bsc | flap_vault_portal | 0x90497450f2a706f1951b5bdda52b4e5d16f34c06 | 2882 | TransparentUpgradeableProxy | 11 | 123054552 / 34 分钟前 | - | VERIFIED-LIVE |
| bsc | likwid_vault | 0x065d449ec9d139740343990b7e1cf05fa830e4ba | 23271 | - | 0 | - | - | CODE-ONLY |
| bsc | likwid_pair_position | 0xb397fe16be79b082f17f1cd96e6489df19e07bcd | 10360 | - | 0 | - | - | CODE-ONLY |
| bsc | likwid_margin_position | 0x6bec0c1dc4898484b7f094566ddf8bc82ed7abe8 | 24483 | - | 0 | - | - | CODE-ONLY |
| bsc | likwid_lend_position | 0xce91db5947228bba595c3cac49eb24053a06618e | 14239 | - | 0 | - | - | CODE-ONLY |
| bsc | likwid_helper | 0x16a9633f8a777ca733073ea2526705cd8338d510 | 20150 | - | 0 | - | - | CODE-ONLY |
| bsc | flap_aioracle | 0xaee3a7ca6fe6b53f6c32a3e8407ec5a9df8b7e39 | 2837 | - | 0 | - | - | CODE-ONLY |
| bsc | flap_stocks_vault_factory_v1 | 0xf8ac088f06d155f3c3f531f1ef80b14f1604530a | 11564 | - | 0 | - | - | CODE-ONLY |
| bsc | flap_stocks_vault_factory_v2 | 0x40a9a2fda017e0923ea0b403f2f063f9e51168fb | 14921 | - | 0 | - | - | CODE-ONLY |
| bsc | flap_stocks_vault_factory_v3 | 0x5418f7e8ff90354db0ecd48c8b710219244eb3c5 | 15810 | - | 0 | - | - | CODE-ONLY |
| bsc | flap_trigger_service | 0xcf4ee25035cf883895110f367f5ba8172416a7f9 | 2837 | - | 6699 | 123054002 / 38 分钟前 | - | VERIFIED-LIVE |
| bsc | flap_candy_box | 0x6255fbd731272a517022e99f6cacf6a5de9414ee | 2840 | - | 0 | - | - | CODE-ONLY |
| bsc | flap_tax_token_helper | 0x53841c73217735f37bc1775538b03b23fefd8346 | 2837 | TransparentUpgradeableProxy | 0 | - | - | CODE-ONLY |
| bsc | cubepeg_launchpad_candidate | 0x60a2dfa786c3ec6bd1a733c8fab910a6481f0b50 | 23658 | Launchpad | 0 | - | - | CODE-ONLY |
| bsc | clanker_v4_bsc | 0xea30438e0b5f99096cb05a8da63be55a6a298f6a | 12070 | Clanker | 0 | - | - | VERIFIED-LIVE |
| bsc | coinbarrel_launcher_bsc | 0x7e086b1fff3db54ab26ca0a8304dd83577f958e8 | 163 | ERC1967Proxy | 0 | - | - | CODE-ONLY |
| eth | uniswap_v2_factory | 0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f | 13859 | UniswapV2Factory | 51 | 26019367 / 6.6 小时前 | PairCreated=51 | VERIFIED-LIVE |
| eth | uniswap_v3_factory | 0x1f98431c8ad98523631ae4a59f267346ea31f984 | 24535 | UniswapV3Factory | 5 | 26020110 / 4.1 小时前 | PoolCreated=5 | VERIFIED-LIVE |
| eth | doppler_deployer | 0xb35469ee64a87afd19b31615094fe3962d73e421 | 24087 | - | 0 | - | - | HISTORICAL |
| eth | doppler_hook_initializer | 0xbdf938149ac6a781f94faa0ed45e6a0e984c6544 | 24384 | - | 0 | - | - | HISTORICAL |
| eth | klik_factory | 0x254bf550657040f78608476ce9aad820ab2266ad | 22250 | Factory | 2 | 26019535 / 6.0 小时前 | - | VERIFIED-LIVE |
| eth | uniswap_liquidity_launcher | 0x0000ffffbe8efe702c8703ae3477ff5de3d319c0 | 4127 | - | 0 | - | - | VERIFIED-LIVE |
| eth | uniswap_lbp_strategy_v3 | 0x2eef0e2a9a652d755accad95a24541a98b5ca000 | 21241 | - | 1 | 26020100 / 4.1 小时前 | - | VERIFIED-LIVE |
| eth | clanker_v4_eth | 0x6c8599779b03b00aaae63c6378830919abb75473 | 12070 | Clanker | 0 | - | - | VERIFIED-LIVE |
| eth | trench_manager_v2 | 0x974b2d86d6353c62f19110127af1a5df4c6370f0 | 761 | - | 51 | 26019339 / 6.7 小时前 | - | VERIFIED-LIVE |
| eth | trench_bonding_curve_factory_eth | 0x02acb27bd38ab7ae4b569ba7251ff31739bd8e36 | 10354 | - | 0 | - | - | HISTORICAL |
| eth | livo_launchpad_eth | 0xaa74aa89590e3b50be178ea970e490c173b61110 | 8141 | LivoLaunchpad | 6 | 26020708 / 2.1 小时前 | LivoTokenBuy=0, LivoTokenSell=4, TokenLaunched=2, TokenGraduated=0 | VERIFIED-LIVE |
| eth | livo_factory_univ2_eth | 0x78af7e41ab894fc2acd1b1c918e3cc6d710054b9 | 163 | - | 0 | - | TokenCreated=0, BondingCurveAssigned=0 | HISTORICAL |
| eth | stroid_launchpad_v3_eth | 0x75d9ef48e30bcb0b658c1d387233fb52ebf9a4fe | 18455 | LaunchpadV3 | 1 | 26021087 / 51 分钟前 | - | VERIFIED-LIVE |
| eth | stroid_launchpad_v2_eth | 0xbbdf89fd1700bcbd906b743610f88a1fbebc5b21 | 22151 | LaunchpadV2 | 0 | - | - | HISTORICAL |
| eth | stroid_launchpad_v1_eth | 0x86b00cc0e3da64af96dc90c52bb4f755fc243b6a | 13652 | Launchpad | 0 | - | - | HISTORICAL |
| base | uniswap_v3_factory | 0x33128a8fc17869897dce68ed026d694621f6fdfd | 24535 | UniswapV3Factory | 2 | 51573205 / 51 分钟前 | PoolCreated=2 | VERIFIED-LIVE |
| base | aerodrome_pool_factory | 0x420dd381b31aef6683db6b902084cb0ffece40da | 3516 | PoolFactory | 1 | 51574540 / 7 分钟前 | PoolCreated=1 | VERIFIED-LIVE |
| base | clanker_v4 | 0xe85a59c628f7d27878aceb4bf3b35733630083a9 | 12375 | Clanker | 43 | 51572836 / 1.1 小时前 | - | VERIFIED-LIVE |
| base | flaunch | 0x516af52d0c629b5e378da4dc64ecb0744ce10109 | 9760 | Flaunch | 0 | - | - | CODE-ONLY |
| base | doppler_deployer | 0xb35469ee64a87afd19b31615094fe3962d73e421 | 24087 | DopplerDeployer | 0 | - | - | CODE-ONLY |
| base | doppler_hook_initializer | 0xbdf938149ac6a781f94faa0ed45e6a0e984c6544 | 24384 | DopplerHookInitializer | 2750 | 51572745 / 1.1 小时前 | - | VERIFIED-LIVE |
| base | doppler_uniswap_v4_initializer | 0x53b4c21a6cb61d64f636abbfa6e8e90e6558e8ad | 2698 | UniswapV4Initializer | 0 | - | - | CODE-ONLY |
| base | virtuals_bonding_proxy | 0xf66dea7b3e897cd44a5a231c61b6b4423d613259 | 1159 | TransparentUpgradeableProxy | 0 | - | - | CODE-ONLY |
| base | virtuals_sell_executor | 0xf8dd39c71a278fe9f4377d009d7627ef140f809e | 7503 | Multicall3 | 0 | - | - | CODE-ONLY |
| base | virtuals_creator_vault | 0xdad686299fb562f89e55da05f1d96fabeb2a2e32 | 0 | - | - | - | - | FAILED |
| base | uniswap_v2_factory_base | 0x8909dc15e40173ff4699343b6eb8132c65e18ec6 | 13859 | UniswapV2Factory | 20 | 51572796 / 1.1 小时前 | - | VERIFIED-LIVE |
| base | flap_portal | 0x0000bc1c4fd15dd79029af8f5d77d68ae4490000 | 2840 | - | 693 | 51573221 / 51 分钟前 | - | VERIFIED-LIVE |
| base | flap_vault_portal | 0x00003877386745d059d92fa39159595bdabf0000 | 2840 | - | 0 | - | - | CODE-ONLY |
| base | flap_tax_token_helper | 0x0000fdc1c95d399ef5aae461216b3628c6020000 | 2840 | - | 0 | - | - | CODE-ONLY |
| base | klik_factory | 0xb6cb1c049ee8942683fd3172f7eba63b6e8a6835 | 21708 | Factory | 0 | - | - | CODE-ONLY |
| base | klik_hook | 0x487f034772b128e917b23a4edfd53442aa8de0cc | 4071 | - | 0 | - | - | CODE-ONLY |
| base | zora_factory | 0x777777751622c0d3258f214f9df38e35bf45baf3 | 130 | ZoraFactory | 46 | 51572777 / 1.1 小时前 | - | VERIFIED-LIVE |
| base | uniswap_liquidity_launcher | 0x0000ffffbe8efe702c8703ae3477ff5de3d319c0 | 4127 | LiquidityLauncher | 0 | - | - | VERIFIED-LIVE |
| base | uniswap_lbp_strategy_v3 | 0xf10124b01e9fa88b0a2ef3fa95a53b3310446000 | 21241 | LBPStrategy | 0 | - | - | CODE-ONLY |
| base | uniswap_uerc20_factory | 0x000000e200088d55c39a11f609e5f667729ad49b | 0 | - | - | - | - | FAILED |
| base | virtuals_bonding_curve_base | 0x1a540088125d00dd3990f9da45ca0859af4d3b01 | 1167 | TransparentUpgradeableProxy | 3 | 51573131 / 54 分钟前 | - | VERIFIED-LIVE |
| arbitrum | uniswap_v3_factory | 0x1f98431c8ad98523631ae4a59f267346ea31f984 | 24535 | UniswapV3Factory | 6 | 507161674 / 4.9 小时前 | PoolCreated=6 | VERIFIED-LIVE |
| arbitrum | camelot_factory | 0x6eccab422d763ac031210895c81787e87b43a652 | 23646 | CamelotFactory | 0 | - | - | CODE-ONLY |
| arbitrum | doppler_deployer | 0x4389ad34938b14f25cff7ed983c53f5a42a2573f | 24063 | - | 0 | - | - | CODE-ONLY |
| arbitrum | doppler_hook_initializer | 0xaa7f809bb3752f715fa2e418230667c382a56544 | 24039 | DopplerHookInitializer | 0 | - | - | HISTORICAL |
| robinhood | doppler_deployer | 0x4389ad34938b14f25cff7ed983c53f5a42a2573f | 24063 | - | 0 | - | - | CODE-ONLY |
| robinhood | doppler_hook_initializer | 0x4e3468951d49f2eea976ed0d6e75ffcb44a9a544 | 25533 | DopplerHookInitializer | 710 | 68250872 / 3 分钟前 | - | VERIFIED-LIVE |
| robinhood | doppler_uniswap_v4_initializer | 0x6cce158b6d1747617fc218592b4d60b239b957ea | 2685 | - | 1 | 17358812 / 59.3 天前 | - | VERIFIED-LIVE |
| robinhood | pons_factory | 0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e | 24177 | PonsV2LaunchFactory | 61 | 68250954 / 3 分钟前 | - | VERIFIED-LIVE |
| robinhood | pons_launch_and_buy | 0xe33e9e479df8802cb0866d5d05258bec4cf62948 | 4416 | PonsV2LaunchAndBuy | 46 | 68250979 / 3 分钟前 | - | VERIFIED-LIVE |
| robinhood | pons_meme_hook | 0xe5e702641ea86f4ae6cc3cdaed2b886f976be044 | 15167 | PonsV2MemeHook | 1068 | 68250877 / 4 分钟前 | - | VERIFIED-LIVE |
| robinhood | long_airlock | 0xeb7c034704ef8dcd2d32324c1545f62fb4ad0862 | 5695 | - | 6 | 68251843 / 2 分钟前 | - | VERIFIED-LIVE |
| robinhood | long_rehype_hook | 0x6f02324d20cc679d0e585290caa6b16bacbc0f77 | 14564 | - | 9 | 68251843 / 2 分钟前 | - | VERIFIED-LIVE |
| robinhood | o1_factory | 0xce9c48cfa068947f77738c81be406b53338e5b0d | 24466 | RWAERC20LaunchpadFactory | 2201 | 58259628 / 11.7 天前 | - | VERIFIED-LIVE |
| robinhood | o1_hook | 0x0310cfebe1d7a69f2414f6595bbe9d17c5342acc | 15732 | LaunchHook | 1962 | 68053832 / 5.6 小时前 | - | VERIFIED-LIVE |
| robinhood | o1_fee_escrow | 0xc5444b417a04a7e1b9c1e327c7d499803c14e5ef | 1873 | FeeEscrow | 1310 | 68054048 / 5.5 小时前 | - | VERIFIED-LIVE |
| robinhood | bags_factory | 0xe8cc4431adf8b5a847c113ef0c6af9043219cb37 | 130 | ERC1967Proxy | 529 | 48643897 / 23.0 天前 | - | VERIFIED-LIVE |
| robinhood | bags_hook | 0x2380abf72c17aabab76480244759ac7e2932eecc | 6628 | BagsV4Hook | 291 | 66277962 / 2.3 天前 | - | VERIFIED-LIVE |
| robinhood | bags_vault | 0x4861446aa7ffd9e67a83cbbacb1a4b70540b83aa | 130 | ERC1967Proxy | 247 | 66283973 / 2.3 天前 | - | VERIFIED-LIVE |
| robinhood | letscash_factory | 0x5bd1fbe78a78fe8236fa00cf48fbeba74ae34661 | 176 | ERC1967Proxy | 6521 | 48265964 / 23.4 天前 | - | VERIFIED-LIVE |
| robinhood | letscash_hook | 0x75a54357d9c78a2db19004a5fdc76c50f9242aec | 13927 | CashCatHookV2 | 0 | - | - | HISTORICAL |
| robinhood | pools_entry | 0x0000ffffbe8efe702c8703ae3477ff5de3d319c0 | 4127 | LiquidityLauncher | 5578 | 63255128 / 5.8 天前 | - | VERIFIED-LIVE |
| robinhood | pools_token_factory | 0x000000e200088d55c39a11f609e5f667729ad49b | 13380 | - | 8081 | 48258564 / 23.4 天前 | - | VERIFIED-LIVE |
| robinhood | pair_launchpad | 0x8660a7f019c7943b0b0a91b8e39aff3b6db6ae62 | 145 | PairERC1967Proxy | 8911 | 48254466 / 23.4 天前 | - | VERIFIED-LIVE |
| robinhood | flap_controller | 0x26605f322f7ff986f381bb9a6e3f5dab0beaeb09 | 2840 | TransparentUpgradeableProxy | 6729 | 68052890 / 5.6 小时前 | - | VERIFIED-LIVE |
| robinhood | varo_launchpad | 0x851153fe84239c2dc55fa191ac2f099e20a6d0b8 | 141 | UUPSProxy | 3724 | 48271579 / 23.4 天前 | - | VERIFIED-LIVE |
| robinhood | virtuals_launchpad | 0xd4ccbfa37e2f35611b3042e4096ad7a3459bd007 | 1167 | - | 1574 | 48281616 / 23.4 天前 | - | VERIFIED-LIVE |
| robinhood | virtuals_factory | 0xfc2e4da3edb2e18100473339c763705d263d20a9 | 1167 | - | 525 | 48321419 / 23.3 天前 | - | VERIFIED-LIVE |
| robinhood | flap_vault_portal | 0xe9f7ab7de8fb8756acbb6a1cd13316a43308197b | 2840 | - | 159 | 48622490 / 23.0 天前 | - | VERIFIED-LIVE |
| robinhood | flap_tax_token_helper | 0xb10bd2672ae63735d677164a54b573a016f0203c | 2840 | TransparentUpgradeableProxy | 2 | 4185534 / 74.6 天前 | - | VERIFIED-LIVE |
| robinhood | flap_trigger_service | 0xd3421b1b616a72bb88993a0cf75709bb8d532cc1 | 2840 | TransparentUpgradeableProxy | 49 | 55296598 / 15.2 天前 | - | VERIFIED-LIVE |
| robinhood | uniswap_v3_factory_robinhood | 0x1f7d7550b1b028f7571e69a784071f0205fd2efa | 24535 | UniswapV3Factory | 109 | 68062545 / 5.3 小时前 | - | VERIFIED-LIVE |
| robinhood | apestore_launcher | 0x6e4910ea5a04376032f6564da9a9e4e88b7a87c1 | 12528 | - | 44 | 50056769 / 21.3 天前 | - | VERIFIED-LIVE |
| robinhood | bottomfun_factory | 0x1bed2687321074a198302c004036b51923812b18 | 5725 | TokenFactory | 31 | 10479808 / 67.3 天前 | - | VERIFIED-LIVE |
| robinhood | bottomfun_hook | 0xd23c94bd8cbeb48c169c020e16a844aeab2368cc | 10046 | - | 256 | 49991179 / 21.4 天前 | - | VERIFIED-LIVE |
| robinhood | bottomfun_locker | 0xf46663171d1d0653fe7c3b8e42d43bccac137c56 | 13011 | PermanentLocker | 53 | 10275154 / 67.5 天前 | - | VERIFIED-LIVE |
| robinhood | bowfun_factory | 0xc70e510e14710ea535cab7b2414860af63feab79 | 16318 | - | 0 | - | - | HISTORICAL |
| robinhood | dyorfun_factory | 0x80b42aed46d73f47119dc444bea28a9e68f32bf4 | 130 | ERC1967Proxy | 2 | 48675542 / 22.9 天前 | - | VERIFIED-LIVE |
| robinhood | klik_factory | 0x16cf6788b762ee8969744586ed16fc5705140dd7 | 22343 | Factory | 805 | 48374863 / 23.3 天前 | - | VERIFIED-LIVE |
| robinhood | klik_hook | 0x745d717620052a97a22deee2e5eba59583f3e0cc | 9130 | UniversalKlikHook | 0 | - | - | CODE-ONLY |
| robinhood | launchproof_cca_factory | 0x000000001f26a0044baa66024e7b6599c61963f8 | 24214 | ContinuousClearingAuctionFactory | 645 | 48336939 / 23.3 天前 | - | VERIFIED-LIVE |
| robinhood | launchproof_lbp_strategy | 0x05d552391067389ee44fec3924157ed33f976000 | 20624 | - | 1246 | 48307623 / 23.4 天前 | - | VERIFIED-LIVE |
| robinhood | launchproof_instant_no_fees | 0xad44d55e7f8337c3ce113fbb591486e85be104b2 | 10822 | InstantLaunchStrategy | 256 | 48287627 / 23.4 天前 | - | VERIFIED-LIVE |
| robinhood | noxafun_factory | 0xd9ec2db5f3d1b236843925949fe5bd8a3836fccb | 22811 | - | 0 | - | - | HISTORICAL |
| robinhood | pons_active_factory | 0xa5aab3f0c6eeadf30ef1d3eb997108e976351feb | 24353 | PonsLaunchFactory | 0 | - | - | HISTORICAL |
| robinhood | pons_active_locker | 0x736d76699c26d0d966744cae304c000d471f7f35 | 5426 | PonsLaunchLocker | 1 | 68252771 / 2 分钟前 | - | VERIFIED-LIVE |
| robinhood | pons_legacy_factory | 0x0c37a24f5d23a486fa692d1500881d698b1f77a4 | 24192 | - | 2 | 47230445 / 24.6 天前 | - | VERIFIED-LIVE |
| robinhood | pons_legacy_locker | 0x31ca5e101941a93a7dd6d0497928700625cf54b5 | 4861 | - | 136 | 48484789 / 23.1 天前 | - | VERIFIED-LIVE |
| robinhood | printr_protocol | 0xb77726291b125515d0a7affeea2b04f2ff243172 | 273 | PrintrProxy | 5 | 54467970 / 16.1 天前 | - | VERIFIED-LIVE |
| robinhood | realfun_launcher | 0xa3a71925be892c609ac4be4efe918dc9c35fc5e8 | 19098 | SingleSidedLauncher | 240 | 58013449 / 12.0 天前 | - | VERIFIED-LIVE |
| robinhood | robinfun_factory | 0xd861cb5dc71a0171e8f0f6586cadb069f3a35e4d | 25062 | - | 563 | 48616844 / 23.0 天前 | - | VERIFIED-LIVE |
| robinhood | uniswap_lbp_strategy_v3 | 0xbf1ab81f7d534b2cc0da76fcf4d541322bb0e000 | 21241 | - | 115 | 63133486 / 6.0 天前 | - | VERIFIED-LIVE |
| robinhood | pons_v2_launch_locker | 0x267444d099b10fb5ed7c3cc7b7c767adca574952 | 1969 | RobinFunFiV2LaunchLocker | 48 | 68062524 / 5.3 小时前 | - | VERIFIED-LIVE |
| robinhood | hoodfun_launchpad | 0x5fcc1df0dc020cf454e742e9a8ae2554c37a452c | 20518 | HoodCustomLaunchpad | 85 | 50903843 / 20.3 天前 | - | VERIFIED-LIVE |
| robinhood | hoodfun_platform | 0xc6a2941b962fb667786d7f4b97f7f965d6f0a4f8 | 3762 | - | 24 | 11420448 / 66.2 天前 | - | VERIFIED-LIVE |
| robinhood | trench_manager | 0x77dc6f6361b7b99456fc3761ce5b7dda80d83f9d | 758 | - | 206 | 67754957 / 14.0 小时前 | - | VERIFIED-LIVE |
| robinhood | trench_bonding_curve_factory | 0x2ecfb98bce4f3616115e4a2a7a2379af388dfbaa | 758 | - | 405 | 52483215 / 18.5 天前 | - | VERIFIED-LIVE |
| robinhood | trench_fee_vault | 0x076e3cd13e188e3646828e7cebb766c7dd6adb8a | 758 | - | 224 | 67255491 / 28.0 小时前 | - | VERIFIED-LIVE |
| robinhood | trench_v4_fee_hook | 0x31200554eca1eff6d130dbec7975afa1234b60cc | 5501 | - | 237 | 65789997 / 2.9 天前 | - | VERIFIED-LIVE |
| robinhood | livo_launchpad | 0xfd550c5dc070ea575a06a40f2e18304d85211663 | 8141 | LivoLaunchpad | 2 | 68149936 / 2.9 小时前 | LivoTokenBuy=0, LivoTokenSell=0, TokenLaunched=2, TokenGraduated=0 | VERIFIED-LIVE |
| robinhood | livo_factory_univ2 | 0x7843203be233b3be7e5017a68a64fdbf32b45ffe | 163 | - | 1006 | 51116683 / 20.1 天前 | TokenCreated=503, BondingCurveAssigned=503 | VERIFIED-LIVE |
| robinhood | coinbarrel_launcher | 0x4234e536aa5da8be18d41ef6f86533430e264e70 | 163 | ERC1967Proxy | 258 | 48499592 / 23.1 天前 | - | VERIFIED-LIVE |
| robinhood | arrowpad_factory | 0x69225a43b20b824f4027b201731d9a21368bf6bc | 10825 | ArrowPadFactory | 122 | 30299349 / 44.3 天前 | - | VERIFIED-LIVE |
| robinhood | arrowpad_locker | 0xba9c247041a7715591c7b48f20dfba520a7d68e9 | 3986 | ArrowPadLocker | 3 | 63430816 / 5.6 天前 | - | VERIFIED-LIVE |
| robinhood | stoxes_portal | 0xa0e82b5bf840e268718a143032cd630a3bf1850e | 7310 | StoxesFunPortal | 1 | 64163410 / 4.8 天前 | TokenRegistered=0, Bought=0, Sold=1, Graduated=0 | VERIFIED-LIVE |
| robinhood | stoxes_factory | 0x3a5a312b66a3f3aabb647e75e8e81ccc08b7bb58 | 5722 | StoxesFunFactory | 1 | 33790781 / 40.2 天前 | - | VERIFIED-LIVE |
| robinhood | stoxes_v3_launch_factory | 0xf612b37d684b3eca3583121172764ab0ef8417fc | 13093 | StoxesFunV3LaunchFactory | 1 | 52755534 / 18.2 天前 | TokenLaunched=1 | VERIFIED-LIVE |
| robinhood | pew_instant_factory_1 | 0xc9182c283a9b739fed26a8e7f55a4d2b09f39d8c | 22352 | - | 6 | 56863638 / 13.3 天前 | - | VERIFIED-LIVE |
| robinhood | pew_instant_factory_2 | 0x3364e68a4454d18132d0a2ac538c966369828291 | 22352 | - | 2 | 66629945 / 45.5 小时前 | - | VERIFIED-LIVE |
| robinhood | pew_instant_factory_3 | 0x7da7cf925dc8e98e2c2395979efa54bbd43d576e | 22352 | - | 1 | 44588996 / 27.7 天前 | - | VERIFIED-LIVE |
| robinhood | pew_fee_splitter | 0x009aa3cc02f3b8b1c50b940760ae7022d676dcba | 4833 | - | 6 | 22029025 / 53.9 天前 | - | VERIFIED-LIVE |
| robinhood | pew_launch_zap | 0xde12817424402685dd8419ce174fd05a5233d107 | 2122 | - | 0 | - | - | CODE-ONLY |
| robinhood | stroid_launchpad_v3 | 0x23343d0c3c17d69d57e7ac81dc7d79b591a9d2d2 | 18455 | - | 1 | 10655939 / 67.1 天前 | - | VERIFIED-LIVE |
| robinhood | stroid_fee_hook_v3 | 0xd6c16a041ba47eb4aeb4fd8ccc2580446e8ec0cc | 13209 | - | 4 | 10655939 / 67.1 天前 | - | VERIFIED-LIVE |
| robinhood | hyper_meme_factory_v4 | 0x08a95be2bc4a3dcf1488ebd4145004d5948297e9 | 3973 | FactoryV4 | 7 | 13345472 / 63.9 天前 | - | VERIFIED-LIVE |
| robinhood | hyper_meme_curve_impl_v4 | 0x1579ec5a49a31ba6ad0d1fcefc05ade11df4e643 | 8127 | BondingCurve | 0 | - | - | HISTORICAL |
| robinhood | pmav_hook | 0x97472ae141ff13a4d317328bc4f3f95172fba8cc | 30507 | PmavHook | 15 | 63679509 / 5.3 天前 | - | VERIFIED-LIVE |
| robinhood | pmav_hook_v22 | 0x2d0e12fec42cea31022c37e9d714db6d2d49a8cc | 35043 | PmavHook | 3 | 66539977 / 48.0 小时前 | - | VERIFIED-LIVE |
| robinhood | pmav_flywheel_factory | 0xdbe590c96cabb40ed85a8eae4ad1be9e40f011e1 | 3318 | RewardsFactory | 47 | 17629800 / 59.0 天前 | - | VERIFIED-LIVE |
| robinhood | circus_launchpad | 0xb7fa26c6fcb8801cabc538b82a6e80ae1c43cb00 | 130 | ERC1967Proxy | 1621 | 63354349 / 5.7 天前 | - | VERIFIED-LIVE |
| robinhood | circus_locker | 0xa256bc02dbb92ed1cb607a7de7b0bbebca29e930 | 4662 | CircusLocker | 8 | 47736155 / 24.0 天前 | - | VERIFIED-LIVE |
| robinhood | circus_lens | 0x15bd5b371f1d206e39528054be35973964c91887 | 5766 | - | 0 | - | - | CODE-ONLY |
| robinhood | lunch_launcher | 0xf5ac14e7691ef44b15b59fcc6a756e41a3e5efd6 | 130 | ERC1967Proxy | 1388 | 13519785 / 63.7 天前 | - | VERIFIED-LIVE |
| robinhood | lunch_pair_launcher | 0x568e12b312751992dcfe387cfc8ec7d63e941103 | 130 | - | 8 | 68062274 / 5.4 小时前 | - | VERIFIED-LIVE |
| robinhood | lunch_v4_launcher | 0xc783221ab1db0244203458417981b4631e80b988 | 130 | ERC1967Proxy | 219 | 63548462 / 5.5 天前 | - | VERIFIED-LIVE |
| robinhood | lunch_v4_pair_hook | 0x4eb1976978756bd56802d8162f2271844924e0cc | 130 | ERC1967Proxy | - | - | - | CODE-ONLY |
| robinhood | memecoin_fun_factory | 0x5b5e19d685c9564b17c670b891cf55ce37aeb364 | 190 | - | 6 | 62929825 / 6.2 天前 | - | VERIFIED-LIVE |
| robinhood | memecoin_fun_locker | 0x577eabdafc2bbf4807a1c454bbd4de3641cf50f4 | 11256 | MemePositionFeeLockerWithDividend | 2 | 58150076 / 11.8 天前 | - | VERIFIED-LIVE |
| robinhood | potato_pad | 0x88bb90a984b4f61d971d71b25c81b187aee4ca07 | 15857 | - | 1 | 14983183 / 62.0 天前 | - | VERIFIED-LIVE |
| robinhood | potato_curve_pad | 0xbe2acd9044516399aa4c697c299571664fbe9d4b | 21475 | PotatoCurvePad | 5 | 37964700 / 35.4 天前 | - | VERIFIED-LIVE |
| robinhood | lemon_v3_factory | 0xe51960f1b45f1c9fb6d166e6a884f866fc70433b | 24535 | UniswapV3Factory | 43 | 63637378 / 5.4 天前 | - | VERIFIED-LIVE |
| arc | uniswap_liquidity_launcher | 0x0000ffffbe8efe702c8703ae3477ff5de3d319c0 | 4127 | - | 0 | - | - | VERIFIED-LIVE |
| arc | uniswap_lbp_strategy_v3 | 0x542bcda1015485ef0b1cd11b835dc58df5102000 | 21241 | - | 0 | - | - | HISTORICAL |
| arc | coinbarrel_launcher_arc | 0x80c7a44817ac4bfa0ce9241386791157e7fd9544 | 163 | - | 0 | - | - | HISTORICAL |
| stable | pew_instant_factory_1_stable | 0xee0665f2e348bed28769cbd09fd804be953d10db | 22354 | - | 0 | - | - | CODE-ONLY |
| stable | pew_instant_factory_2_stable | 0x096b4622056afd784c5ceefbceab3f3ed27f9a07 | 22352 | - | 0 | - | - | CODE-ONLY |
| hyperevm | hyperswap_v3_factory | 0xb1c0fa0b789320044a6f623cfe5ebda9562602e3 | 24351 | - | 0 | - | - | VERIFIED-LIVE |
| hyperevm | hyperswap_position_manager | 0x6eda206207c09e5428f281761ddc0d300851fbc8 | 24384 | - | 0 | - | - | HISTORICAL |
| hyperevm | hyper_meme_factory | 0x2b593d685de902f215b04dbb1bf225dc65126597 | 3805 | TokenFactory | 0 | - | - | HISTORICAL |
| sol | pump_fun_program | 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P | - | - | - | - | - | VERIFIED-LIVE |
| sol | letsbonk_program | LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj | - | - | - | - | - | VERIFIED-LIVE |
| sol | boop_program | boop8hVGQGqehUK2iVEMEnMrL5RbjywRzHKBmBE7ry4 | - | - | - | - | - | VERIFIED-LIVE |
| sol | moonshot_program | MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG | - | - | - | - | - | VERIFIED-LIVE |
| sol | virtuals_solana_program | 5U3EU2ubXtK84QcRjWVmYt9RaDyA8gKxdUrPFXmZyaki | - | - | - | - | - | FAILED |
| sol | bags_fee_share_v1 | FEEhPbKVKnco9EXnaY3i4R5rQVUx91wgVfu8qokixywi | - | - | - | - | - | VERIFIED-LIVE |
| sol | bags_meteora_dbc | dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN | - | - | - | - | - | VERIFIED-LIVE |
| sol | bags_meteora_damm_v2 | cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG | - | - | - | - | - | VERIFIED-LIVE |
| sol | raydium_amm_v4 | 675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8 | - | - | - | - | - | VERIFIED-LIVE |
| sol | raydium_cpmm | CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C | - | - | - | - | - | VERIFIED-LIVE |
| sol | raydium_clmm | CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK | - | - | - | - | - | VERIFIED-LIVE |
| sol | pump_amm | pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA | - | - | - | - | - | VERIFIED-LIVE |
| sol | meteora_dlmm | LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo | - | - | - | - | - | VERIFIED-LIVE |
| sol | orca_whirlpool | whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc | - | - | - | - | - | VERIFIED-LIVE |

## 未核实（明确阻塞，不猜地址）

| 链 | 平台 | 阻塞原因 | 下一步 |
|---|---|---|---|
| bsc | cubepeg | 链上/官方页面核实：cubepeg 是 Four.meme 的 UniToken NFT 发射模式（four.meme 代币页标注“模式Cubepeg发射”，cubepeg.com 是 Cubus×Four.meme 的 NFT 索引前端），没有独立工厂合约 | 已由 fourmeme_token_manager 覆盖；候选地址 0x60a2dfa7… 仅命中泛用 Launchpad 合约（0 事件），不采用 |
| bsc | goplus_creator | GoPlus 侧没有公开工厂合约；已证明 SafuSkill（goplus_skills）代币走 Four.meme TokenManager2 | 需要 GoPlus 官方合约页或 GMGN API key 反查发射交易 |
| bsc | lunafun | 链上实测：LUNA.FUN 代币 0xbe3e95eb…4444 经官方 Helper3 getTokenInfo 返回 version=2、tokenManager=0x5c9520…（Four.meme TokenManager2） | 已由 fourmeme_token_manager 覆盖；GMGN 的 lunafun 是 Four.meme 上的合作方分类 |
| bsc | four_xmode_agent | 缺少有来源的工厂地址；GMGN 把它列为独立平台 | 先查 Four.meme 官方文档是否公开 xmode agent 合约，再上链核实 |
| eth | printr | Robinhood 侧的 printr protocol 已核实；Ethereum 侧没有官方部署表（printr 文档只给 Robinhood 地址） | 等 printr 公开多链部署表，或在提供 API key 后用 GMGN 反查 ETH 发射交易 |
| eth | stroid（v1/v2 之外的旧版） | 官方前端只列出 launchpad v1/v2/v3 与 feeHook v1/v2/v3，均已列入 targets 并核实 | 如出现新版本，按同一 harness 补测 |
| base | basememe | GMGN 平台枚举里有 basememe，但没有官方合约页；Base 免费公共 RPC 不提供 archive eth_getLogs，无法用日志反查地址 | 需要官方合约页，或付费 archive RPC / GMGN API key |
| base | baseapp | 同上：没有官方合约页，且无法用日志反查 | 同上 |
| base | bankr | Bankr 本身不发币；官方文档说明 Base 上通过 Doppler provider 部署（docs.bankr.bot） | 已改为核实 Doppler 合约；Bankr 前端/CLI 只是客户端 |
| robinhood | gmgn_robinhood_discovery | GMGN openapi 需要个人 API key（本地未配置） | 用户提供 GMGN_API_KEY 后可只读验证 27 个平台并交叉核对 allow-list 之外的平台 |
| robinhood | holoworld (HoloLaunch) | 平台确实存在（Holoworld 官方 X 2026-07-15 宣布 HoloLaunch 上线 Robinhood Chain，Binance Square 亦有报道），但官网与 docs.holoworld.com 的 llms.txt 全量目录里都没有合约页 | 需要 Holoworld 公开合约页，或提供 archive RPC / GMGN API key 做链上反查 |
| robinhood | arena | arena.exchange 能打开但页面内没有任何 chain 4663 配置或合约地址；Dedaub 检测表把它列为 Avalanche + Robinhood 平台 | 需要官方合约页，或提供 archive RPC / GMGN API key |
| robinhood | motion | 网络核查确认不存在：motion.fun 是 GoDaddy 建站器上的 “Launching Soon” 占位页（版权 2024），没有任何产品、合约或链上活动 | 忽略；若将来出现真实产品再重新核实 |
| robinhood | dyorswap | 网络核查确认不存在：dyorswap.fun 无法解析，dyorswap.com 在售，dyorswap.io 返回 SSL 525 | 忽略 |
| robinhood | mintfast | 网络核查确认不存在：mintfast.fun 无法解析，mintfast.xyz 只有 114 字节空页 | 忽略 |
| robinhood | launchhood | 网络核查确认不存在：launchhood.fun / launchhood.xyz 都无法解析 | 忽略 |
| robinhood | leavehood | 网络核查确认不存在：leavehood.fun / leavehood.xyz 都无法解析 | 忽略 |
| robinhood | oro | 网络核查确认不是发射台：oro.fun 是 Telegram 上的预测市场（“Oro — Yes or No”），oro.xyz 是待售域名 | 忽略；Dedaub 的 oro slug 对应的是另一个未公开的合约 |
| robinhood | arrowfinance | 网络核查确认 arrowfinance.io 是 Robinhood Chain 上的 CDP 借贷协议，它自己的 “Launchpad” 入口就是已核实的 ArrowPad（arrowpad.fun） | 已由 arrowpad_factory 覆盖 |
| robinhood | sushi | Dedaub 把 sushi 列为 BSC + Robinhood 的发射平台；Sushi 是 DEX 不是发射台，Robinhood 上 lemon.fun 使用的就是 Sushi V3 工厂 | 已由 lemon_v3_factory 与 Uniswap/Pancake 池工厂覆盖 |
| arc | arc_launchpads | GMGN 对 arc 没有默认平台 allow-list（服务端不加过滤）；链上只核实到 Uniswap V4 PoolManager 与 LiquidityLauncher | 用链上日志发现更多活跃工厂后按地址核实 |
| stable | stable_launchpads | pew.fun 的 Stable 部署已列入 targets（有代码、窗口内 0 事件）；Stable RPC 的 eth_getLogs 只允许约 1,000 区块窗口，无法证明历史事件 | 需要付费 archive RPC 或 GMGN API key 才能收紧判定 |
| hyperevm | hyperevm_dex | HyperSwap V3 工厂与 PositionManager 已核实；其余 DEX（KittenSwap 等）没有公开部署表 | 找到官方部署表后按同一 harness 补测 |
| sol | bonkers | GMGN 平台枚举里有 bonkers，但没有可核实的官方 program id | 需要官方文档或链上反查；找到后按同一 harness 补测 |

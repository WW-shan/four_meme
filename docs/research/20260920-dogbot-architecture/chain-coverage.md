# 多链与 launchpad 覆盖研究（2026-09-20）

目的：回答“现在到底有哪些链和平台在打狗”，以及我们的雷达该怎么改。
证据：GMGN 官方 CLI 源码与文档（本地 clone）、GMGN 只读 API 探测（demo key，一次性）、DefiLlama/第三方数据。

## 一、GMGN 正式支持的链（源码为准）

`src/validate.ts` 的 `VALID_CHAINS`：

```
sol / bsc / base / eth / arbitrum / hyperevm / robinhood / arc / stable
```

- `monad` 在源码里被**注释掉**：CLI 不接受。
- 只读探测结果：robinhood ✅（2 个 token）、arc ✅（2 个）、hyperevm ✅（1 个）、stable ✅ 但返回 **0 个 token**；monad / megaeth / xlayer / sonic → HTTP 400。
- 结论：GMGN 目前可用的“打狗链”是 **9 条**；新链里真正有量的是 Robinhood、Arc、HyperEVM，Stable 暂时没量，Monad 等尚未开放。

## 二、每条链的 launchpad（GMGN 平台枚举）

- **Robinhood**（27 个平台）：trench / noxa / dyorswap / apestore / printr / virtuals / bankr / clanker / klik / livo / flap / flap_stocks / flap_pve / bags / bowfun / o1 / circus / arrowfinance / longxyz / motion / pons / stoxes_tax / stoxes / holoworld / pewfun / dyorfun_v3 / noxafi。清单可能滞后，未列入的平台不会出现在 trenches。
- **Arc / Stable**：没有默认 allow-list（返回全部平台）；**不支持 condition orders / smart_trade**，只支持普通 swap 和 limit_order。
- **BSC**：fourmeme / four_xmode_agent / cubepeg / likwid / goplus_creator / goplus_skills / openfour / flap / flap_stocks / flap_aioracle / clanker 等。
- **Solana**：pump / bonk / bags 等；**Base**：clanker / flaunch / zora / klik 等；**HyperEVM**：hyperliquid 生态 launchpad。
- 代币创建 API 目前只覆盖：sol(pump/bonk/bags)、bsc(fourmeme/flap)、base(klik/clanker)、robinhood(trench/pons)。

## 三、对我们架构的影响

现状：雷达只订阅 **BSC 的 Four.meme 一个合约**，因此 BSC 都扫不全，更谈不上全链。GMGN 的公开 API 已经能跨 9 条链做发现（hot searches / trending / trenches / 平台过滤），所以正确分层是：

1. **发现层（已有基础）**：GMGN 多链 hot searches + trending + trenches，按链和 platform 过滤；X/社交只做提醒与排序。
2. **链适配层（新）**：
   - EVM 家族参数化：chain_id、RPC、工厂地址、ABI → 统一 `TokenLaunch`；BSC 先覆盖 Four.meme + Flap + Pancake V2/V3；Base/ETH/Arbitrum/HyperEVM/Robinhood/Arc/Stable 复用同一适配器，只换配置。
   - Solana 单独适配：pump.fun / bonk / bags + Geyser/logs。
3. **安全层**：GoPlus / honeypot / DexScreener 已是多链接口，按链补字段；未知字段继续 fail-closed。
4. **执行层**：按链独立；Arc/Stable 只支持 limit_order，不支持 TP/SL，暂不列入实盘优先。

## 四、建议优先级

1. **BSC 全平台**：Four.meme + Flap + Pancake V2/V3 + 其余 BSC launchpad。
2. **Robinhood**：平台最多、GMGN 有量、新链红利；先只读发现 + 影子。
3. **Solana**：pump/bonk/bags；Geyser/Jito 需要 provider key。
4. **Base / ETH / Arbitrum / HyperEVM**：复用 EVM 适配器。
5. **Arc**：只读观察；**Stable**：暂不投入（当前 0 量）。

## 五、证据

- GMGN CLI 源码：`src/validate.ts`、`docs/cli-usage.md`（本地 clone `/tmp/meme-gmgn-upstream-20260916`）。
- 只读探测命令：`POST https://openapi.gmgn.ai/v1/market/hot_searches`，链 robinhood/arc/stable/hyperevm/monad/megaeth/xlayer/sonic，2026-09-20。

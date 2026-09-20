# Log decoder and quote audit — 2026-09-16

Read-only audit of the current worktree. No network, external model, runtime/config edit, or process change was used. The sole written deliverable is this file. The parent task owns publication and the scoreboard update.

**Conclusion:** the 58 reported impossible sales have a concrete decoder mechanism, not a market-price explanation. Both collectors classify the `LiquidityAdded` topic as a sale. Its `offers` word becomes the seller address, its `quote` address becomes the sold quantity, and its `funds` becomes proceeds. The repeated address is exactly `200,000,000 * 10**18` interpreted as an address. Separately, legacy logs are either dropped or passed to consumers under unrecognized amount field names, and the bot's legacy receipt-price decoder can divide fee by proceeds. Currency and state loss prevent treating the retained lifecycle records as a complete BNB execution dataset.

## 1. Event identities computed from the checked-in ABIs

Computed with `Web3.keccak(text=name + '(' + ','.join(input['type']) + ')')`, using `config/TokenManager2.lite.abi` and `config/TokenManager.lite.abi`. All these ABI inputs have `indexed=false`; the supported static events therefore have exactly one topic. Lengths below are data bytes, excluding topic 0.

| ABI event signature | Topic 0, without `0x` | Data bytes / meaning |
| --- | --- | --- |
| `LiquidityAdded(address,uint256,address,uint256)` | `c18aa71171b358b706fe3dd345299685ba21a5316c66ffa9e319268b033c44b0` | 128; `base, offers, quote, funds`; not a trade |
| `TokenPurchase(address,address,uint256,uint256,uint256,uint256,uint256,uint256)` | `7db52723a3b2cdd6164364b3b766e65e540d7be48ffa89582956d8eaebe62942` | 256; modern purchase |
| `TokenSale(address,address,uint256,uint256,uint256,uint256,uint256,uint256)` | `0a5575b3648bae2210cee56bf33254cc1ddfbc7bf637c0af2ac18b14fb1bae19` | 256; modern sale |
| `TokenPurchase(address,address,uint256,uint256,uint256)` | `00fe0e12b43090c1fc19a34aefa5cc138a4eeafc60ab800f855c730b3fb9480e` | 160; legacy purchase |
| `TokenSale(address,address,uint256,uint256,uint256)` | `80d4e495cda89b31af98c8e977ff11f417bafcee26902a17a15be51830c47533` | 160; legacy sale |
| `TokenPurchase2(uint256)` | `48063b1239b68b5d50123408787a6df1f644d9160f0e5f702fefddb9a855954d` | 32; auxiliary `origin`, no amount/account |
| `TokenSale2(uint256)` | `741ffc4605df23259462547defeab4f6e755bdc5fbb6d0820727d6d3400c7e0d` | 32; auxiliary `origin`, no amount/account |
| `TradeStop(address)` | `8f9ab4bd7eff0d085f91575d50cd83f97aa5258e24ded7630d4fd6739e857132` | 32; graduation/trade-stop marker |
| `TokenCreate(address,address,uint256,string,string,uint256,uint256,uint256)` | `396d5e902b675b032348d3d2e9517ee8f0c4a926603fbc075d3d282ff00cad20` | Dynamic strings; no quote address or decimals |

The hardcoded purchase topic `a78d55aeb92a87db782edde05df51f62cd9c43f9c4ee844147e54d963cd30d37a` matches no event in either local ABI. This audit does not assign it an identity; supporting it requires independent ABI/deployment evidence.

## 2. Critical: liquidity addition is manufactured into a sale

Authoritative layout: `config/TokenManager2.lite.abi:2–31`. Incorrect topic labels: `scripts/backfill_fourmeme_month_raw.py:37–44` and `src/core/listener.py:999–1007`.

The 128-byte compact-trade branches at `scripts/backfill_fourmeme_month_raw.py:182–187` and `src/core/listener.py:1073–1109` read this liquidity event as follows:

| Word | Actual ABI field | Current trade interpretation |
| --- | --- | --- |
| 0 | `base: address` | token address |
| 1 | `offers: uint256` | account = last 20 bytes of `offers` |
| 2 | `quote: address` | amount = numeric address value |
| 3 | `funds: uint256` | cost/proceeds |

With `offers=200000000000000000000000000`, the current decoder produces `0x000000000000000000A56Fa5B99019A5c8000000`, exactly matching the account in `docs/research/20260913-bsc-strategy-foundation/72-market-cap-heat-data-audit-20260914.json:8–12`. That previous artifact reports 58 rows/58 tokens; this subtask reproduced the mechanism, rather than independently recounting those underlying rows.

Deterministic fixture, using `quote=0x55d398326f99059ff775485246999027b3197955` and `funds=1234*10**18`:

```text
Correct event: LiquidityAdded(base, 200000000000000000000000000,
                             0x55d398326f99059ff775485246999027b3197955,
                             1234000000000000000000)
Raw decoder event: TokenSale
Live listener event: TokenSale
False amount: 489982930986835137684486657990555633941558688085
False token_amount after /1e18: 4.899829309868351376844866580e29
False price: 2.518455076618077363865319205e-27
```

Both existing source paths were exercised offline, including the real listener with a supplied block timestamp and real local ABI. If `quote` is the zero address, raw backfill rejects this false trade because amount is zero (`:185`); the listener still emits a zero-amount sale, which the collector then rejects. A nonzero quote creates a positive numeric amount and passes. The token address represented by the quote word must be checked on-chain before concluding which launch quote was used; the fixture itself is not a chain observation.

`DataCollector.on_token_sale` does not exclude graduated lifecycles (`src/data/collector.py:827–890`). It records the fabricated sale, increments seller/volume counts, and puts the tiny value into price history. `_refresh_price_state` accepts any positive price (`:359–374`). This explains why a graduation liquidity event can become an apparent near-total price collapse.

Repair must classify the event from its signature and retain liquidity separately. Deleting prices below a threshold or blocking this one address does not repair event semantics; other `offers` values create different false accounts, and legitimate low-priced trades must remain valid.

## 3. Critical: legacy handling and receipt-price field offsets

The legacy ABI fields are `token, account, tokenAmount, etherAmount, fee` (`config/TokenManager.lite.abi:57–92` and `:107–142`). Correct trade quantities occupy **word 2 and word 3**, not word 3 and word 4.

- **Raw backfill:** the actual legacy topics `00fe…` and `80d4…` are absent from `TOPICS`; `_decode_trade` returns `None` at `scripts/backfill_fourmeme_month_raw.py:167–168`. Both were reproduced. Merely adding these topics would still be wrong: the generic `len(data)>=160` branch at `:171–173` would read `etherAmount` as amount and `fee` as cost.
- **Live listener with the legacy ABI:** ABI fallback correctly decodes the legacy names but forwards `tokenAmount`/`etherAmount` unchanged (`src/core/listener.py:1120–1150`). The collector reads only `amount`/`cost` (`src/data/collector.py:767–770`, `:840–843`), so the legacy trade is rejected as zero amount. This was reproduced. `config/config.py:64` defaults to `config/TokenManager.lite.abi`; the default ABI is therefore relevant, not merely hypothetical.
- **Live listener with only the modern ABI:** legacy topics are unknown and cannot decode; an offline legacy-sale fixture was skipped. Registering handlers called `TokenSaleV1` does not supply an ABI or normalize argument names.
- **Bot receipt price:** `src/trader/bot.py:81–86` explicitly allows the actual legacy sale topic. `_decode_fourmeme_trade_log_price` at `:1973–1979` starts with words 3/4 and only switches to words 2/3 if amount or cost is zero. For a real legacy sale with nonzero proceeds and fee it returns `fee / etherAmount`, not `etherAmount / tokenAmount`. The result feeds `_sell_execution_price_from_receipt` (`:2000–2013`).

The three receipt parsing methods were extracted from the current source AST and run without importing/starting the bot:

| Legacy fixture | Correct quote/token | Current receipt result |
| --- | --- | --- |
| 100,000 tokens, 0.5 quote proceeds, zero fee | `0.000005` | `0.000005` — zero-fee fallback masks the defect |
| 100,000 tokens, 0.5 quote proceeds, 0.005 quote fee | `0.000005` | `0.01` — 2,000 times too high |

The bot also includes the liquidity topic in `FOURMEME_SALE_TOPICS`, but its receipt parser has no 128-byte branch and currently returns `None` for a correctly sized liquidity event. Do not conflate that latent allowlist error with the active 128-byte collector error.

`src/core/processor.py:104–124` also expects only `amount`/`cost`, so raw legacy ABI output loses its quantities there as well.

## 4. Critical to capital comparisons: quote units are erased

`TokenCreate` has no quote or decimal fields in either ABI. Raw backfill never enriches creation records from token metadata (`scripts/backfill_fourmeme_month_raw.py:196–209`, `:301–317`). The collector's creation/metadata whitelists omit quote and decimals even if a caller supplies them (`src/data/collector.py:112–150`, `:258–305`).

Both buy and sale handlers convert **both** quantities with `/1e18`, label quote quantity `bnb_amount`, and use the ratio as price (`src/data/collector.py:767–781`, `:840–854`). Native BNB is 18 decimals; an ERC-20 quote needs its own decimals and exchange rate. An ERC-20 with 18 decimals still is not BNB.

An in-memory collector fixture explicitly supplied a six-decimal quote, 1,000 tokens, and 100 quote units. The collector stored `bnb_amount=1e-10`, `price=1e-13`; the correct values were 100 quote units and `0.1 quote/token`. Neither lifecycle nor metadata retained the supplied quote/decimal fields.

Some within-token percentage returns can remain numerically unchanged when the same missing quote unit is used throughout. That does not validate BNB/USD capital, market-cap thresholds, cross-token volume comparisons, cross-boundary prices, or execution eligibility. The executor explicitly rejects nonzero quote addresses in `src/core/trader.py:329–335` and `:374–381`. Research that selects those tokens cannot automatically represent the current executor's tradable universe.

## 5. State needed for execution was available but discarded

Modern purchase/sale logs have eight words: `token, account, price, amount, cost, fee, offers, funds` (`config/TokenManager2.lite.abi:88–209`). The fast paths keep only token/account/amount/cost and derive a replacement price (`scripts/backfill_fourmeme_month_raw.py:171–187`; `src/core/listener.py:1031–1045`, `:1088–1100`). They discard the original price, fee, and both state quantities. The collector also drops those fields even if a decoded ABI event supplies them.

`cost/amount` is an average transaction ratio; it is not sufficient evidence for the marginal price or output of a different order size. The exact scaling of contract `price`, the before/after semantics of `offers`/`funds`, and whether `cost` includes fee require deployment/implementation validation. Do not infer a constant-product invariant solely from those field names.

Available read-only metadata surfaces:

| Surface | Selector | Available fields and limitations |
| --- | --- | --- |
| Modern manager `_tokenInfos(address)` | `0xe684626b` | 13 outputs: `[0]base, [1]quote, [2]template, [3]totalSupply, [4]maxOffers, [5]maxRaising, [6]launchTime, [7]offers, [8]funds, [9]lastPrice, [10]K, [11]T, [12]status`; ABI `config/TokenManager2.lite.abi:466–543` |
| Legacy manager `_tokenInfos(address)` | `0xe684626b` | Same selector, different 9-output schema: initialized, launchTime, K, T, offers, ethers, tradeEnable, liquidityAdded, tradingHalt; no explicit quote; ABI `config/TokenManager.lite.abi:351–409` |
| Helper `getTokenInfo(address)` | `0x1f69565f` | At helper `0xF251F83e40a78868FcfA3FA4599Dad6494E46034`: version, tokenManager, quote, lastPrice, tradingFeeRate, minTradingFee, launchTime, offers, maxOffers, funds, maxFunds, liquidityAdded (`src/core/trader.py:21–44`) |
| ERC-20 `decimals()` | `0x313ce567` | Query the base token and nonnative quote contract; neither manager info nor helper result declares decimals |

The helper wrapper at `src/core/trader.py:287–303` preserves quote/offers/funds but currently omits fee-rate/min-fee outputs and queries latest state. It does not enrich the collector. Historical research needs explicit block-tagged state, response provenance, manager/version matching, and unknown-state handling; a current balance/reserve cannot establish historical liquidity. The modern ABI also exposes `_tokenInfoExs(address).reserves` (`:442–464`) and template parameters (`:339–381`), but their economic meaning must be verified before use.

`LiquidityAdded` supplies base/quote and liquidity quantities at migration, which can cross-check quote mapping. It does not describe later DEX swap execution or reserve updates. Historical DEX pool identity, token0/token1, decimals, fee, and relevant swap/sync state are still needed after graduation. Using only graduation-enriched tokens as the early candidate universe would introduce survivor selection.

The existing “raw” backfill persists normalized lifecycles and a summary, not the full RPC topics/data (`scripts/backfill_fourmeme_month_raw.py:301–336`). Floats cannot losslessly reconstruct original integers. Recover affected logs by stored transaction hash/block/log index or refetch the range, preserve the original raw response, and build a separately versioned corrected dataset.

## 6. Minimal correct decoder contract

1. Identify `(chain, emitter, topic0, supported ABI/deployment version)`. Build the registry from original ABI signatures; do not guess event identity from payload length or rename ABI events before hashing.
2. Validate exact indexed-topic layout and fixed data length for each supported static event. Current local ABIs require one topic and 256/160/128/32 bytes as above. Reject/quarantine unknown or truncated layouts; do not reinterpret a malformed modern trade as a compact trade.
3. Decode modern trade words by name, retaining `amount`, `cost`, `fee`, `price`, `offers`, and `funds` as integers. Decode legacy `tokenAmount`/`etherAmount` at words 2/3, normalize those names only after successful ABI decoding, and retain fee. Preserve raw ABI version and event name.
4. Decode liquidity into a separate record `{base, offers_raw, quote, funds_raw}`. Decode `TradeStop` as a lifecycle transition and `*2(origin)` as auxiliary metadata; none increments user buy/sell count, volume, or price history.
5. Persist raw event provenance and quantities: manager/emitter, topic(s), data, transaction hash, log index, block number/hash, chain timestamp, decode version, and metadata/state block. Preserve raw logs even when an event is rejected by the strategy.
6. Resolve base decimals and quote address/decimals before economic normalization. Compute `quote_amount = cost_raw / 10**quote_decimals`, `token_amount = amount_raw / 10**token_decimals`, and quote/token VWAP explicitly. Mark unknown quote/metadata unusable for currency-dependent conclusions. Convert to BNB/USD only with a dated conversion source; restrict to verified native quote when modeling the current native-only executor.
7. Preserve fees and state separately from VWAP. Establish contract state and fee semantics before computing executable order-size outputs. Rebuild all dependent labels/statistics after repair; removing only 58 rows is insufficient to restore dropped logs, fees, quote units, and state.

## 7. Regression evidence and required cases

Executed: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.model.test_backfill_fourmeme_month_raw tests.core.test_listener_http_pool` — **40 tests passed**. These passing tests do not validate event identities. `tests/core/test_listener_http_pool.py:1063–1103` explicitly asserts that the real liquidity topic with a synthetic 128-byte payload becomes `TokenSale`; its fixture is semantically wrong. Related tests at `:1036–1061` and `:1105–1150` also label that topic as a sale. The raw-backfill test (`tests/model/test_backfill_fourmeme_month_raw.py:9–34`) covers one modern purchase and casing only.

Required future regression cases, derived from ABI semantics rather than the faulty implementation:

- Recompute every topic from both source ABIs; assert no liquidity/auxiliary signature enters trade allowlists. Leave the unmatched `a78…` unsupported until identified.
- Encode real liquidity with zero and nonzero quote addresses and `offers=200_000_000*10**18`; both must preserve liquidity metadata and produce zero sale records. Include `TradeStop` then `LiquidityAdded` in one transaction.
- Encode modern buy and sale with distinct nonzero price/amount/cost/fee/offers/funds values; assert all raw values survive decoding and collector serialization.
- Encode legacy buy and sale with both zero and nonzero fees; assert correct normalized amounts and receipt price. The 100,000-token / 0.5-proceeds / 0.005-fee fixture must yield `0.000005`, not `0.01`.
- Pass a 128-, 160-, or 224-byte truncated payload under a 256-byte modern signature and incorrect indexed layouts; reject rather than guessing offsets.
- Exercise six- and eighteen-decimal quote assets, non-eighteen-decimal base assets, native zero-address quote, and missing metadata. A legitimate very low price must not be removed solely because of its magnitude.
- Assert quote, decimals, fees, state quantities, ABI version, and provenance survive snapshot/incremental flush and metadata rehydration. Unknown metadata must remain unknown.
- Assert historical helper/manager results use the correct ABI output shape and block tag; quote/state enrichment must not condition the early candidate universe on eventual graduation.

No regression files or runtime fixes were written under this read-only assignment. No scoreboard edit was made by this subtask because it owns only this audit file; these findings materially alter data-validity interpretation and should be integrated into the parent round's scoreboard entry before archive/commit/push.

## 8. Authorized follow-up: bounded offline decoder correction

The parent subsequently authorized implementation in four files under the same active task. Sections 1–7 above preserve the initial audit and its pre-change line references; this section records the resulting correction. No network request, external coding model, git commit, runtime listener/collector/bot edit, config/env edit, or collection-process change was made.

Files changed:

- `src/data/fourmeme_log_decoder.py` — new shared offline decoder. Its nine-event registry is computed from the two checked-in ABIs, and only the four actual modern/legacy trade signatures enter `TRADE_TOPICS`. Static payloads require the exact ABI length and one topic; ABI decoding validates address padding. Dynamic creation payloads must round-trip to their canonical encoding. Unknown signatures, malformed data, and invented indexed layouts return `None`.
- `scripts/backfill_fourmeme_month_raw.py` — removed guessed word offsets and the incorrect topic table. `_decode_log` delegates to `decode_fourmeme_log`; `_decode_trade` exposes only actual `TokenPurchase`/`TokenSale` events. Existing tuple and lowercase token-key contracts remain. Liquidity and auxiliary events retain separate names and do not enter the existing trade handlers.
- `tests/model/test_fourmeme_log_decoder.py` — 13 tests verify the registry against both local ABIs, exact known signatures, modern integer fields, legacy nonzero/zero fees, native/ERC-20 liquidity, auxiliary events, creation/stops, byte/hex inputs, malformed lengths/layouts/padding, unknown topics, and a valid very small price.
- `tests/model/test_backfill_fourmeme_month_raw.py` — 3 tests, including two new regressions. The full offline backfill fixture contains a create, modern purchase, real legacy sale, origin event, stop, and ERC-20 liquidity event. The persisted lifecycle contains exactly one real sale and two trade price points; the liquidity event creates neither a seller nor a price point.

`decode_fourmeme_log(log)` returns `(event_name, args)` or `None`. It preserves original integer `price`, `fee`, `offers`, `funds`, and trade quantities. Legacy `tokenAmount`/`etherAmount` survive alongside correct `amount`/`cost` aliases. Liquidity returns `{base, offers, quote, funds}` and never invents account/amount/cost. Token/base/quote addresses are lowercase; account/creator addresses remain checksummed. No ratio, quote currency, or price floor is inferred.

Test progression:

1. Before implementation, the new backfill test reproduced the phantom account in the persisted sale. The liquidity tests observed `TokenSale` for nonzero quote and a dropped native-quote event; the new decoder import was initially absent.
2. The 13 new decoder tests passed after adding the shared module.
3. After backfill integration, an exact float assertion exposed the existing collector's `99999.99999999999` representation of 100,000 tokens. That integration assertion now uses numerical tolerance; decoder raw-integer assertions remain exact. The collector was not changed.
4. Final command: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.model.test_fourmeme_log_decoder tests.model.test_backfill_fourmeme_month_raw tests.core.test_listener_http_pool` — **55 tests passed** (16 decoder/backfill tests plus 39 unchanged listener tests). No whitespace diagnostics were emitted by the four scoped `git diff --no-index --check` checks.

The parent's saved public RPC response in `liquidity-logs-rpc.json` was decoded locally with the new module, without another network call:

```json
{
  "event": "LiquidityAdded",
  "args": {
    "base": "0x07dcaccade1e3c034c707039bdfe02792f88ffff",
    "offers": 200000000000000000000000000,
    "quote": "0x46ceefda28dd7207059ed19b0acdc026955bb15c",
    "funds": 636999999999921599766
  },
  "blockNumber": "0x70026d8",
  "logIndex": "0xf1",
  "transactionHash": "0xcb992be33532cd684ffc2dbf3726fc997b71a4bda9266a3b155ec872d49f07f2"
}
```

Local review found no remaining critical issue in this bounded offline change. Material limitations remain outside its ownership: the live listener and bot receipt decoder still have the earlier defects, the existing collector still discards state/quote fields and assumes 18 decimals, and historical lifecycle/research artifacts have not been rebuilt by this subtask. The shared decoder preserves fields at its output boundary; it does not claim that unchanged collector persistence retains them. The parent task remains responsible for full-suite verification, scoreboard closeout, local archive, and any Git action.

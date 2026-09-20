# Heat-source capability matrix (2026-09-14)

This matrix separates a source's **event time** from the time at which this
process could have observed it. A source is not replayable for trading merely
because it exposes a historical field: replay needs an append-only local
capture of the response and its receive time.

| Source | What it can signal | Source time quality | Identity | Expected live delay | Historical/replay status | Cost/limits | Role in the redesign |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BSC Four.meme contract events | Token creation, buys, sells, trade stop; wallet waves and creator behavior | Block timestamp plus block/log/tx provenance; this is the strongest causal clock available locally | Token, wallet, tx, block, log | Existing listener is sub-block polling plus provider/RPC lag; measure local receive time before using a delay assumption | Fully replayable from lifecycle data, but old rows do not contain local receive time | RPC/provider limits and reorg/duplicate handling | Primary event clock and control strategy |
| Derived creator/narrative/launch-intensity events | Repeated symbol/name bursts, creator launch rate, same-second clusters | Inherited from chain events; no external social claim | Token, creator, normalized narrative key | Same as chain listener | Replayable from lifecycle data if the derivation is versioned | False clusters, symbol collisions, creator sybils | Cheap causal context, never a standalone buy trigger |
| DEX Screener pair/token endpoints | Pair price, m5/h1/h6/h24 volume and buys/sells, liquidity, pair creation | `pairCreatedAt` is useful for pool age; rolling stats are a current snapshot, not an event history | Token, pair, chain, DEX | HTTP poll latency plus aggregator refresh; record request and receive times | Current snapshots can be archived going forward; no local historical snapshot stream yet | Docs describe 300 requests/min for pair/token endpoints | Post-launch liquidity/flow confirmation and exit health |
| DEX Screener profiles, boosts, takeovers, trending metas | Social/profile edits, paid boost activity, community takeover, narrative meta heat | Takeovers/ads expose a source date; profile/boost payloads do not expose a dependable event timestamp | Token and chain, sometimes a meta slug; global endpoints mix chains | Smoke test through the proxy returned in about 0.9-1.1s, excluding polling interval | Not replayable from the current API alone; own append-only capture required | Docs describe about 60 requests/min; latest lists are mixed-chain and can contain only a few BSC rows | Online discovery/attention feature, never a historical label until archived |
| GeckoTerminal BSC pools/trending/OHLCV | New pools, liquidity, volume, transactions, OHLCV | `pool_created_at` is a pool clock; m5/m15/m30/h1/h6/h24 fields are as-of snapshots | Pool, token relationships, DEX; no wallet identity | Smoke test through the proxy returned about 0.3-1.1s, excluding poll interval | OHLCV can be fetched retrospectively, but that does not reproduce what was visible at entry; archived snapshots are needed for causal replay | Public API is beta/free and docs describe roughly 10 calls/min | Market continuation and liquidity-risk confirmation; use a separate source class |
| GMGN smart-money/copy-trade | First buyers, insiders, smart-money labels and alerts | Provider-specific; the public tutorial does not define a local historical event clock | Wallet/token when the cooperation feed supplies them | Unknown until an approved feed is measured | No local historical export; treat as live-only until a contract/API capture is available | Cooperation/API access and third-party data dependencies | Optional shadow source, never a backtest input yet |
| X filtered stream | Posts mentioning a contract, ticker, launchpad, or curated account | Post `created_at`, author ID, post ID, edit history; local receive time must be added by the collector | Post, author, conversation, URL, extracted token | X docs state filtered-stream P99 is about 6-7 seconds; Powerstream is lower latency but Enterprise-only | Recent search covers 7 days; full archive is pay-per-use/Enterprise. Neither recreates delivery latency without our archive | X docs list pay-per-use pricing of $0.005 per post read and a 3M monthly post-read cap for pay-per-use; filtered-stream rules/connections are tiered | Earliest narrative/attention discovery; verify with chain before any order |
| Aggregate BSC market regime | Chain-wide DEX volume and regime changes | Daily provider timestamp; deliberately lag by one day | Chain, date | Minutes to hours | Historical daily series is replayable, but not token-level | Provider/API limits and aggregate scope | Regime gate only; never an entry signal |

## Current endpoint evidence

The proxy smoke test is saved in
`docs/research/20260913-bsc-strategy-foundation/38-heat-source-api-smoke-20260914.json`.
All seven tested endpoints returned HTTP 200 on 2026-09-14:

- DEX Screener profiles, boosts, takeovers, and trending metas returned 30,
  30, 20, and 18 records. The lists were global: the field sample contained
  BSC, Solana, Ethereum, and Robinhood records.
- A generic DEX Screener search for `BNB` returned 30 Solana pairs. A BSC
  strategy must resolve a known token address and call the chain/token endpoint;
  it must not treat a free-text search response as a BSC feed.
- GeckoTerminal BSC new-pool and trending-pool endpoints returned 20 records
  each and exposed `pool_created_at`, reserve, volume, transaction counts, and
  m5/m15/m30/h1/h6/h24 fields. These are current snapshots.

The field-level sample is in
`docs/research/20260913-bsc-strategy-foundation/39-heat-source-field-sample-20260914.json`.
The requests used `LOCAL_PROXY_URL` from `.env`; no trading or runtime setting
was changed.

## X-specific conclusion

X is useful as a **discovery clock**, not as proof that a token is investable.
The official X pages fetched for this round document recent search (last seven
days), full-archive search for paid/Enterprise access, post fields such as
`created_at`, `author_id`, and `public_metrics`, and filtered-stream delivery
with approximately 6-7 seconds P99 latency. Powerstream is a premium,
lower-latency option. The fetched pages are preserved as files 46 and 47 in
this directory.

The practical implication is to archive every received post with both
`source_event_time=created_at` and `observed_time=local receipt time`. A post
retrieved later by recent search is a reconstructed observation and must not be
used to claim a six-second live edge. The post ID, author, query/rule ID, text
hash, token extraction result, edit history, and response hash are required for
deduplication and later replay.

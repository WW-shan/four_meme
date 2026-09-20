# X crypto heat leaderboard research (2026-09-14)

## Answer to the KOL-post question

A single post by a large account should be treated as an **attention shock**.
It is valuable for discovery because it may precede DEX aggregation, but its
follower count is not enough evidence to buy. The account may be paid,
compromised, copied, or referring to a ticker shared by several contracts. The
right unit is an event with a token mapping and a measured chain response.

The leaderboard should therefore rank **actionable heat**, not popularity:

1. **Address specificity** — exact BSC contract in the post or an official
   link is stronger than a ticker or hashtag. A ticker-only item remains a
   narrative row and cannot enter the buy queue.
2. **Source quality** — maintain a time-decayed, out-of-sample record for each
   author: valid contract mapping rate, unique-token hit rate, median chain
   response, false/edited-post rate, and post-entry cost-adjusted outcomes. Do
   not rank by followers alone.
3. **Originality and independence** — original posts, quote posts, and replies
   are separate classes. Count independent authors and independent URLs rather
   than raw retweets. One hundred copies of one post is one source cluster.
4. **Propagation velocity** — measure new unique authors, replies, quotes, and
   links per minute relative to that author's own baseline. Raw likes are
   delayed and easy to buy.
5. **Chain response** — after the X event, measure BSC creates/buys, unique
   buyers, signed flow, implied curve market-cap change, and creator/cluster
   selling in the next 15/30/60 seconds.
6. **Market accessibility** — apply the approximate implied curve market-cap
   band, liquidity/slippage checks, and a late-extension penalty. A hot post
   with no executable market is a watch item, not a trade.

The board should show these components separately. A single opaque score makes
it too easy for a viral but unsafe post to hide a weak chain response.

## Proposed board rows

Each rolling 5-minute and 15-minute board row should contain:

`token`, `chain`, `first_source_time`, `last_observed_time`, `source_count`,
`independent_author_count`, `trusted_author_max`, `original_post_count`,
`new_author_rate`, `engagement_velocity_z`, `contract_specificity`,
`post_cluster_id`, `implied_mcap_usd`, `mcap_change_30s`, `buyers_30s`,
`buy_count_30s`, `signed_imbalance_30s`, `sell_pressure_30s`,
`creator_sell_flag`, `mapping_confidence`, `latency_p50/p90`, and
`risk_flags`.

Use three states instead of a buy/no-buy score:

- `narrative_only`: X heat exists but no unambiguous contract.
- `mapped_unconfirmed`: a BSC contract is mapped, but the chain has not
  responded or the response is too concentrated.
- `actionable_watch`: X heat plus causal on-chain response and executable
  liquidity. This is the only state eligible for a separate shadow replay.

## Existing products and what they contribute

| Product/source | Relevant capability | Free/replay limitation | Decision |
| --- | --- | --- | --- |
| X official API | Filtered Stream, recent/full search, author/post fields and timestamps | Recent search is seven days; full archive and low-latency Powerstream require paid access; pay-per-use reads are billed | Use as an optional live discovery feed once authenticated; archive every receipt |
| DEX Screener | Profiles, boosts, takeovers, trending metas, pair volume/liquidity | Public endpoints are rate-limited and latest lists mix chains; profile/boost payloads lack a dependable event timestamp | Free corroboration/snapshot source, not historical X heat |
| GeckoTerminal | BSC pools, OHLCV, liquidity, volume, transaction counts and trending pools | Public beta/current snapshots; low public call rate; no X identity or wallet provenance | Free market confirmation source |
| GMGN | Real-time smart-money/KOL activity, first buyers, snipers, insider labels, BSC market/trending API | Requires API credentials for Agent API; labels are third-party and docs warn they are not guaranteed realtime; no local historical export | Optional source-specific shadow feed, not a current backtest label |
| LunarCrush | Social volume, creators, topics, sentiment, trending and market metrics | Official docs state free Hobby is market-data only; social/creator/AI endpoints require paid tiers | Good reference for the desired product shape, not a zero-cost dependency |
| Santiment | Social volume, unique social volume, sentiment, trending stories/words, on-chain metrics | GraphQL API and historical/realtime access are plan-gated; no free local X event archive | Research comparison; do not make runtime dependency |
| Kaito | Attention economy/mindshare framing and crypto information ranking | Public docs fetched here describe the concept, not a freely replayable API contract | Conceptual benchmark, not a data source yet |
| Birdeye | Token/pair/wallet/holder/security/smart-money/discovery data | Requires account/API key and package/rate limits; public docs do not establish a free historical BSC X feed | Optional market intelligence source, not required for the free path |

Evidence files for the product comparison are `46-*`/`47-*` (X), `51-*`/`52-*`
(GMGN), `53-*`/`54-*` (LunarCrush), `55-*`/`61-*`/`62-*` (Santiment),
`56-*`/`57-*` (Kaito), `58-*` (DEXTools), `59-*` (Birdeye), and `60-*`
(DEX Screener). Empty or marketing-only fetches are treated as unverified
capabilities rather than proof of a free API.

## Zero-cost implementation path

The free path should not depend on an X commercial stream at first:

1. Keep the existing BSC listener as the exact event clock and collect all
   lifecycle/heat events locally.
2. Use the free DEX Screener/GeckoTerminal public endpoints only as rate-limited
   snapshots, with proxy, request time, response hash, and no fabricated
   historical timestamp.
3. If an authenticated X feed is later available, ingest a small curated set of
   accounts and explicit contract-address rules first. Do not scrape all of X.
4. Build the leaderboard and archive events for 7-14 days in shadow mode. A
   historical board reconstructed from a later search response is useful for
   narrative analysis but cannot claim the original entry latency.

The board should be a monitoring and replay surface before it becomes a buy
surface. Its first acceptance test is incremental lift versus the matched
on-chain-only board, not a high social-score hit rate.

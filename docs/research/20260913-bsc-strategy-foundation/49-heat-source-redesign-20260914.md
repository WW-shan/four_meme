# Heat-source-first BSC redesign (2026-09-14)

## Decision

The next system should be an event-driven **discovery -> verification ->
heat-confirmation -> stateful hold/exit** pipeline. The old abstraction asked
one generic model to predict every token's return from a small, rapidly shifting
sample. The available evidence rejects that as the core: the fresh 31-day
ranker, delayed DEX confirmation, and repeat-early-buyer probe all failed sealed
final or stress gates. The leader/follower probe alone produced 5,493 candidates
but every final horizon/delay portfolio was negative.

X can improve the first stage because a good post may precede the DEX aggregate
listing. It does not remove the need for a chain-side confirmation. A post can
be paid promotion, a copied address, a stale ticker, or a coordinated exit. AI
should extract and classify the post; deterministic chain and execution checks
must decide whether an order is even eligible.

## Runtime state machine (future shadow implementation)

1. **Discovered** — an X filtered-stream event (or another attention source)
   mentions a BSC contract, a unique token URL, or a curated narrative. Store
   the raw response reference and hashes. Do not buy.
2. **Mapped** — resolve one checksum-normalized BSC token address. Reject a
   ticker-only match, an ambiguous address, a non-BSC address, an edit that
   changes the address, and an address that cannot be matched to a Four.meme
   create event or known pool.
3. **Verified** — run cheap deterministic checks before an AI call: original
   post rather than a retweet, author/rule allowlist, post age, duplicate
   content, contract checksum, creator history, and basic token safety. AI is
   only an extractor/classifier for ambiguous text and must return a versioned
   JSON result with confidence and evidence spans.
4. **Heat-confirmed** — require independent on-chain evidence in a short
   predeclared window: at least a spread of unique buyers, non-declining net
   flow, bounded entry slippage, and no immediate creator/cluster sell. Use the
   existing exact block/log timestamps. DEX Screener/GeckoTerminal snapshots can
   confirm liquidity and continuation, but cannot replace this clock.
5. **Probe/hold** — in shadow replay, enter after an explicit execution delay.
   A future canary may use a small fixed probe and add to a position only when
   heat remains alive. Do not size from AI confidence or from follower count.
6. **Decay/exit** — exit when the chain flow turns negative, liquidity/volume
   collapses, X attention stops accelerating, or price drawdown crosses a
   predeclared barrier. A long hold is conditional on continued heat; it is not
   a blind 3-day hold. A maximum hold is still required for stale feeds.

## AI and X boundary

The useful split is:

- **X API** supplies the event stream and timestamps. Filtered Stream is the
  normal starting point; Powerstream is an optional premium latency upgrade.
- **AI** normalizes text, extracts a contract/address or narrative key, scores
  whether the post is an announcement, copy, question, or promotion, and
  returns structured evidence. It does not authorize a trade.
- **On-chain rules** decide whether the claim has a live market response.

Use a two-speed path to protect the edge: parse obvious addresses and reject
bad chain IDs locally in milliseconds; call AI only for unresolved text or
conflicting signals. If the total receive-to-eligible decision exceeds a
predeclared limit (initially 30 seconds for a research grid), record the event
but do not pretend it was an early entry. Every decision stores source time,
receive time, parse start/end, chain confirmation time, and submitted/assumed
execution time.

## Minimum X confirmation contract

The first shadow grid should test, without selecting on the sealed final set:

- source class: curated accounts, contract-address mentions, narrative-only
  posts, and paid/profile signals separately;
- post-to-chain windows: 15s, 30s, 60s, and 180s;
- entry delay: the observed p50/p90 receive-to-chain-confirmation delay plus
  3s and 10s execution controls;
- hold horizons: 5m, 1h, 6h, and 24h, with a conditional heat-decay exit;
- controls: the same token's on-chain-only signal, matched by time and token
  opportunity; a future post-retrieval control is invalid;
- costs: current fee/slippage assumptions plus a harsh latency/slippage stress.

The objective is not post classification accuracy. The primary result is the
paired, cost-adjusted capital delta versus the on-chain control, along with
false-mapping rate, median/p90 latency, drawdown, and the fraction of signals
that remain actionable after confirmation.

## Data contract

`src/pipeline/heat_event_schema.py` defines schema version 1 and
`scripts/build_heat_event_replay.py` exports reconstructed chain events to
append-only JSONL. The smoke export in the task directory produced 491 events
from 100 latest-window tokens. Reconstructed rows intentionally leave
`observed_time` and latency empty. A live X/DEX/Gecko adapter must populate
those fields on receipt; it must not backfill them from a provider's current
response.

Required fields for a live event are:

`source`, `event_type`, `token`, `wallet` where applicable,
`source_event_time`, `observed_time`, `latency_seconds`, `block_number` /
`log_index` / `transaction_hash` where applicable, `evidence_url`, payload
hash, raw-payload reference, and adapter/version identifiers. The event ID and
dedupe key must be stable across reconnects and must exclude observation time
from the source-event dedupe identity.

## Acceptance gates before any canary

1. Collect at least 7-14 continuous days of X plus chain events with zero
   timestamp-order violations and a measured receive-latency distribution.
2. Keep X-only, chain-only, DEX-only, and combined replay reports separate.
   No combined score is allowed until an individual source has a positive
   paired delta in an independent final window.
3. Require at least 30 final token opportunities and preferably 50+ completed
   exits; report token-level bootstrap intervals rather than only a median.
4. Positive validation, sealed final, expanding walk-forward, moderate-cost and
   harsh-cost replay are all required. A single 10x winner cannot waive these
   gates; it is recorded as a moonshot diagnostic and evaluated with realistic
   slippage and missed-entry probability.
5. Require false token mapping below 1%, p90 decision latency below the tested
   entry window, no future-field leakage, and a non-negative paired delta after
   removing the best single trade.
6. Only after the shadow gates pass may a small canary be considered. Keep
   `ENABLE_TRADING`, model paths, sizing, and the current bot unchanged during
   this research node.

## What this changes in the project

- Keep the lifecycle collector and exact timestamp validation as the source of
  truth.
- Add append-only adapters for X, DEX Screener, and GeckoTerminal using the
  configured proxy, with reconnects, rate-limit backoff, raw-payload hashes,
  and explicit observed times.
- Replace the single generic token-return target with source-specific event
  replays and a causal state machine. A model may be added later as a
  meta-ranker after a source has demonstrated lift; it is not the first step.
- Add a matched on-chain-only control and report every source's incremental
  contribution. This is the only way to tell whether X found earlier heat or
  merely added a correlated label.

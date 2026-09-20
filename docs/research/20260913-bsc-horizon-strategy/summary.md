# BSC Entry Horizon and Profit Objective Research

Date: 2026-09-13 (Asia/Shanghai)

## Scope

The replay starts at the first entry-time activity gate and never filters on
`graduated` or uses `TradeStop` as an exit. It uses chain-second price history,
3-second entry/exit delays, 100 bps fee, 200 bps slippage, and a fixed 0.1 BNB
stake for the equal-stake profit totals. A path that ends before the requested
horizon is reported separately as censored; the strict pass scores it as a full
loss.

The month window contains 199,706 deduplicated lifecycle tokens and 3,022
default-gate entry candidates (3 buyers, 5 buys, age <=300s). The timestamp
audit found no malformed, future, pre-creation, or out-of-order trade times in
the sampled and latest-window checks; persisted rows do not carry block/log
provenance, so raw timestamps remain the chronological source of truth.

## Results

At a +20% target and -30% stop, the last-observation censor policy gives
conservative equal-stake totals of -25,065.9% (1h), -28,040.2% (3h),
-29,252.9% (6h), -30,035.4% (12h), -30,682.2% (24h), -31,203.4% (48h), and
-31,313.8% (72h). The strict full-loss censor policy is materially worse. A
target/stop grid selected +20%/-50% on validation, but its final totals remain
 negative for every horizon. The purge-aware companion is
`36-target-barrier-profile-purged-loss.json`; its selected barriers and gate
rules are also negative in final. The gate grid over buyers, buy count, and entry age
also remains negative in its final split; validation selection must not be
promoted to runtime settings.

The latest monthly window has substantial path censoring: complete candidates
fall from about 1,691 at 1h to 648 at 72h. This is why a long-horizon mean based
only on complete paths is not sufficient evidence. A last-observation positive
result can be a capture-boundary artifact.

The validation-to-final collapse is measurable drift, not a single mysterious
model failure. For the strict 6h/+20%/-30% label, validation target-first rate
is `41.6%` versus `32.9%` in final; complete paths are `369/604` versus
`250/605`. The classifier's selected score count falls from `56` validation
rows at `p>=0.7` to `6` final rows, and the ranker's selected count falls from
`37` to `1`. Validation positive results are also selection-unstable: a
bootstrap interval for the ranker's validation return sum spans roughly
`-65.7%` to `+777.9%`. The final classifier's bootstrap probability of a
positive sum is below `0.1%`, while the final ranker has only one trade. Daily
activity and feature distributions shift sharply, especially
`buy_volume_slope_30_60`, so the evidence supports both threshold/model
overfitting and a market/path-coverage regime change. The full diagnostics are
in `33-validation-final-drift-audit.json`.

The strict re-run also purges one full label horizon at each split boundary.
That removes `100` training candidates and `22` validation candidates for the
6-hour experiment. The classifier then selects no trade on validation, while
the ranker makes `+0.2609` BNB on 12 validation trades and `-0.1000` BNB on one
final trade. The earlier unpurged validation gains were therefore partly
boundary/threshold selection optimism; purging does not recover final profit.

Fresh models were trained from decision-time features rather than the previous
short-horizon model. The barrier classifier predicts target-before-stop; the
profit ranker predicts signed-log cost-adjusted return and is evaluated by
top-k cumulative profit and a separate cash-constrained portfolio replay. For
6h/+20%/-30% with 1 BNB starting equity, 0.1 BNB stakes, and at most 8 open
positions, the new classifier's validation profit was `+0.1380` BNB but final
profit was `-0.4649` BNB; the new profit ranker's validation profit was
`+0.3981` BNB but final profit was `-0.1000` BNB. The full horizon grid had no
model with positive validation and final profit. The earlier final top-50
ranker figure (`+144.8%`) was an unconstrained last-observation diagnostic, not
an executable portfolio result. These are research artifacts, not live
candidates.

The most stable feature importance in the exploratory runs was price momentum,
followed by 30s volume and recent sell pressure. Univariate gates such as the
90th percentile of `buy_volume_slope_30_60` look positive on validation but
turn negative in the final split. This is evidence to keep these fields in the
next model, not a static veto rule.

The fixed `+20%` upper barrier is also not a suitable sole target for the
user's long-runner goal: it teaches the model that a roughly 20% exit is a
success and clips multi-x winners. An `uncapped_horizon` signed-log return
experiment was added; under strict full-loss censoring it performed worse on
this window, which means the objective is conceptually better aligned but the
data does not yet support it.

## Current market context

Fetched sources (the broad xAI search endpoint timed out and Zhipu was not
configured, so no unsupported search synthesis was used):

- DefiLlama BSC page (`https://defillama.com/chain/BSC`) showed about $1.258B
  24h BSC DEX volume, $11.068B over the displayed 7d period, 1.94M active
  addresses and 476k new addresses. These values are a current snapshot, not a
  guarantee of meme-token profitability.
- BNB Chain's official `BNB Trenching Szn is Back` post
  (`https://www.bnbchain.org/en/blog/bnb-trenching-szn-is-back-win-a-share-of-200k-on-flap-and-four-meme`)
  documents a 200,000 USDT campaign split between Flap volume activity and a
  Four.meme 10-day PnL competition. This supports current attention/liquidity
  but also signals incentive-driven and crowded flow.
- BNB Chain's `BSC Payment Lane` post
  (`https://www.bnbchain.org/en/blog/bsc-payment-lane-reserved-blockspace-that-keeps-payments-moving`)
  says swaps and MEV ordering remain unchanged; congestion can still affect
  execution timing. The replay therefore keeps explicit delay and cost stress.
- DEX Screener API documentation
  (`https://docs.dexscreener.com/api/reference`) exposes token/pair liquidity,
  volume, transaction counts, price changes, and pair timestamps. Those fields
  are suitable future live features, but this lifecycle window does not contain
  a complete DEX history for every candidate.

## Decision

Keep the runtime model, `.env`, position sizing, and live enablement unchanged.
Use `scripts/profile_target_barriers.py` and
`scripts/train_entry_profit_ranker.py` for the next independent window. A live
switch requires positive final cumulative profit under strict censor handling,
enough complete paths, target/stop execution stress, and a predeclared trade
count/capital cap. `docs/model_scoreboard.md` was updated with this round.

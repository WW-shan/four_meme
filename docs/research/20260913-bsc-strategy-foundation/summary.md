# BSC strategy foundation redesign (2026-09-14)

> **Superseded economic interpretation, 2026-09-16.** Raw price ratios and test
> passes below do not establish executable profit or loss. A complete quote
> audit found 597/615 heat candidates nonnative, while runtime only supports
> native BNB; all 22 claimed executable observations were nonnative. The
> liquidity-as-sale decoder and several execution/capital assumptions were also
> incorrect. See [the current report](../20260916-bsc-profitability-evidence/recommendation.md)
> and `evidence-status.json`. Historical results remain for provenance only.

## Evidence

- The fetched Four.meme page describes itself as a BSC meme fair-launch platform: `docs/research/20260913-bsc-strategy-foundation/14-fetch-fourmeme-20260914.md`.
- DefiLlama's fetched BSC DEX overview reports about `$789.8M` aggregate 24h volume and `$10.76B` over 7 days. The daily series peaked around `$2.31B` on September 8 and fell to about `$691.0M` on September 13; this is a chain-wide regime measure, not Four.meme-only volume: `docs/research/20260913-bsc-strategy-foundation/18-bsc-market-metrics-20260914.json`.
- GeckoTerminal's fetched API page documents a public rate limit of roughly 10 calls/min and identifies the service as an on-chain DEX tracker: `docs/research/20260913-bsc-strategy-foundation/15-fetch-gecko-api-20260914.md`.

## Data corrections

- The latest 31-day lifecycle window is `data/training/latest_bsc_20260914_31d_final`: `234,933` deduplicated tokens, `0` malformed rows, and `129` graduated tokens.
- The rate-limited GeckoTerminal collection (as-of `2026-09-13T16:49:36Z`) found `127/129` graduated token paths and `12,630` post-graduation hourly bars. The lifecycle window is newer (as-of `2026-09-13T17:38:04Z`); the one-hour as-of difference is recorded rather than hidden. Two tokens had no curve/DEX overlap.
- `scripts/repair_post_graduation_bridges.py` removes pre-graduation pool bars, restores curve quotes at the graduation second, and rebuilds bridges from raw bars. `scripts/validate_post_graduation_ohlcv.py` reports `0` violations in `docs/research/20260913-bsc-strategy-foundation/19-dex-bridge-validation-20260914.json`.
- The collector now throttles all workers, retries rate limits, checkpoints partial output, and resumes only successful rows. The bridge remains explicitly approximate because USD/native quote paths are scaled at the boundary.

## New protocol

`src/pipeline/tail_capture_strategy.py` and `scripts/run_tail_capture_training.py` implement an independent protocol:

1. Generate every qualifying early buy event (or a declared one-event-per-token control), using only events at or before the decision second.
2. Join curve and post-graduation DEX paths, and train on complete paths only. Incomplete/idle paths remain in capital replay as explicit last-observation risk.
3. Fit a CatBoost ranker on log wealth (with an optional MFE diagnostic) in hourly cross-token competition groups. The old token-group objective only ranked events within one token and could not learn capital competition.
4. Add causal regime, creator-history, narrative-symbol, launch-intensity, and flow-quality features. Daily BSC volume is shifted one day so the model cannot see a same-day closing aggregate.
5. Replay fixed 1h/6h/24h/72h holds with 3-second entry/exit delay, 1% fee, 2% slippage, 0.1 BNB stakes, 8-position capacity, and moderate/harsh cost stress. A separate 40% peak-drawdown trailing target was also tested.

## Results

The reviewed final protocol report is `docs/research/20260913-bsc-strategy-foundation/29-tail-capture-reviewed-20260914.json`, with fresh artifacts under `data/models/20260914_tail_capture_v10_reviewed`.

| Horizon | Complete path rate | Final model replay | Final complete-only replay | Harsh final | Expanding walk-forward |
|---|---:|---:|---:|---:|---:|
| 1h | 57.1% | 0 trades / 0.0000 BNB | 0 trades / 0.0000 BNB | 0.0000 BNB | all 3 folds negative |
| 6h | 46.8% | 0 trades / 0.0000 BNB | 0 trades / 0.0000 BNB | 0.0000 BNB | all 3 folds negative |
| 24h | 35.0% | 32 trades / -0.9476 BNB | 17 trades / -0.1176 BNB | -0.9029 BNB | all 3 folds negative |
| 72h | 24.8% | 2 trades / -0.0623 BNB | 0 trades / 0.0000 BNB | -0.0816 BNB | all 3 folds negative |

The model reduced the loss of the earliest-signal baseline in several horizons, but no horizon passed the predeclared minimum-trade, complete-path, stress, and walk-forward gates. The 24h validation uplift came from a small set of complete paths and reversed in final. The 40% trailing target also failed final and stress replay (`docs/research/20260913-bsc-strategy-foundation/28-tail-capture-trailing40-final-20260914.json`). The latest 7-day control briefly produced `+0.2708 BNB` in base 1h final replay, but harsh stress was `-0.4388 BNB` and two of three walk-forward folds were negative (`docs/research/20260913-bsc-strategy-foundation/30-tail-capture-latest7d-reviewed-20260914.json`); it is rejected as a small-sample regime artifact.

The staged DEX-confirmation path in `src/pipeline/dex_confirmation_strategy.py` waits for post-graduation bars before entering. Its 24-rule grid selected three bars, an eight-hour window, prior-day market ratio `0.5`, price retention `-50%`, and volume retention `0.05`; validation was `-0.2433 BNB` and final was `-0.3304 BNB` (`docs/research/20260913-bsc-strategy-foundation/32-dex-confirmation-grid-20260914.json`). The fresh ranker on that path produced only two final 6h trades for `-0.0008 BNB`, no 24h final trades, and `-0.3468 BNB` at 72h; it failed all acceptance gates (`docs/research/20260913-bsc-strategy-foundation/33-dex-confirmation-selected-20260914.json`).

The first rule-based leader/follower probe also failed as a standalone edge: 5,493 leader candidates were replayed across 3s/10s/30s/60s follower delays and 1h/6h/24h holds; every final portfolio result remained negative (`docs/research/20260913-bsc-strategy-foundation/34-leader-follow-profile-20260914.json`). The local lifecycle data can identify repeat early buyers, but that is not enough to establish a profitable leader.

External heat sources are now clearly separated by replayability. DEX Screener's fetched API documentation exposes latest token profiles, community takeovers, ads, boosts, trending metas, pair liquidity/volume, and token-pair endpoints (`docs/research/20260913-bsc-strategy-foundation/35-fetch-dexscreener-api-20260914.md`). GeckoTerminal supplies realtime/REST OHLCV and liquidity (`36-fetch-gecko-api-guide-20260914.md`). GMGN explicitly advertises smart-money/copy-trade and insider/first-buyer tools, but its fetched docs point to a cooperation/API flow rather than a local historical export (`37-fetch-gmgn-docs-20260914.md`). Historical joins for these attention sources are still missing.

The X source was evaluated as a possible earlier attention clock. Official X docs state that recent search covers seven days, full-archive search requires pay-per-use/Enterprise access, post fields include `created_at`, `author_id`, and public metrics, and Filtered Stream delivers with approximately 6-7 seconds P99 latency. Powerstream is lower-latency but premium. The current `.env` has no X Bearer Token, so no live X feed was enabled. Proxy smoke tests of DEX Screener and GeckoTerminal returned HTTP 200, but the lists were mixed-chain/current snapshots rather than historical event streams (`38-heat-source-api-smoke-20260914.json`, `39-heat-source-field-sample-20260914.json`).

The first free-first market-cap probe was later found invalid: its horizon exit lookup used future events without an upper bound, and 58 synthetic graduation-system prices were present in the raw path. The corrected per-candidate audit (`70-market-cap-heat-failure-attribution-20260914.json`) separates snapshot and executable outcomes. Only 22 of 615 `$50k` activity+flow candidates had a timely 1-hour exit; 3 were negative and 19 positive. The detailed audit is `73-market-cap-heat-failure-audit-20260914.md`. The X leaderboard research treats one KOL post as an attention shock and requires contract mapping, independent propagation, and chain response before an actionable state (`68-x-heat-leaderboard-research-20260914.md`).

## Decision

- No `.env`, `MODEL_DIR`, position sizing, trading enablement, bot behavior, or live process changed.
- No model is safe for live switching. The evidence supports a regime-aware **abstain** state when the recent volume regime is weak, not a claim that a 3-day blind hold is profitable.
- The next research requirement is richer historical attention/holder/liquidity data and a longer independent collection window. The staged DEX-confirmation route is also rejected for this window; further barrier, threshold, or confirmation sweeps would be optimizing noise.
- The redesign is now a source-specific discovery/verification/stateful-hold pipeline. X may discover attention, but only a matched on-chain confirmation can make a candidate actionable. AI is limited to structured post extraction/classification; it cannot authorize a buy. The capability matrix and replay contract are in `48-heat-source-capability-matrix-20260914.md` and `49-heat-source-redesign-20260914.md`.
- `docs/model_scoreboard.md` was updated with this rejection and the data/label corrections.

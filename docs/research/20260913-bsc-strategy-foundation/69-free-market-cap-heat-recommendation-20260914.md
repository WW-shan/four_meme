# Free-first market-cap and heat recommendation (2026-09-14)

> **2026-09-16: the subsequent report 70 is also not valid profitability evidence.**
> The new quote audit found 597/615 candidates were not BNB-quoted; a same-time
> external trade does not prove our order's execution. All prior threshold and
> horizon preferences below are unvalidated. The current recommendation is in
> [the denomination/execution audit](../20260916-bsc-profitability-evidence/recommendation.md).

## What the latest replay says

**Correction:** The first version of this table is superseded by the causal
failure audit in `70-market-cap-heat-failure-attribution-20260914.json`. The
original exit lookup used future events without a horizon bound and included
synthetic graduation-system prices. Do not use the table below as a final
profitability result; it remains only a record of the earlier probe.

The latest 31-day window has `234,933` lifecycle tokens. The probe treats the
curve's implied market cap as `price * total_supply / 1e18` BNB, converts USD
thresholds with a documented `734.17` BNB/USD snapshot, waits 30 seconds for
confirmation, then applies a 3-second entry delay. It uses 1% fee and 2%
slippage on both sides and only reports horizons with a price point available
after the entry.

The report is `50-market-cap-heat-gate-profile-20260914.json` and the command is
`python scripts/profile_market_cap_heat_gate.py --lifecycle-dir
data/training/latest_bsc_20260914_31d_final ...`.

| Approx. threshold | Activity+flow selected | 1h complete mean / median / positive | 6h mean | 24h mean |
| ---: | ---: | ---: | ---: | ---: |
| $10k | 932 | -22.3% / -9.0% / 37.6% | -54.4% | -71.6% |
| $30k | 710 | -18.0% / -3.1% / 44.8% | -55.1% | -75.4% |
| $50k | 615 | -14.6% / +1.0% / 52.1% | -55.9% | -77.0% |
| $100k | 591 | -13.7% / +1.4% / 52.9% | -55.6% | -77.0% |

The positive 1h median at `$50k-$100k` is a useful screening observation, not
an executable edge: the mean remains negative, the return distribution is very
wide, and the longer horizons collapse. The table is also a complete-path
diagnostic rather than a final portfolio replay. It does not authorize a live
threshold.

## Recommendation

1. **Do not buy at a market-cap threshold by itself.** Use implied curve market
   cap as an eligibility band and a way to avoid the no-liquidity tail. Keep
   `$30k-$100k` as a shadow grid, with `$50k` as the first predeclared point,
   rather than hard-coding one number in the bot.
2. **Require a heat state after the crossing.** For the first shadow protocol,
   confirm that the token remains above the band for 30 seconds and has at
   least three unique buyers, five buys, signed 30-second imbalance of at least
   `0.20`, and sell pressure no higher than `0.60`. These are replay parameters,
   not live-approved settings; the replay still loses money on average.
3. **Rank opportunities instead of buying every pass.** The gate selected
   hundreds of tokens. Group candidates by 5-minute opportunity time and keep a
   small top-k watch list using independent buyers, new-author/X spread,
   mcap acceleration, and slippage. A market-cap gate alone does not solve
   capital competition.
4. **Start with a short conditional hold.** The current data does not support
   a blind 3-4 day hold. Evaluate 15m/1h exits and keep a position only while
   X attention and on-chain flow are both expanding. A 6h or 24h hold should
   be a separate research arm, not the default.
5. **Use free sources first.** The BSC listener and local lifecycle state are
   the strongest zero-cost source. Public DEX Screener and GeckoTerminal
   endpoints can corroborate current liquidity/flow, but must be archived with
   request time and response hash. Official X real-time access is not a
   dependable free interface; a free board can begin with locally collected
   chain heat and add X only when an authenticated feed is available.

## X leaderboard decision rule

An X post becomes `actionable_watch` only when all of these are present:

- exact BSC contract mapping or an official project URL;
- original/quote post from a source with a measured historical record;
- at least two independent authors or a high-confidence source followed by a
  causal chain response;
- new buyer and signed-flow expansion in 15-60 seconds;
- implied market cap inside the shadow band, no creator/cluster sell flag, and
  acceptable entry slippage.

One KOL post without the chain response remains `mapped_unconfirmed`. The
leaderboard should expose source count, independent authors, engagement
velocity, mapping confidence, latency, market-cap band, buyers, flow, and risk
flags separately. This makes it possible to test whether X adds incremental
information over the on-chain-only control.

No model, `.env`, trading switch, position size, or bot behavior changed in
this round.

The corrected conclusion and one-by-one failure rows are in
`73-market-cap-heat-failure-audit-20260914.md`; the original threshold sweep
must remain rejected until valid post-entry and post-graduation exit coverage
is available.

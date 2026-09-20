# Market-cap heat failure audit (2026-09-14)

> **2026-09-16 correction: execution/profitability claims below are withdrawn.**
> Nearby trades measure observation coverage, not whether our AMM order could
> execute. The 615-token quote audit found 597 nonnative quotes; all 22 formerly
> called executable rows are nonnative and outside the current executor's quote
> support. Sixteen of 18 native rows had already migrated at entry. The 58
> near-zero prices came from misdecoding `LiquidityAdded`, not genuine system
> sales. The old numeric rows remain historical artifacts, not proven wins or
> losses. See [the new audit](../20260916-bsc-profitability-evidence/recommendation.md)
> and its per-candidate CSV. No price-ratio result below establishes execution.

## First correction: the earlier aggregate was invalid

The first report in `50-market-cap-heat-gate-profile-20260914.json` was not a
valid fixed-horizon replay. Its exit lookup selected the first lifecycle event
**after** the horizon without an upper bound. In the `$50k` activity+flow arm:

- `489` rows were counted as complete at 1 hour;
- `302/489` selected an exit more than 60 seconds after the 1-hour target;
- `102/489` were more than one hour late;
- the maximum delay was `2,020,484` seconds (about 23 days).

The raw path also contained `58` synthetic graduation-system sells at a
near-zero price, all from
`0x000000000000000000a56fa5b99019a5c8000000`. These are migration/system
records, not executable user quotes. They were filtered from the corrected
price path at `1e-15` BNB/token. The audit numbers are in
`72-market-cap-heat-data-audit-20260914.json`.

The old aggregate should therefore be treated as **invalid evidence**, not as
proof that every selected token was a losing trade.

## Corrected per-candidate attribution

`70-market-cap-heat-failure-attribution-20260914.json` contains one row for
each of the `615` `$50k` activity+flow candidates. The corrected protocol:

- uses the confirmed quote carried to the declared 3-second causal entry time;
- never uses an event before entry as a future outcome;
- reports a snapshot return at or before the target with its observation age;
- reports a separate executable return only when an event occurs within a
  3-second grace after the target;
- keeps failure tags and raw timestamps on every candidate.

The corrected 1-hour results are:

- `594` rows have a post-entry price snapshot; snapshot mean `-12.6%`, median
  `-2.6%`, positive rate `45.6%`;
- only `22` rows have an executable event within the 3-second exit grace; of
  those, `19` are positive and `3` are negative;
- `593` rows do not have a timely executable 1-hour exit, so they cannot be
  called proven losses;
- the 6-hour snapshot mean is `-46.6%`, and the 24-hour snapshot mean is
  `-61.3%`; these remain weak and need independent DEX coverage before being
  treated as executable results.

## The three proven 1-hour failures

The separate detail file is
`71-market-cap-true-executable-failures-20260914.json`.

1. `0x1d9569226d5393609c730ed59319ff4774af4444` (`景甜指甲刀`) — executable
   1-hour return `-88.9%`; the first 15 minutes were already slightly weak
   (`MFE -2.6%`), so this is an immediate/structural collapse rather than a
   missed trailing exit. The pre-entry heat window did not reveal the later
   failure.
2. `0x21f462b87ce24eb95e62aa56c1b8af1d9ba0ffff` (`WHISK`) — executable return
   `-60.8%`; it reached `+79.1%` MFE inside 15 minutes and then reversed to a
   large loss. This is the clearest evidence for a conditional profit-lock or
   heat-decay exit; a blind 1-hour hold is the wrong exit rule.
3. `0x13869585d3e8778f568b6c6cf1a24cadd08a4444` (`牛来人生`) — executable
   return `-0.29%`; it is effectively a cost/friction failure, not a large
   directional collapse.

## What the remaining “failures” mean

The per-row tags distinguish actual loss from missing evidence:

- `no_executable_1h_exit` (`593` rows): no event within the declared exit
  grace. This is a data/execution-coverage problem, not a proven loss.
- `stale_1h_snapshot` (`395` rows): the snapshot used to mark the price is
  older than 60 seconds. It is a diagnostic only.
- `early_adverse_move` (`222` rows): the observed 15-minute path crossed a
  `-20%` cost-adjusted drawdown. This is a candidate for an early risk veto,
  but only when the observation is post-entry and timely.
- `post_confirmation_sell_wave` (`165` rows): sell pressure exceeded `60%`
  in the first post-confirmation window. This is the strongest directly
  actionable feature for a future veto.
- `pump_then_reversal` (`75` rows): the path reached at least `+10%` within
  15 minutes and later fell below the 1-hour snapshot entry value. This is an
  exit/decay problem, not evidence that the initial heat detector never found
  a real move.
- `short_lived_heat` (`195` rows): positive 1-hour snapshot followed by a
  negative 6-hour snapshot. This argues against multi-hour blind holding.

## Correct conclusion

The prior statement that the market-cap gate was simply “wrong” was too strong
because the replay implementation mixed horizons and included malformed
graduation-system prices. The corrected evidence says:

- The gate identifies real short-lived bursts in some cases, including the
  `WHISK` pump, but it does not provide a reliable hold rule.
- The current raw lifecycle data has insufficient timely exit coverage to prove
  a profitable 1-hour strategy for most candidates.
- The next useful experiment is not another market-cap threshold sweep. It is a
  causal short-horizon execution dataset with valid post-entry quotes, a
  15-minute adverse-move veto, a heat-decay/sell-wave exit, and separate DEX
  coverage after graduation.
- Until that dataset exists, no threshold or model should be switched into the
  bot.

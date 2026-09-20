# Independent replay and economic-semantics audit

Audit time: 2026-09-15T19:53:01Z (2026-09-16 Asia/Shanghai). Read-only subtask;
only this task artifact is written. No network access, external coding model,
runtime change, or raw ABI / quote-currency re-audit was performed. The lead
agent owns those separate checks and the final scoreboard update.

Reviewed the complete four requested modules, reports 50/70/71/73, their
targeted tests, the OHLCV collector call site, and relevant scoreboard notes.
Line references below describe the working-tree files inspected, which are
untracked at HEAD `c52d67445f78bafefcbda712839a58865d158d94`; HEAD alone does not
identify their contents. SHA-256:

| File under `src/pipeline/` | SHA-256 |
| --- | --- |
| `market_cap_heat_gate.py` | `36900f24475ad83ae13738a0e6789f735e53cbf78bd577846f4cd55c880cb5fe` |
| `market_cap_heat_failure.py` | `7a28315f67888abcdf737064b9929e86fd1d0c00c1582bcdddf0941d023e74da` |
| `tail_capture_strategy.py` | `b814b674a8bc1cf2966fad6a494cb2e4b2963146015eb672a75a6742630306dd` |
| `cross_boundary.py` | `968c39dba5cfacc0ebab75fb64c500a3d5f0a3ff5af6e6befdb52f1361f12f59` |

## Conclusion

The historical record does **not** establish 22 executable trades, 19 wins, or
three proven trade losses. It establishes 22 selected event-price observations
near one scheduled horizon, under a price proxy whose entry, ordering, venue,
and execution assumptions remain unverified. Both profitable and unprofitable
strategy conclusions drawn from these replay outputs need qualification.

The correction withdrawing report 50 is appropriate, but report 70 is not an
execution-validated replacement. Likewise, the tail replay cannot currently
support comparisons of realizable capital growth or maximum drawdown. This
does not prove that the strategy works or fails; it invalidates the measurement.

## Direct counts from preserved report 70

These counts only describe the existing artifact, without endorsing its price
units, data validity, universe, or execution assumptions.

| Observation | Count / value |
| --- | --- |
| Frozen selected rows | 615 |
| Non-null `h3600_strict_return` | 22; 19 positive, 3 negative |
| Strict observations exactly at the target | 3 |
| Strict observations after the target | 19: 8 at +1s, 3 at +2s, 8 at +3s |
| 1h snapshot kinds | 572 carry-forward, 22 event, 21 missing-before-entry |
| Next observed event later than declared entry +3s | 279 |
| Next observed event later than declared entry +60s | 15; maximum wait 977s |
| Event exists exactly at declared entry | 178 |
| That event's stored price differs from carried confirmation price | 175/178; event/confirmation ratio 0.140545 to 1.567735 |
| Rows with a recorded graduation | 36 |
| Graduation at/before confirmation | 16 |
| Graduation at/before declared entry | 18 |
| Graduation at/before scheduled 1h exit | 31; none has a strict 1h event |

## Prioritized findings

### 1. Critical: future market activity is mislabeled as our executability

**Source:** `market_cap_heat_failure.py:60-69`, `:197-215`, `:230-233`,
`:263`, and `:269-289`; `scripts/attribute_market_cap_heat_failures.py:73`.

`_bounded_event` merely finds a lifecycle price record in
`[target, target + 3s]`. Its existence supplies an "executable" return; its
absence supplies `no_executable_1h_exit`. The summary then computes performance
only over non-null returns. Thus the reported 86.36% positive rate and +5.71%
mean condition on future activity in just 22/615 selected candidates.

An AMM or bonding curve does not generally need a different user to trade in
the same three-second interval before we can submit our sale. Conversely, a
buy/sell observation in that interval does not demonstrate that our specified
quantity could sell for that price. The code contains no stateful quote,
liquidity/reserve check, size-dependent impact, revert check, gas, failed
transaction cost, or evidence of our fill. Even a correctly decoded historical
trade price is not automatically a quote for our order.

**Invalidated claims:** report 70's `executable_1h`; report 71's
`strict_executable_failure_count`; report 73 lines 40-64 and its heading
"The three proven 1-hour failures"; scoreboard line 15's "three true failures".
The remaining 593 rows are neither proven losses nor proven unexecutable.
Quiet pools must not be excluded from the opportunity denominator merely for
lacking another trade at a scheduled exit.

**Minimal correction/tests:** rename these fields to event-observation
diagnostics; retain all 615 candidates and classify actual execution evidence
as unknown unless a venue-specific, amount-specific historical quote or fill
supports it. Test an unchanged, funded pool with no unrelated trade, and a
near-target unrelated trade where the requested order is oversized or reverts:
event presence must not control execution success. Add timestamp/provenance
and separate mark-to-market from liquidation estimates and realized cash.

### 2. Critical: delayed entry uses an old price and can cross graduation

**Source:** `market_cap_heat_failure.py:155-176`, `:202`, `:211-214`;
`market_cap_heat_gate.py:138-157`. Graduation is only copied as metadata at
`market_cap_heat_failure.py:183-184`, not used to determine the entry venue.

The declared entry is confirmation +3s, but every return uses the confirmation
price even when the stored path changed during those three seconds. The 175
artifact rows with a different price at the exact declared entry demonstrate
that this is not just a hypothetical stale-price problem. They do not by
themselves provide correct fill prices, because ordering and quote semantics
still need repair. Eighteen candidates already graduated by this entry time;
a carried curve price cannot demonstrate that a curve buy remained available.

**Witness, executed in memory:** confirmation t=150 at `6e-8`; a new price
`1.2e-7` at t=151; declared entry t=153; price still `1.2e-7` at the exact
1h target t=3753. With zero fees/slippage, the current attribution reports
**+100%**, while the latest observed state at entry was already `1.2e-7`.
This is a stale entry reference, not a captured post-entry doubling.

**Invalidated claims:** the "causal entry" wording in reports 70/73, all
reported net percentages using this reference, and any inference that a price
move during execution latency was profit after entry.

**Minimal correction/tests:** distinguish signal price, available quote,
quote observation time, submission time, venue, and confirmed/simulated fill.
Replay all observed state transitions through entry; model latency and amount
or retain an explicitly non-executable mark. Reject/mark unknown a curve-only
entry after migration until the DEX venue and state exist. Add the doubling
witness and graduation-between-confirmation-and-entry cases.

### 3. Critical: nominal holding periods, completeness, and cash release disagree

**Source:** `market_cap_heat_gate.py:157`, `:187-197`;
`tail_capture_strategy.py:391-405`, `:428-458`, `:465-473`, `:691-694`.

The old gate still takes the first event after both entry delay and exit
horizon without a lateness bound. Report 73 already documents up to 23 days of
exit lateness in report 50. The code can still reproduce that class of error.
Report 70 removes that particular unbounded exit substitution but does not fix
the independent entry-price problem above.

Tail replay also waits without a bound for an entry event. It calls a path
complete whenever *any* event exists at or beyond the intended horizon. A
normal exit may be up to `max(exit_delay_seconds, 3600)` seconds late; with no
such exit, the last event **before** the horizon becomes a realized exit.
Stop-trigger exits use still another boundary: next event after the delay,
anywhere before the horizon. These are different holding policies, not a
common three-second execution assumption.

**Witness, executed in memory:** signal t=0; entry t=3, price=1; event t=4,
price=2; intended hold=3600s; zero costs; `incomplete_policy='loss'`.
Without another event the conservative return is -100% with `censored_loss`.
Append only a far-future event at t=10000, outside the holding period: the
same replay becomes `complete=True`, `status='ok'`, **+100%**, and
`exit_time=4` (a one-second hold). This creates an early realized sale with
future knowledge of the path's absence, and portfolio replay releases that
cash at t=4.

**Invalidated claims:** report 50's fixed-horizon aggregates; tail horizon
comparisons, complete-only portfolios, "conservative" results, and capital
reuse from last-observation exits, including the scoreboard's tail results
at lines 24-25. Existing negative results also cannot prove the strategies
would lose under correctly modeled execution.

**Minimal correction/tests:** declare a decision time, actual fill time,
holding target, bounded execution attempt, and valuation as-of separately.
Never synthesize a sale earlier than its exit decision. A stale mark can value
inventory but cannot release cash. Data completeness must be determined from
acquisition coverage, not the existence of a later trade. Test future-append
invariance: adding events after the evaluation cutoff must not change entry,
exit, completeness, or P&L for that cutoff.

### 4. Critical: same-second sorting invents market order and barrier hits

**Source:** `market_cap_heat_gate.py:41-67`, `:104-108`;
`tail_capture_strategy.py:152-167`, `:185-207`, `:278-301`, `:411-427`;
`cross_boundary.py:45-55`.

The gate sorts a set of `(timestamp, price)` tuples. `at_or_before` therefore
chooses the highest price in a second and `at_or_after` chooses the lowest,
regardless of chain ordering. Buy order is lexicographic transaction-hash
order, not transaction index. Tail path sorting uses `(timestamp, type,
price)`, so a buy is always ordered before a sell in the same second.
Conservative max-entry/min-exit choices do not repair the invented order used
for confirmation, features, running peaks, and stop triggers.

**Witnesses, executed in memory:** an ordered pair of prices `[2, 1]` at t=100
becomes `[1, 2]`; the last-before lookup returns 2. For tail replay, after
entry price=1, a real ordered sell at price=2 followed by a buy at price=4 in
the same second becomes buy=4 followed by sell=2. With a 25% trailing stop and
zero delay/cost, the code manufactures a stop at price=2 (+100%); the supplied
ordered path never drew down and ends at price=4 (+300%).

**Invalidated claims:** exact threshold confirmation, same-second MFE/MAE,
causal barrier or trailing-stop evidence, and the asserted precision of
three-second fills.

**Minimal correction/tests:** preserve block number, transaction index,
log index, venue, and receipt/availability time; define when a block's events
become usable. If ordering is unavailable, mark ambiguity and use separate
best/worst path bounds, not an invented point estimate. Test ordered ties,
opposite buy/sell orderings, and stable event identities.

### 5. Critical: the graduation bridge deletes returns and lacks bar availability

**Source:** `cross_boundary.py:83-86`, `:92-118`, `:123-141`;
collector integration `scripts/collect_post_graduation_ohlcv.py:197-216`.

The bridge computes `scale = last_curve_price / first_dex_close` and then
multiplies every DEX close by it. This forces the first DEX price to equal the
last curve price regardless of the real migration gap or the time between
them. No simultaneous overlap is required. Constant scaling preserves ratios
*within* one DEX quote series, but does not establish a curve-to-DEX token
return or a BNB-denominated portfolio return when the source is USD. This is a
mathematical splice defect independently of the separate raw quote audit.

**Witness, executed in memory:** last curve price=100; first same-currency DEX
price=10; next DEX price=20. The bridge emits 100 -> 100 -> 200, erasing a real
-90% initial gap and replacing the final -80% level with +100%.

The collector requests hourly OHLCV but passes `bar[0]` unchanged; the bridge
stores the close at that timestamp and filters `as_of` against that timestamp.
It has no bar-end or close-available time. For an interval-start timestamp
(the usual OHLCV convention), a close/volume known up to one hour later is made
available at the start. The implementation does not enforce a contract that
would prevent this. Exact provider timestamp semantics should be verified by
the lead's source audit before quantifying affected real bars.

**Invalidated claims:** cross-boundary capital growth, migration-gap risk,
post-graduation entry timing, and "0 bridge violations" as economic validity.
The existing `approximate_price_bridge=True` marker admits approximation but
does not bound its P&L error or make the returns executable.

**Minimal correction/tests:** convert identified quote assets using time-aligned
FX and preserve actual pre/post venue prices; maintain a missing interval when
no bridge can be established. Add explicit bar start/end/available timestamps,
exclude incomplete bars, and enter only after availability plus latency. Test
true boundary gaps, long gaps, varying FX, and `as_of` halfway through a bar.

### 6. Critical: portfolio processing can spend future proceeds in the past

**Source:** `tail_capture_strategy.py:658-700`.

Candidates are processed by signal time, while `close_until` is called using
their possibly much later, variable entry times. Thus the simulated clock can
advance to t=200 to release a previous position, then move back to t=5 for
another fill. This permits impossible cash reuse or capacity decisions.

**Witness, executed in memory:** initial equity/stake=1 BNB, capacity=2; signal
order C/A/B with fills C=3, A=200, B=5; C exits at 100 for +100%, A at 210 for
0%, B at 6 for +100%. The current function takes all three and finishes at
3 BNB. At B's actual fill time all 1 BNB was still tied up in C, so C's future
proceeds cannot pay for B. With these fills and no borrowing the feasible
sequence finishes at 2 BNB. Order reservations would need explicit separate
modeling; they are absent here.

Maximum drawdown is also calculated from cash collected at exits, omitting
open inventory and intermediate marks. Two concurrent flat trades with 0.4
BNB stakes and zero costs, each returning its stake, finish at 1 BNB but report
**40% drawdown**. Conversely, severe unrealized drawdowns may be missed.

**Invalidated claims:** capacity-limited net capital results when variable
entry times overlap, and all `max_drawdown_pct` values as equity risk.

**Minimal correction/tests:** process signals, reserved cash, entry attempts,
confirmed fills, marks, and exits on a monotone event queue. Cash changes only
on an actual modeled cash movement; equity includes independently marked
inventory. Add both witnesses, unsorted exits, same-block capacity, and
unknown-valuation tests before making capital/risk comparisons.

### 7. High: an absolute price floor silently removes valid economic outcomes

**Source:** `market_cap_heat_gate.py:20-22`, `:41-50`;
`market_cap_heat_failure.py:128-140`; report 73 lines 15-20.

The purported filter for 58 synthetic migration records is actually
`price >= 1e-15` for every price-history record. It does not identify a system
event, decode error, address, amount anomaly, or venue. A legitimate positive
price below the floor is discarded, including a possible severe collapse.
Meanwhile, flow attribution still sums all buy/sell rows without an equivalent
validity classification. Filtering prices alone can leave invalid records in
the sell-wave diagnostics or silently carry forward a higher price.

**Witness:** a positive ordinary buy record with price `1e-16` produces an empty
price path. Nothing in these functions establishes that it was malformed.

**Invalidated claims:** that the corrected path specifically excludes only
the identified synthetic system sales, and that all remaining adverse-move,
snapshot, or sell-pressure labels have consistent validity semantics.

**Minimal correction/tests:** use auditable event identity/type plus correct
decoder semantics; quarantine unresolved records with explicit reasons while
preserving originals. No arbitrary absolute floor should remove a verified
valid positive price. Test a valid low-price trade and an invalid system record
at both low and ordinary numeric prices; classify flow and price consistently.

### 8. High: future-dependent sampling and evaluation contaminate model evidence

**Source:** `tail_capture_strategy.py:219-225`, `:362`, `:502-510`,
`:780-801`, `:843-851`, `:961-968`, `:1018-1026`.

`_evenly_limit` selects candidates using the final number of qualifying events
for a token. At default 24 candidates, a later event can change which earlier
signals ever reach portfolio replay. This is not a causal live admission rule.
Witness with limit=2: a prefix at times `[1,2,3]` selects `[1,3]`; appending
future times 4 and 5 changes it to `[1,5]`, deleting the earlier decision.

Complete-only portfolios condition on later activity/coverage. They can be
useful diagnostics, but their returns cannot establish a deployable policy.
Training on complete labels also changes the training population and requires
explicit censoring treatment; retaining incomplete rows in another report does
not remove that bias.

The expanding walk-forward helper passes `eval_complete` to `_fit_ranker` as
the early-stopping/evaluation set and then reports performance on that same
fold. `_fit_ranker` uses `use_best_model=True` at line 607. Therefore the
purported independent next-slice check uses that slice's labels to select its
iteration. Purging uses `sample_time + horizon`, despite variable delayed
entry and late exits, so actual labels may extend across split boundaries.

**Invalidated claims:** causal default candidate stream, independent expanding
walk-forward support, and acceptance checks that promote complete-only profits.
This does not imply the observed negative folds would become positive.

**Minimal correction/tests:** stream candidates without future-aware thinning
(or use a predeclared prefix/hash rule); keep full opportunity denominators;
purge by actual label end/availability; choose early stopping only inside the
training history. Test prefix invariance and assert no evaluation-fold label
is passed into any fit/selection operation.

### 9. Warning: confirmation and hindsight failure tags are weaker than claimed

**Source:** `market_cap_heat_gate.py:126-146`;
`market_cap_heat_failure.py:72-84`, `:128-129`, `:186`, `:218-258`;
report 73 lines 31-33 and 65-100.

The gate takes the first **observed** above-threshold buy, without requiring a
prior below-threshold observation. It tests one point at +30s, not continuous
retention for 30s; a dip and recovery can pass. If that first confirmation
fails, a later valid re-crossing is never reconsidered. These may be deliberate
admission rules, but they must not be described as a verified first crossing
or sustained retention.

`_snapshot_event` prefers the future bounded event over the past snapshot.
Nineteen of the 22 event snapshots in report 70 occur 1-3s after the target,
contradicting the stated "at or before" method. Keep before-target marks and
after-target event diagnostics separate.

`post_confirmation_sell_wave` uses the entire next 60 seconds (57 seconds of
which occur after the declared entry); the 15-minute MAE/MFE tags use future
outcomes. They are legitimate descriptive tags after those observations
arrive, not pre-entry veto features. The single WHISK MFE/reversal example
does not demonstrate that a specified latency-aware profit-lock would have
filled, nor establish that conditional exits outperform blind holding across
the frozen universe.

**Minimal correction/tests:** label first-seen-above-threshold explicitly;
record entry eligibility/coverage and any re-entry policy; separate causal
before-target snapshots from after-target events; evaluate post-entry rules
only from their own observation/decision time and include the exposure before
that time. Test first-observation-above, dip/recovery, later recrossing,
before/after-target separation, and a price jump during exit latency.

## Verification and closeout

- Ran `PYTHONDONTWRITEBYTECODE=1 python -m unittest
  tests.model.test_market_cap_heat_gate tests.model.test_market_cap_heat_failure
  tests.model.test_tail_capture_strategy tests.model.test_cross_boundary`:
  **16 tests passed**. Passing existing tests does not resolve these findings;
  some tests currently enshrine the approximate bridge or event-coverage label.
- Ran the in-memory counterexamples and artifact-only counts quoted above.
  No regression test files, implementation files, preserved research reports,
  or runtime settings were edited by this subtask.
- Minimum next evidence: a frozen, point-in-time candidate universe with event
  ordering and venue transition metadata; amount-specific historical quotes or
  replayable states; explicit acquisition/coverage windows; chronological cash
  accounting; and a predeclared exit comparison evaluated without selecting on
  future activity. Until then, use price paths only as diagnostics and state
  that net capital growth is unmeasured.
- **Scoreboard:** intentionally not edited by this read-only subtask. The lead
  should append a correction before closeout, specifically withdrawing the
  executability / "three true failures" wording and qualifying tail capital
  comparisons. No profitability, best-strategy, or deployment claim is made
  from the 22 filtered observations or a single token example.
- Parent task owns archive/commit/push status. This subtask does not archive,
  stage, commit, or push CCG state.

## Independent implementation review, 2026-09-15 20:58 UTC

Scope: the new `profitability_evidence_audit.py` and CLI/tests; revised
`market_cap_heat_gate.py` and `market_cap_heat_failure.py` with their CLI/tests;
the decoder/backfill integration and targeted tests; and
`docs/research/20260916-bsc-profitability-evidence/candidate-audit.json`
generated at `2026-09-15T20:48:25.409806+00:00`. The lead was concurrently
writing the final prose/retraction documents, which are not certified by this
code/artifact review. No code was edited by this reviewer.

Reviewed source hashes:

| File | SHA-256 |
| --- | --- |
| `src/pipeline/profitability_evidence_audit.py` | `df6aa05ad6e103e33a19dc175d8fe238387ca9895e376f2165ec5fff31f2666a` |
| `scripts/audit_bsc_profitability_evidence.py` | `1624027a8e50c8e3e295c5cea3768553c3e35cf216a1703d091ac3dc3299e851` |
| `src/pipeline/market_cap_heat_failure.py` | `543aa05c8be9bd88724b4d5be133ceca00b658554a057a132b0ae4db1a3b12bf` |
| `src/pipeline/market_cap_heat_gate.py` | `17d79c12d7380ae12c9501504058bc7fdbbc888238b0c97c393215ba79b3d0e8` |

### Review result

**No newly identified Critical defect in the actual frozen artifact.** The
new audit properly withholds actual P&L/execution and model-selection
eligibility. Two reproducible Warning-level input-handling defects should be
fixed before treating this as a reusable audit command; neither changes the
published counts on this particular input. One lower-priority input-contract
issue is also noted below. This review does not rehabilitate the unresolved
tail, portfolio-accounting, or bridge measurements in the original audit.

Confirmed:

- The candidate token multiset and row count exactly match all 615 prior
  candidates. `18 native + 597 nonnative + 0 unknown = 615`; aggregate quote
  counts and every audit-flag count reconcile with the rows.
- All 615 `actual_pnl_bnb` values are null; all execution statuses are unknown;
  all historical-quote-verification flags are false; live/model-selection
  eligibility is false. None of the 22 old near-target observations is removed
  or promoted to a fill, and all 22 are nonnative under the saved registry
  snapshot. Later metadata is explicitly scoped as a denomination audit.
- The FDV math at `profitability_evidence_audit.py:97-103` is correct under the
  stated legacy export: `p_saved = Q_raw / B_raw`, so multiplying by human
  supply gives `FDV_quote = p_saved * S_raw / 10**quote_decimals`; base
  decimals cancel. The six-decimal quote test covers a material unit error.
  All 623 saved asset-decimal responses are 18 in this acquisition. Each
  published FDV agrees with the old USD proxy divided by its assumed 734.17
  BNB/USD within relative error `3.14e-16`. All 381 USDT rows are below 50,000
  **quote units**; no exact USD parity or capital-return inference is made.
- All 95 tiny-buy flags also meet the 90% threshold when independently divided
  by **all** confirmation-window buys, rather than just known amounts. Thus
  the denominator defect below does not affect the reported 95.
- Known graduation values in this artifact are 36 integer timestamps and 579
  nulls. Therefore the ISO parsing defect below does not change the published
  18 migrated-at-entry or 31 migrated-by-1h counts.
- The three non-lifecycle input hashes in `candidate-audit.json` match the
  saved source reports/snapshots. Lifecycle hashes were recorded by the command
  but not redundantly recomputed by this subreview.
- Entry marks now include price changes during the declared delay; as-of
  snapshots exclude later events; actual execution remains unknown. The
  absolute price floor and price-sorted same-second ties are removed. The
  remaining legacy unbounded-event diagnostics are explicitly marked
  unvalidated and ineligible for model selection.

### Warning R1: valid ISO timestamps and missing dates become false venue facts

`src/pipeline/profitability_evidence_audit.py:109-116` parses timestamps with
`_decimal`. A valid ISO graduation timestamp is silently treated as missing.
The repository loader only normalizes timestamps while merging multiple
snapshots (`runner_reserve_profile.py:421-424`); a single lifecycle retains
the original ISO value. This is a reachable input convention, not an invented
malformed representation.

In-memory regression witness: `graduate_time=132` and entry=133 yields
`curve_venue_ended_at_entry=True`; the equivalent
`"1970-01-01T00:02:12+00:00"` yields False and drops both migration flags.
Missing graduation similarly becomes False. One actual native candidate,
`0xc5ebc3105f407ce0d608c1da71d90b9c18ec4444` (`yy`), has no recorded graduation;
its False value is not proof that the curve remained available at entry.

Recommended fix: parse epoch/ISO/datetime using an explicit UTC convention,
reject non-finite/bool timestamps, and preserve unknown venue state as null (or
name the boolean as merely "recorded graduation before entry"). Add missing,
invalid, timezone-offset, and equivalent epoch/ISO tests. Count only explicit
True in migration aggregates.

### Warning R2: dropping unknown amounts inflates the tiny-buy percentage

`src/pipeline/profitability_evidence_audit.py:125-129` drops missing/invalid
amounts before computing the proportion, then labels the result as a fraction
of buys. A valid 10-buy window containing eight known 0.1-USDT buys and two
unknown amounts is flagged as "at least 90% below one USDT", even though only
80% of all buys are confirmed tiny. This contradicts the stated treatment of
unknown evidence.

Recommended fix: expose known/unknown amount counts and use all buys as the
denominator for the confirmed lower bound, or decline the exact percentage
when coverage is incomplete. Add the 8-known-small/2-unknown regression plus
an all-unknown window. The currently published 95 remains valid because all
95 already meet the threshold against their full reported buy counts.

### Info R3: invalid row preservation and input version need an explicit contract

The new audit's docstring promises retention of invalid rows, but
`profitability_evidence_audit.py:80-81` raises `AttributeError` for a null item
in `prior_report.rows` instead of retaining/flagging or explicitly validating
the input. The CLI also does not declare/check that its expected frozen input
uses the old `entry_time_causal` / `h3600_strict_return` fields; feeding the
new version-2 attribution output silently loses those diagnostics.

Recommended fix: either validate the expected old report schema up front with
a descriptive error, or normalize supported versions and retain malformed
rows with source-row identity and explicit flags. This does not affect the
615 valid version-1 rows reviewed here.

### Review verification and remaining scope

Ran `PYTHONDONTWRITEBYTECODE=1 python -m unittest` with the five modules
`tests.model.test_profitability_evidence_audit`,
`tests.model.test_market_cap_heat_failure`,
`tests.model.test_market_cap_heat_gate`,
`tests.model.test_fourmeme_log_decoder`, and
`tests.model.test_backfill_fourmeme_month_raw`: **31 tests passed**. Also ran
the timestamp, missing-amount, malformed-row, and artifact-reconciliation
witnesses above without writing code. Warnings were sent to the lead for
bounded fixes; any subsequent source revision requires its own recheck.

Scoreboard and final prose remain lead-owned; this review intentionally does
not edit them. Current metadata is not historical state, failed RPC attempts
do not prove every possible provider lacks history, and the two native rows
without a recorded pre-entry migration are not a profitable or executable
strategy sample. No additional research-performance or deployment claim is
approved by this review.

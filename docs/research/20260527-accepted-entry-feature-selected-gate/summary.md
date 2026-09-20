# Accepted-Entry Feature-Selected Gate

Date: 2026-05-27

## Question

Can actual accepted-entry trade-log attribution improve the current v95 live-sized replay by selecting only the most discriminative time-exit-loss features before fitting an accepted-entry keep/skip meta-gate?

This was tested as a new direction after the action-policy router pass-through replay showed that broad route maps did not line up with actual strict-replay entry indices.

## Research Basis

SmartSearch artifacts are saved in this directory.

Commands:

```bash
smart-search doctor --format json > docs/research/20260527-accepted-entry-feature-selected-gate/00-doctor.json
smart-search deep "For a live meme-token trading bot with repeated accepted-entry time-exit losses, how should actual trade-log attribution, meta-labeling, survival/time-to-event labels, and conservative offline policy evaluation guide a feature-selected accepted-entry keep or post-entry exit overlay using only decision-time features and strict validation/final/stress gates?" --format json --output docs/research/20260527-accepted-entry-feature-selected-gate/00-deep-plan.json
```

Fetched evidence reused from the same research pass:

- Hudson & Thames meta-labeling: secondary models should decide when to act on a primary strategy, not replace the primary strategy blindly.
- Trading Signal Survival Analysis: signal start time and target-hit duration can be modeled as time-to-event evidence.
- Counterfactual Risk Minimization / OPE tutorial: logged-policy evidence needs conservative validation before policy changes.

## Experiment

The experiment reused actual v95 accepted-entry trade logs and the existing feature-contrast report:

- Feature source: `data/replay_reports/accepted_entry_feature_contrast_20260526_dead_flow_toxicity_meta_gate_round.json`
- Replay report: `data/replay_reports/accepted_entry_feature_selected_loss_gate_replay_20260527_time_exit_top12.json`

The selected `time_exit_loss` top-12 feature set was:

```text
early_activity_ratio, early_volume_ratio, name_length, time_since_launch,
creator_holding_ratio, creator_is_seller, burst_intensity, trade_frequency,
small_buy_ratio, avg_holding, symbol_length, address_overlap_ratio
```

The fitted tree used only three features:

- `address_overlap_ratio`
- `time_since_launch`
- `creator_holding_ratio`

## Result

Decision: `reject`.

Baseline:

- Validation: `0.021094872146` BNB, `32` trades, `75.00%` win rate, max DD `-9.8821%`.
- Final: `0.005174515325` BNB, `21` trades, `52.3810%` win rate, max DD `-18.2292%`.

Candidate thresholds:

- `0.35`: no effective change; validation and final tied baseline.
- `0.45`: final improved slightly (`+0.0000596094` BNB, `20` trades, `55.00%` win rate), but validation net profit fell by `-0.0000273625` BNB and max DD worsened.
- `0.55`: validation improved (`+0.000445486` BNB, `29` trades, `82.7586%` win rate), but final fell by `-0.000294957` BNB and drawdown worsened.

No threshold passed both validation and final. No `.env`, threshold, sizing, model artifact, bot process, or live runtime setting changed.

## Conclusion

The actual-trade-log feature-selected keep/skip gate remains useful attribution, but it is not robust enough for live or canary promotion. The important signal is narrower: time-exit features can sometimes remove one final loser, but the effect is not validation-stable.

Next work should not continue broad accepted-entry keep/skip feature selection as the main branch. A better follow-up is a true post-target / time-to-event exit overlay, where the action is `continue_hold` versus `lock_profit` after target activation rather than rejecting baseline entries before the trade opens.

Scoreboard update: `docs/model_scoreboard.md` records this rejected experiment and the next-direction constraint.

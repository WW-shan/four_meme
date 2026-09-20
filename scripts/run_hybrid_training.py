#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _parse_int_list(raw):
    if raw is None:
        return []
    values = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        values.append(int(part))
    return values


def _parse_optional_int_list(raw):
    if raw is None:
        return None
    return _parse_int_list(raw)


def _parse_float_list(raw):
    if raw is None:
        return []
    values = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        values.append(float(part))
    return values


def _resolve_replay_execution_controls(args):
    if args.live_replay_profile:
        entry_delay_seconds = 3 if args.entry_delay_seconds is None else args.entry_delay_seconds
        exit_delay_seconds = 3 if args.exit_delay_seconds is None else args.exit_delay_seconds
        max_open_positions = 8 if args.max_open_positions is None else args.max_open_positions
        entry_max_fill_wait_seconds = (
            3 if args.entry_max_fill_wait_seconds is None else args.entry_max_fill_wait_seconds
        )
        exit_max_fill_wait_seconds = (
            6 if args.exit_max_fill_wait_seconds is None else args.exit_max_fill_wait_seconds
        )
        entry_price_protection_pct = (
            0.25 if args.entry_price_protection_pct is None else args.entry_price_protection_pct
        )
    else:
        entry_delay_seconds = 0 if args.entry_delay_seconds is None else args.entry_delay_seconds
        exit_delay_seconds = 0 if args.exit_delay_seconds is None else args.exit_delay_seconds
        max_open_positions = args.max_open_positions
        entry_max_fill_wait_seconds = args.entry_max_fill_wait_seconds
        exit_max_fill_wait_seconds = args.exit_max_fill_wait_seconds
        entry_price_protection_pct = args.entry_price_protection_pct

    return {
        "live_replay_profile": bool(args.live_replay_profile),
        "entry_delay_seconds": int(entry_delay_seconds),
        "exit_delay_seconds": int(exit_delay_seconds),
        "max_open_positions": None if max_open_positions is None else int(max_open_positions),
        "entry_max_fill_wait_seconds": None if entry_max_fill_wait_seconds is None else int(entry_max_fill_wait_seconds),
        "exit_max_fill_wait_seconds": None if exit_max_fill_wait_seconds is None else int(exit_max_fill_wait_seconds),
        "entry_price_protection_pct": None if entry_price_protection_pct is None else float(entry_price_protection_pct),
        "entry_execution_failure_rate": max(0.0, min(1.0, float(getattr(args, "entry_execution_failure_rate", 0.0) or 0.0))),
        "exit_execution_failure_rate": max(0.0, min(1.0, float(getattr(args, "exit_execution_failure_rate", 0.0) or 0.0))),
        "max_pending_entries": None if getattr(args, "max_pending_entries", None) is None else int(args.max_pending_entries),
        "stress_replay": bool(args.stress_replay or args.live_replay_profile),
    }


def _load_execution_calibration(path):
    if not path:
        return None
    calibration_path = Path(path)
    if not calibration_path.exists():
        raise FileNotFoundError(f"execution calibration file not found: {calibration_path}")
    with calibration_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else None


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run hybrid CatBoost+PPO training")
    parser.add_argument("--output-dir", default="data/models", help="Output directory for artifacts")
    parser.add_argument("--total-timesteps", type=int, default=20000, help="PPO total timesteps")
    parser.add_argument("--lifecycle-dir", default="data/training", help="Directory containing lifecycle files")
    parser.add_argument("--sample-mode", default="trade_event", help="DatasetBuilder sample mode")
    parser.add_argument("--max-sample-age-seconds", type=int, default=300, help="Max token age in seconds for new entries")
    parser.add_argument("--future-windows", default="300", help="Comma-separated future label windows in seconds")
    parser.add_argument("--max-hold-seconds", type=int, default=300, help="Maximum hold horizon for replay and sell learning")
    parser.add_argument("--min-policy-hold-seconds", type=int, default=0, help="Minimum age before policy sell signals can close a position")
    parser.add_argument("--max-samples-per-token", type=int, default=120, help="Evenly cap dense event samples per token")
    parser.add_argument(
        "--buy-max-samples-per-token",
        type=int,
        default=None,
        help="Optional separate cap for buy-model samples per token; sell episodes keep all eligible samples",
    )
    parser.add_argument(
        "--include-flow-features",
        action="store_true",
        help="Include optional short-window sell-pressure and signed-imbalance features in training artifacts",
    )
    parser.add_argument("--sample-cache-dir", default=".cache/hybrid_samples", help="Directory used to cache generated lifecycle training samples")
    parser.add_argument("--no-sample-cache", action="store_true", help="Disable lifecycle training sample cache")
    parser.add_argument("--target-label-column", default="executable_return_pct", help="Label column for buy target")
    parser.add_argument("--target-threshold-value", type=float, default=80.0, help="Threshold for positive buy label")
    parser.add_argument(
        "--label-target-return-pct",
        type=float,
        default=None,
        help="Upper-barrier return percent used when building target-hit labels; defaults to target threshold",
    )
    parser.add_argument("--train-entry-value-model", action="store_true", help="Train an auxiliary entry-value regression model")
    parser.add_argument(
        "--entry-value-target-label-column",
        default="live_risk_adjusted_return_pct",
        help="Label column for the entry-value regression model",
    )
    parser.add_argument(
        "--entry-ranking-mode",
        choices=("chronological", "buy_prob", "entry_value"),
        default="chronological",
        help="Entry ordering mode used during replay evaluation",
    )
    parser.add_argument("--min-entry-score", type=float, default=None, help="Minimum predicted entry-value score required during replay evaluation")
    parser.add_argument(
        "--buy-primary-score-rescue-min-prob",
        type=float,
        default=None,
        help="Minimum buy probability for rescuing primary signals rejected only by low entry score",
    )
    parser.add_argument(
        "--buy-primary-score-rescue-min-pred-return",
        type=float,
        default=None,
        help="Minimum predicted return for primary score rescue candidates",
    )
    parser.add_argument(
        "--buy-primary-score-rescue-min-entry-volume-30s",
        type=float,
        default=None,
        help="Minimum 30s buy volume for primary score rescue candidates",
    )
    parser.add_argument(
        "--buy-primary-score-rescue-min-entry-price-volatility",
        type=float,
        default=None,
        help="Minimum price volatility for primary score rescue candidates",
    )
    parser.add_argument(
        "--buy-primary-score-rescue-min-age-seconds",
        type=float,
        default=None,
        help="Minimum token age for primary score rescue candidates",
    )
    parser.add_argument(
        "--label-live-downside-penalty-weight",
        type=float,
        default=0.0,
        help="Penalty weight applied to worst live delayed downside when building live_risk_adjusted_return_pct",
    )
    parser.add_argument(
        "--label-delay-robust-entry-delays",
        default=None,
        help="Comma-separated entry delays used to build live_delay_robust_return_pct labels",
    )
    parser.add_argument(
        "--label-delay-robust-min-weight",
        type=float,
        default=1.0,
        help="Weight assigned to the worst delayed live return in live_delay_robust_return_pct",
    )
    parser.add_argument(
        "--bc-label-mode",
        choices=("sell_pressure", "profit_path"),
        default="sell_pressure",
        help="BC warmstart label mode for the sell policy",
    )
    parser.add_argument(
        "--bc-profit-path-min-hold-seconds",
        type=float,
        default=0.0,
        help="Minimum hold time before profit_path labels may trigger a sell action",
    )
    parser.add_argument(
        "--bc-profit-path-trailing-start-pct",
        type=float,
        default=0.25,
        help="Profit threshold before profit_path labels may use trailing exits",
    )
    parser.add_argument(
        "--bc-profit-path-trailing-stop-pct",
        type=float,
        default=0.20,
        help="Drawdown from peak that triggers profit_path trailing exits",
    )
    parser.add_argument(
        "--bc-profit-path-sell-margin-pct",
        type=float,
        default=0.05,
        help="Future upside margin required before profit_path labels keep holding",
    )
    parser.add_argument(
        "--bc-profit-path-sell100-pct",
        type=float,
        default=0.80,
        help="Profit threshold at which profit_path labels force a full exit",
    )
    parser.add_argument(
        "--bc-profit-path-sell50-pct",
        type=float,
        default=0.50,
        help="Profit threshold at which profit_path labels prefer a 50%% exit",
    )
    parser.add_argument(
        "--bc-profit-path-sell25-pct",
        type=float,
        default=0.20,
        help="Profit threshold at which profit_path labels prefer a 25%% exit",
    )
    parser.add_argument(
        "--fit-artifacts-on-all-data",
        action="store_true",
        help="After holdout evaluation, retrain saved artifacts on all lifecycle files for production use",
    )
    parser.add_argument("--buy-min-precision", type=float, default=0.50, help="Min precision for buy threshold selection")
    parser.add_argument("--buy-min-threshold", type=float, default=0.50, help="Minimum buy probability threshold allowed")
    parser.add_argument("--buy-calibration-ratio", type=float, default=0.20, help="Ratio of train samples reserved for buy threshold calibration")
    parser.add_argument("--min-calibration-samples", type=int, default=20, help="Minimum calibration samples required for the buy model")
    parser.add_argument("--buy-min-calibration-predictions", type=int, default=20, help="Minimum calibration buy candidates required at the selected threshold")
    parser.add_argument(
        "--buy-sample-weighting",
        choices=("none", "token_balanced", "recency_decay"),
        default="none",
        help="Optional buy-model sample weighting mode",
    )
    parser.add_argument(
        "--buy-recency-half-life-hours",
        type=float,
        default=24.0,
        help="Half-life in hours for recency_decay buy-model sample weighting",
    )
    parser.add_argument("--train-split-ratio", type=float, default=0.8, help="Train split ratio for lifecycle file partitioning")
    parser.add_argument("--validation-split-ratio", type=float, default=0.0, help="Optional validation split ratio used for replay threshold tuning")
    parser.add_argument("--min-validation-files", type=int, default=1, help="Minimum number of files reserved for validation when enabled")
    parser.add_argument("--min-eval-files", type=int, default=1, help="Minimum number of files reserved for evaluation")
    parser.add_argument("--min-entry-unique-buyers", type=int, default=3, help="Minimum unique buyers required before an entry sample can be generated")
    parser.add_argument("--min-entry-buy-count", type=int, default=5, help="Minimum buy count required before an entry sample can be generated")
    parser.add_argument("--min-entry-volume-30s", type=float, default=None, help="Minimum 30s buy volume feature required during replay/live entry filtering")
    parser.add_argument("--min-entry-price-volatility", type=float, default=None, help="Minimum price volatility feature required during replay/live entry filtering")
    parser.add_argument("--stop-loss", type=float, default=-0.50, help="Hard stop-loss used by runtime-aligned eval replay")
    parser.add_argument("--position-fraction", type=float, default=0.10, help="Cash fraction used per replay position")
    parser.add_argument("--max-position-fraction", type=float, default=0.10, help="Maximum fraction of starting equity used for any single replay position")
    parser.add_argument("--initial-equity-bnb", type=float, default=1.0, help="Starting BNB equity used by replay")
    stake_group = parser.add_mutually_exclusive_group()
    stake_group.add_argument("--fixed-stake-bnb", type=float, default=None, help="Fixed BNB stake per replay entry; live profile defaults to 0.1")
    stake_group.add_argument("--no-fixed-stake-bnb", action="store_true", help="Use fractional live sizing instead of a fixed BNB stake")
    parser.add_argument("--include-trade-log", action="store_true", help="Include runtime replay trade logs in the manifest")
    parser.add_argument("--allow-partial-exits", action="store_true", help="Allow PPO SELL25/SELL50 actions to partially close positions")
    parser.add_argument("--fee-bps", type=float, default=100.0, help="Per-side fee in basis points used by replay")
    parser.add_argument("--slippage-bps", type=float, default=200.0, help="Per-side slippage in basis points used by replay")
    parser.add_argument("--one-entry-per-token", dest="one_entry_per_token", action="store_true", default=True, help="Allow at most one entry per token in replay")
    parser.add_argument("--allow-token-reentry", dest="one_entry_per_token", action="store_false", help="Allow replay to re-enter a token after a full exit")
    parser.add_argument("--max-trades-per-token", type=int, default=1, help="Maximum replay entries per token")
    parser.add_argument("--trailing-start-pct", type=float, default=0.30, help="Profit threshold before trailing stop can activate")
    parser.add_argument("--trailing-stop-pct", type=float, default=0.25, help="Drawdown from peak that triggers trailing stop")
    parser.add_argument("--rug-sell-pressure", type=float, default=0.95, help="Sell pressure threshold that triggers fast rug exit")
    parser.add_argument("--risk-tune-buy-threshold", dest="risk_tune_buy_threshold", action="store_true", default=True, help="Tune buy threshold with calibration replay")
    parser.add_argument("--no-risk-tune-buy-threshold", dest="risk_tune_buy_threshold", action="store_false", help="Disable calibration replay buy-threshold tuning")
    parser.add_argument("--risk-tune-min-trades", type=int, default=20, help="Minimum calibration replay trades for threshold tuning")
    parser.add_argument("--risk-tune-max-trades", type=int, default=200, help="Maximum calibration replay trades for threshold tuning")
    parser.add_argument("--risk-tune-min-threshold", type=float, default=0.50, help="Minimum threshold considered by replay risk tuning")
    parser.add_argument("--risk-tune-target-entry-rate", type=float, default=0.15, help="Target fraction of calibration token episodes to enter")
    parser.add_argument("--risk-tune-entry-rate-penalty", type=float, default=0.25, help="Penalty weight for missing target entry-rate during threshold scoring")
    parser.add_argument("--risk-tune-min-entry-rate", type=float, default=None, help="Minimum calibration entry-rate required during threshold tuning")
    parser.add_argument("--risk-tune-max-entry-rate", type=float, default=None, help="Maximum calibration entry-rate allowed during threshold tuning")
    parser.add_argument("--risk-tune-candidate-entry-rates", default="0.05,0.10,0.15,0.25,0.40", help="Comma-separated entry-rate quantiles used to generate threshold candidates")
    parser.add_argument("--risk-tune-thresholds", default=None, help="Comma-separated explicit buy probability thresholds used during replay tuning")
    parser.add_argument("--risk-tune-max-drawdown-pct", type=float, default=-40.0, help="Maximum allowed calibration replay drawdown for threshold tuning")
    parser.add_argument("--risk-tune-min-win-rate", type=float, default=0.50, help="Minimum calibration replay win rate for threshold tuning")
    parser.add_argument("--risk-tune-drawdown-penalty", type=float, default=1.0, help="Drawdown penalty weight for replay threshold scoring")
    parser.add_argument("--risk-tune-turnover-penalty", type=float, default=0.001, help="Entry-rate turnover penalty for replay threshold scoring")
    parser.add_argument("--risk-tune-probability-threshold-count", type=int, default=0, help="Number of observed probability thresholds to add during replay threshold tuning")
    parser.add_argument("--sell-drawdown-penalty-weight", type=float, default=0.0, help="Sell-policy reward drawdown penalty weight")
    parser.add_argument("--sell-hold-penalty-per-step", type=float, default=0.0, help="Sell-policy reward hold penalty per step")
    parser.add_argument("--sell-turnover-penalty", type=float, default=0.001, help="Sell-policy reward turnover penalty")
    parser.add_argument("--walk-forward-segments", type=int, default=3, help="Number of chronological eval segments reported in the manifest")
    parser.add_argument("--stress-replay", action="store_true", help="Report default live-like stress replay scenarios in the manifest")
    parser.add_argument("--live-replay-profile", action="store_true", help="Use live-style replay controls: 3s entry/exit delay and 8 max open positions")
    parser.add_argument("--entry-delay-seconds", type=int, default=None, help="Replay buy-fill delay in seconds; defaults to 3 with --live-replay-profile, otherwise 0")
    parser.add_argument("--exit-delay-seconds", type=int, default=None, help="Replay sell-fill delay in seconds; defaults to 3 with --live-replay-profile, otherwise 0")
    parser.add_argument("--max-open-positions", type=int, default=None, help="Replay maximum simultaneous open positions; defaults to 8 with --live-replay-profile")
    parser.add_argument("--entry-max-fill-wait-seconds", type=int, default=None, help="Skip delayed buys whose first available fill arrives after this many seconds")
    parser.add_argument("--exit-max-fill-wait-seconds", type=int, default=None, help="Report delayed sells whose first available fill arrives after this many seconds")
    parser.add_argument("--entry-price-protection-pct", type=float, default=None, help="Skip delayed buys if the fill price exceeds signal price by this fraction")
    parser.add_argument("--entry-fixed-cost-bnb", type=float, default=0.0, help="Fixed BNB cost per buy transaction used by replay and labels")
    parser.add_argument("--exit-fixed-cost-bnb", type=float, default=0.0, help="Fixed BNB cost per sell transaction used by replay and labels")
    parser.add_argument("--label-fixed-stake-bnb", type=float, default=None, help="Fixed BNB stake assumed when converting live labels to returns")
    parser.add_argument("--label-entry-fixed-cost-bnb", type=float, default=None, help="Fixed BNB cost per buy transaction for live label generation")
    parser.add_argument("--label-exit-fixed-cost-bnb", type=float, default=None, help="Fixed BNB cost per sell transaction for live label generation")
    parser.add_argument("--label-entry-price-protection-pct", type=float, default=None, help="Maximum delayed-entry price jump allowed in live label generation")
    parser.add_argument("--entry-execution-failure-rate", type=float, default=0.0, help="Deterministic delayed/instant buy execution failure rate used by replay")
    parser.add_argument("--exit-execution-failure-rate", type=float, default=0.0, help="Deterministic sell execution failure rate used by replay")
    parser.add_argument("--max-pending-entries", type=int, default=None, help="Maximum simultaneous pending delayed buy fills before new signals are blocked")
    parser.add_argument("--execution-calibration-file", default=None, help="Optional JSON report used to seed live-style replay controls")
    parser.add_argument("--catboost-iterations", type=int, default=500, help="CatBoost iteration limit")
    parser.add_argument("--catboost-learning-rate", type=float, default=0.05, help="CatBoost learning rate")
    parser.add_argument("--catboost-depth", type=int, default=5, help="CatBoost tree depth")
    parser.add_argument("--catboost-l2-leaf-reg", type=float, default=10.0, help="CatBoost L2 leaf regularization")
    parser.add_argument("--catboost-random-strength", type=float, default=1.0, help="CatBoost random strength")
    parser.add_argument("--catboost-bagging-temperature", type=float, default=1.0, help="CatBoost bagging temperature")
    parser.add_argument("--catboost-rsm", type=float, default=0.8, help="CatBoost random subspace ratio")
    parser.add_argument("--catboost-od-wait", type=int, default=50, help="CatBoost overfitting detector wait rounds")
    return parser.parse_args(argv)


def main(argv=None):
    command_args = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(command_args)
    replay_controls = _resolve_replay_execution_controls(args)
    execution_calibration = _load_execution_calibration(getattr(args, "execution_calibration_file", None))
    if execution_calibration:
        replay_overrides = execution_calibration.get("replay_overrides", {})
        if isinstance(replay_overrides, dict):
            for key, value in replay_overrides.items():
                if key in replay_controls and value is not None:
                    replay_controls[key] = value
    explicit_replay_overrides = {
        "entry_delay_seconds": args.entry_delay_seconds,
        "exit_delay_seconds": args.exit_delay_seconds,
        "max_open_positions": args.max_open_positions,
        "entry_max_fill_wait_seconds": args.entry_max_fill_wait_seconds,
        "exit_max_fill_wait_seconds": args.exit_max_fill_wait_seconds,
        "entry_price_protection_pct": args.entry_price_protection_pct,
        "max_pending_entries": args.max_pending_entries,
    }
    for key, value in explicit_replay_overrides.items():
        if value is not None:
            replay_controls[key] = value
    fixed_stake_bnb = args.fixed_stake_bnb
    if fixed_stake_bnb is None and replay_controls["live_replay_profile"] and not args.no_fixed_stake_bnb:
        fixed_stake_bnb = 0.1

    from src.pipeline.train_hybrid import run_hybrid_training

    config = {
        "output_dir": args.output_dir,
        "command_args": command_args,
        "total_timesteps": args.total_timesteps,
        "lifecycle_dir": args.lifecycle_dir,
        "sample_mode": args.sample_mode,
        "max_sample_age_seconds": args.max_sample_age_seconds,
        "max_entry_age_seconds": args.max_sample_age_seconds,
        "future_windows": _parse_int_list(args.future_windows),
        "max_hold_seconds": args.max_hold_seconds,
        "min_policy_hold_seconds": args.min_policy_hold_seconds,
        "max_samples_per_token": args.max_samples_per_token,
        "include_flow_features": bool(getattr(args, "include_flow_features", False)),
        "sample_cache_dir": None
        if bool(getattr(args, "no_sample_cache", False))
        else getattr(args, "sample_cache_dir", ".cache/hybrid_samples"),
        "target_label_column": args.target_label_column,
        "target_threshold_value": args.target_threshold_value,
        "label_target_return_pct": args.label_target_return_pct,
        "train_entry_value_model": bool(args.train_entry_value_model),
        "entry_value_target_label_column": args.entry_value_target_label_column,
        "entry_ranking_mode": args.entry_ranking_mode,
        "min_entry_score": args.min_entry_score,
        "buy_primary_score_rescue_min_prob": args.buy_primary_score_rescue_min_prob,
        "buy_primary_score_rescue_min_pred_return": args.buy_primary_score_rescue_min_pred_return,
        "buy_primary_score_rescue_min_entry_volume_30s": args.buy_primary_score_rescue_min_entry_volume_30s,
        "buy_primary_score_rescue_min_entry_price_volatility": args.buy_primary_score_rescue_min_entry_price_volatility,
        "buy_primary_score_rescue_min_age_seconds": args.buy_primary_score_rescue_min_age_seconds,
        "label_live_downside_penalty_weight": args.label_live_downside_penalty_weight,
        "label_delay_robust_entry_delay_seconds": _parse_optional_int_list(args.label_delay_robust_entry_delays),
        "label_delay_robust_min_weight": args.label_delay_robust_min_weight,
        "bc_label_mode": args.bc_label_mode,
        "bc_profit_path_min_hold_seconds": args.bc_profit_path_min_hold_seconds,
        "bc_profit_path_trailing_start_pct": args.bc_profit_path_trailing_start_pct,
        "bc_profit_path_trailing_stop_pct": args.bc_profit_path_trailing_stop_pct,
        "bc_profit_path_sell_margin_pct": args.bc_profit_path_sell_margin_pct,
        "bc_profit_path_sell100_pct": args.bc_profit_path_sell100_pct,
        "bc_profit_path_sell50_pct": args.bc_profit_path_sell50_pct,
        "bc_profit_path_sell25_pct": args.bc_profit_path_sell25_pct,
        "fit_artifacts_on_all_data": bool(args.fit_artifacts_on_all_data),
        "buy_min_precision": args.buy_min_precision,
        "buy_min_threshold": args.buy_min_threshold,
        "buy_calibration_ratio": args.buy_calibration_ratio,
        "min_calibration_samples": args.min_calibration_samples,
        "buy_min_calibration_predictions": args.buy_min_calibration_predictions,
        "buy_sample_weighting": args.buy_sample_weighting,
        "buy_recency_half_life_hours": getattr(args, "buy_recency_half_life_hours", 24.0),
        "train_split_ratio": args.train_split_ratio,
        "validation_split_ratio": args.validation_split_ratio,
        "min_validation_files": args.min_validation_files,
        "min_eval_files": args.min_eval_files,
        "min_entry_unique_buyers": args.min_entry_unique_buyers,
        "min_entry_buy_count": args.min_entry_buy_count,
        "min_entry_volume_30s": args.min_entry_volume_30s,
        "min_entry_price_volatility": args.min_entry_price_volatility,
        "stop_loss": args.stop_loss,
        "position_fraction": args.position_fraction,
        "max_position_fraction": args.max_position_fraction,
        "initial_equity_bnb": args.initial_equity_bnb,
        "fixed_stake_bnb": fixed_stake_bnb,
        "include_trade_log": args.include_trade_log,
        "allow_partial_exits": args.allow_partial_exits,
        "fee_bps": args.fee_bps,
        "slippage_bps": args.slippage_bps,
        "one_entry_per_token": args.one_entry_per_token,
        "max_trades_per_token": args.max_trades_per_token,
        "trailing_start_pct": args.trailing_start_pct,
        "trailing_stop_pct": args.trailing_stop_pct,
        "rug_sell_pressure": args.rug_sell_pressure,
        "risk_tune_buy_threshold": args.risk_tune_buy_threshold,
        "risk_tune_min_trades": args.risk_tune_min_trades,
        "risk_tune_max_trades": args.risk_tune_max_trades,
        "risk_tune_min_threshold": args.risk_tune_min_threshold,
        "risk_tune_target_entry_rate": args.risk_tune_target_entry_rate,
        "risk_tune_entry_rate_penalty": args.risk_tune_entry_rate_penalty,
        "risk_tune_min_entry_rate": args.risk_tune_min_entry_rate,
        "risk_tune_max_entry_rate": args.risk_tune_max_entry_rate,
        "risk_tune_candidate_entry_rates": _parse_float_list(args.risk_tune_candidate_entry_rates),
        "risk_tune_thresholds": _parse_float_list(args.risk_tune_thresholds),
        "risk_tune_max_drawdown_pct": args.risk_tune_max_drawdown_pct,
        "risk_tune_min_win_rate": args.risk_tune_min_win_rate,
        "risk_tune_drawdown_penalty": args.risk_tune_drawdown_penalty,
        "risk_tune_turnover_penalty": args.risk_tune_turnover_penalty,
        "risk_tune_probability_threshold_count": args.risk_tune_probability_threshold_count,
        "sell_drawdown_penalty_weight": args.sell_drawdown_penalty_weight,
        "sell_hold_penalty_per_step": args.sell_hold_penalty_per_step,
        "sell_turnover_penalty": args.sell_turnover_penalty,
        "walk_forward_segments": args.walk_forward_segments,
        "stress_replay": replay_controls["stress_replay"],
        "live_replay_profile": replay_controls["live_replay_profile"],
        "entry_delay_seconds": replay_controls["entry_delay_seconds"],
        "exit_delay_seconds": replay_controls["exit_delay_seconds"],
        "max_open_positions": replay_controls["max_open_positions"],
        "entry_max_fill_wait_seconds": replay_controls["entry_max_fill_wait_seconds"],
        "exit_max_fill_wait_seconds": replay_controls["exit_max_fill_wait_seconds"],
        "entry_price_protection_pct": replay_controls["entry_price_protection_pct"],
        "entry_fixed_cost_bnb": args.entry_fixed_cost_bnb,
        "exit_fixed_cost_bnb": args.exit_fixed_cost_bnb,
        "label_fixed_stake_bnb": args.label_fixed_stake_bnb,
        "label_entry_fixed_cost_bnb": args.label_entry_fixed_cost_bnb,
        "label_exit_fixed_cost_bnb": args.label_exit_fixed_cost_bnb,
        "label_entry_price_protection_pct": args.label_entry_price_protection_pct,
        "entry_execution_failure_rate": replay_controls["entry_execution_failure_rate"],
        "exit_execution_failure_rate": replay_controls["exit_execution_failure_rate"],
        "max_pending_entries": replay_controls["max_pending_entries"],
        "execution_calibration": execution_calibration,
        "catboost_params": {
            "iterations": args.catboost_iterations,
            "learning_rate": args.catboost_learning_rate,
            "depth": args.catboost_depth,
            "l2_leaf_reg": args.catboost_l2_leaf_reg,
            "random_strength": args.catboost_random_strength,
            "bagging_temperature": args.catboost_bagging_temperature,
            "rsm": args.catboost_rsm,
            "od_wait": args.catboost_od_wait,
        },
    }
    buy_max_samples_per_token = getattr(args, "buy_max_samples_per_token", None)
    if buy_max_samples_per_token is not None:
        config["buy_max_samples_per_token"] = buy_max_samples_per_token
    result = run_hybrid_training(config)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    main()

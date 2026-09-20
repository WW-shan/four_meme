"""Sequential DEX-confirmation strategy built on the tail-capture primitives.

The strategy does not buy a curve signal immediately.  It waits for a
post-graduation DEX path, checks a small predeclared health rule, then enters
and manages the position with a fixed/trailing horizon.  This makes the
confirmation decision observable and keeps the early curve model in the role
of candidate discovery rather than pretending it can see the future.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from src.pipeline.tail_capture_strategy import (
    TAIL_FEATURES,
    TailReplayConfig,
    _feature_frame,
    _fit_classifier,
    _fit_ranker,
    _finite,
    _expanding_walk_forward,
    _portfolio_backtest,
    _score_thresholds,
    _tail_utility,
    _token,
    _token_time_splits,
    build_tail_candidates,
    simulate_tail_path)


@dataclass(frozen=True)
class DexConfirmationConfig:
    confirmation_bars: int = 2
    max_confirmation_wait_hours: int = 8
    min_market_volume_ratio: float = 0.80
    min_price_retention_pct: float = -35.0
    min_volume_retention_ratio: float = 0.20
    replay: TailReplayConfig = TailReplayConfig(
        entry_delay_seconds=3,
        exit_delay_seconds=3,
        trailing_stop_pct=40.0,
    )

    def __post_init__(self) -> None:
        if self.confirmation_bars < 1:
            raise ValueError("confirmation_bars must be positive")
        if self.max_confirmation_wait_hours <= 0:
            raise ValueError("max_confirmation_wait_hours must be positive")
        if not 0.0 <= self.min_market_volume_ratio:
            raise ValueError("min_market_volume_ratio must be non-negative")
        if self.min_price_retention_pct >= 0.0:
            raise ValueError("min_price_retention_pct must be negative")
        if not 0.0 <= self.min_volume_retention_ratio:
            raise ValueError("min_volume_retention_ratio must be non-negative")


def _confirmation_bars(candidate: Mapping[str, Any], config: DexConfirmationConfig) -> list[tuple[float, float, float]]:
    boundary = (candidate.get("cross_boundary") or {}).get("graduation_timestamp")
    graduation = _finite(boundary, math.nan)
    if not math.isfinite(graduation):
        return []
    bars = [
        tuple(row)
        for row in candidate.get("dex_bars") or []
        if isinstance(row, (list, tuple)) and len(row) >= 3 and float(row[0]) >= graduation
    ]
    bars.sort(key=lambda row: row[0])
    if not bars:
        return []
    deadline = graduation + float(config.max_confirmation_wait_hours * 3600)
    return [row for row in bars if row[0] <= deadline]


def simulate_confirmed_entry(
    candidate: Mapping[str, Any],
    *,
    horizon_seconds: int,
    config: DexConfirmationConfig | None = None,
) -> dict[str, Any]:
    """Wait for DEX confirmation, then replay the position path causally."""
    config = config or DexConfirmationConfig()
    market_ratio = _finite((candidate.get("features") or {}).get("bsc_dex_volume_vs_7d_avg"), 0.0)
    if market_ratio < config.min_market_volume_ratio:
        return {"status": "regime_rejected", "entry_available": False, "complete": False}
    bars = _confirmation_bars(candidate, config)
    if len(bars) < int(config.confirmation_bars):
        return {"status": "no_dex_confirmation", "entry_available": False, "complete": False}
    selected = bars[: int(config.confirmation_bars)]
    first_price = float(selected[0][1])
    min_price = min(float(row[1]) for row in selected)
    first_volume = float(selected[0][2])
    later_volumes = [float(row[2]) for row in selected[1:] if float(row[2]) >= 0.0]
    volume_ratio = (
        float(np.mean(later_volumes)) / first_volume
        if first_volume > 0.0 and later_volumes
        else 0.0
    )
    price_retention_pct = (min_price / first_price - 1.0) * 100.0 if first_price > 0.0 else -100.0
    if price_retention_pct < config.min_price_retention_pct:
        return {
            "status": "confirmation_rejected_price",
            "entry_available": False,
            "complete": False,
            "confirmation_time": float(selected[-1][0]),
            "confirmation_price_retention_pct": float(price_retention_pct),
            "confirmation_volume_retention_ratio": float(volume_ratio),
        }
    if volume_ratio < config.min_volume_retention_ratio:
        return {
            "status": "confirmation_rejected_volume",
            "entry_available": False,
            "complete": False,
            "confirmation_time": float(selected[-1][0]),
            "confirmation_price_retention_pct": float(price_retention_pct),
            "confirmation_volume_retention_ratio": float(volume_ratio),
        }
    confirmation_time = float(selected[-1][0])
    # Start the regular replay immediately after the confirmed bar.  The
    # synthetic sample time makes the shared simulator apply its normal entry
    # delay and same-second worst-fill rule.
    synthetic = dict(candidate)
    synthetic["sample_time"] = confirmation_time - config.replay.entry_delay_seconds
    outcome = simulate_tail_path(synthetic, horizon_seconds=horizon_seconds, config=config.replay)
    outcome.update(
        {
            "confirmation_passed": True,
            "confirmation_time": confirmation_time,
            "confirmation_bars": int(config.confirmation_bars),
            "confirmation_price_retention_pct": float(price_retention_pct),
            "confirmation_volume_retention_ratio": float(volume_ratio),
            "market_volume_ratio": float(market_ratio),
        }
    )
    outcome["entry_available"] = outcome.get("status") not in {"missing_entry", "missing_exit"}
    return outcome


def _as_trade_outcome(outcome: Mapping[str, Any]) -> dict[str, Any]:
    """Map confirmation rejects to an explicit no-entry status for replay."""
    row = dict(outcome)
    if not bool(row.get("entry_available")):
        row["status"] = "missing_entry"
    return row


def train_dex_confirmation_experiment(
    lifecycles: Sequence[Mapping[str, Any]],
    *,
    horizon_seconds: int,
    confirmation_config: DexConfirmationConfig | None = None,
    feature_names: Sequence[str] = TAIL_FEATURES,
    market_context: Mapping[str, Mapping[str, Any]] | None = None,
    max_candidates_per_token: int = 3,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Train a fresh ranker on confirmed-entry utility and replay capital."""
    config = confirmation_config or DexConfirmationConfig()
    feature_names = list(feature_names)
    candidates = build_tail_candidates(
        lifecycles,
        config.replay,
        feature_names=feature_names,
        max_candidates_per_token=max_candidates_per_token,
        market_context=market_context,
    )
    outcomes = [
        _as_trade_outcome(
            simulate_confirmed_entry(candidate, horizon_seconds=horizon_seconds, config=config)
        )
        for candidate in candidates
    ]
    splits = _token_time_splits(candidates, int(horizon_seconds))
    eligible = [
        index
        for index, outcome in enumerate(outcomes)
        if bool(outcome.get("complete")) and bool(outcome.get("confirmation_passed")) and outcome.get("status") == "ok"
    ]
    train_indices = [index for index in splits["train"] if index in eligible]
    validation_indices = [index for index in splits["validation"] if index in eligible]
    final_indices = [index for index in splits["final"] if index in eligible]
    result: dict[str, Any] = {
        "schema_version": 1,
        "experiment_type": "bsc_sequential_dex_confirmation_ranker",
        "inputs": {
            "horizon_seconds": int(horizon_seconds),
            "candidate_count": len(candidates),
            "token_count": len({_token(row.get("token")) for row in candidates}),
            "feature_names": feature_names,
            "confirmation_config": {
                "confirmation_bars": config.confirmation_bars,
                "max_confirmation_wait_hours": config.max_confirmation_wait_hours,
                "min_market_volume_ratio": config.min_market_volume_ratio,
                "min_price_retention_pct": config.min_price_retention_pct,
                "min_volume_retention_ratio": config.min_volume_retention_ratio,
                "replay": {key: getattr(config.replay, key) for key in config.replay.__dataclass_fields__},
            },
        },
        "path_coverage": {
            "candidate_count": len(candidates),
            "confirmed_entry_count": int(sum(bool(row.get("confirmation_passed")) for row in outcomes)),
            "complete_confirmed_count": len(eligible),
            "confirmation_rate": float(sum(bool(row.get("confirmation_passed")) for row in outcomes) / len(candidates)) if candidates else 0.0,
            "status_counts": {
                status: sum(row.get("status") == status for row in outcomes)
                for status in sorted({str(row.get("status")) for row in outcomes})
            },
        },
        "decision": "research_only_until_final_walk_forward_and_stress_support",
        "safe_for_live_switch": False,
    }
    if len(train_indices) < 20 or len(validation_indices) < 5 or len(final_indices) < 5:
        result["status"] = "insufficient_complete_confirmed_paths"
        result["reason"] = "confirmed complete train/validation/final support is too small"
        return result
    labels = np.asarray(
        [_tail_utility(_finite(outcome.get("conservative_return_pct"), -100.0)) for outcome in outcomes],
        dtype=float,
    )
    ranker = _fit_ranker(
        candidates,
        feature_names,
        train_indices,
        validation_indices,
        labels,
        {"iterations": 280, "depth": 4, "l2_leaf_reg": 20.0},
        ranking_group_seconds=config.replay.ranking_group_seconds,
    )
    classifier = _fit_classifier(
        candidates,
        feature_names,
        train_indices,
        validation_indices,
        np.asarray([1 if _finite(row.get("conservative_return_pct"), -100.0) > 0 else 0 for row in outcomes]),
    )
    all_indices = list(range(len(candidates)))
    scores = ranker.predict(_feature_frame(candidates, all_indices, feature_names))
    thresholds = _score_thresholds([scores[index] for index in train_indices])
    validation_grid = [
        _portfolio_backtest(candidates, outcomes, scores, splits["validation"], config.replay, threshold)
        for threshold in thresholds
    ]
    eligible_grid = [row for row in validation_grid if row.get("trade_count", 0) >= 5]
    selected = max(eligible_grid or validation_grid, key=lambda row: (row["net_profit_bnb"], row["net_return_pct"], -row["trade_count"]))
    if selected["net_profit_bnb"] <= 0:
        no_trade = [row for row in validation_grid if row.get("trade_count", 0) == 0]
        if no_trade:
            selected = no_trade[0]
    selected_threshold = float(selected["threshold"])
    baseline_scores = [0.0] * len(candidates)
    model_indices = {"train": train_indices, "validation": validation_indices, "final": final_indices}
    split_reports = {}
    for name, indices in (("train", splits["train"]), ("validation", splits["validation"]), ("final", splits["final"])):
        split_reports[name] = {
            "candidate_count": len(indices),
            "confirmed_entry_count": int(sum(bool(outcomes[index].get("confirmation_passed")) for index in indices)),
            "complete_confirmed_count": int(sum(index in eligible for index in indices)),
            "portfolio": _portfolio_backtest(candidates, outcomes, scores, indices, config.replay, selected_threshold),
            "confirmed_entry_baseline": _portfolio_backtest(candidates, outcomes, baseline_scores, indices, config.replay, -1e30),
            "complete_only_portfolio": _portfolio_backtest(candidates, outcomes, scores, model_indices[name], config.replay, selected_threshold),
        }
    stress_reports = {}
    for stress_name, replay in (
        ("base", config.replay),
        ("moderate", replace(config.replay, fee_bps=max(config.replay.fee_bps, 150.0), slippage_bps=max(config.replay.slippage_bps, 400.0))),
        ("harsh", replace(config.replay, fee_bps=max(config.replay.fee_bps, 250.0), slippage_bps=max(config.replay.slippage_bps, 800.0))),
    ):
        stress_outcomes = [
            _as_trade_outcome(simulate_confirmed_entry(candidate, horizon_seconds=horizon_seconds, config=replace(config, replay=replay)))
            for candidate in candidates
        ]
        stress_reports[stress_name] = {
            "validation": _portfolio_backtest(candidates, stress_outcomes, scores, splits["validation"], replay, selected_threshold),
            "final": _portfolio_backtest(candidates, stress_outcomes, scores, splits["final"], replay, selected_threshold),
            "complete_only": _portfolio_backtest(candidates, stress_outcomes, scores, final_indices, replay, selected_threshold),
        }
    result.update(
        {
            "status": "ok",
            "selected_threshold": selected_threshold,
            "threshold_grid_validation": validation_grid,
            "splits": split_reports,
            "stress_replay": stress_reports,
            "walk_forward": _expanding_walk_forward(
                candidates,
                outcomes,
                labels,
                horizon_seconds=horizon_seconds,
                config=config.replay,
                feature_names=feature_names,
                complete_indices=set(eligible),
            ),
            "feature_importance": [
                {"feature": name, "importance": float(value)}
                for name, value in sorted(
                    zip(feature_names, ranker.get_feature_importance(type="PredictionValuesChange")),
                    key=lambda item: float(item[1]),
                    reverse=True,
                )[:30]
            ],
            "model": {
                "ranker": "trained_fresh",
                "classifier": "trained_fresh" if classifier is not None else "unavailable_single_class",
            },
        }
    )
    final = split_reports["final"]["portfolio"]
    final_complete = split_reports["final"]["complete_only_portfolio"]
    checks = {
        "minimum_final_trades_20": final["trade_count"] >= 20,
        "minimum_final_complete_trades_10": final_complete["trade_count"] >= 10,
        "final_net_profit_positive": _finite(final.get("net_profit_bnb"), -math.inf) > 0,
        "final_complete_only_net_profit_positive": _finite(final_complete.get("net_profit_bnb"), -math.inf) > 0,
        "moderate_stress_non_negative": _finite(stress_reports["moderate"]["final"].get("net_profit_bnb"), -math.inf) >= 0,
        "harsh_stress_non_negative": _finite(stress_reports["harsh"]["final"].get("net_profit_bnb"), -math.inf) >= 0,
        "two_positive_walk_forward_folds": sum(
            _finite((row.get("portfolio") or {}).get("net_profit_bnb"), -math.inf) > 0
            for row in result.get("walk_forward", [])
            if row.get("status") == "ok"
        ) >= 2,
    }
    result["research_acceptance"] = {
        "checks": checks,
        "passes": bool(all(checks.values())),
        "runtime_switch": "none",
    }
    if output_dir is not None:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        ranker_path = output / "confirmation_ranker.cbm"
        ranker.save_model(str(ranker_path))
        classifier_path = None
        if classifier is not None:
            classifier_path = output / "confirmation_classifier.cbm"
            classifier.save_model(str(classifier_path))
        (output / "feature_schema.json").write_text(json.dumps({"feature_names": feature_names}, indent=2) + "\n", encoding="utf-8")
        result["artifacts"] = {
            "ranker": str(ranker_path),
            "classifier": str(classifier_path) if classifier_path else None,
            "feature_schema": str(output / "feature_schema.json"),
        }
    return result


__all__ = ["DexConfirmationConfig", "simulate_confirmed_entry", "train_dex_confirmation_experiment"]

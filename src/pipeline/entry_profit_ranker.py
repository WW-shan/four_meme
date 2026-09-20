"""Train and evaluate entry-time barrier classifiers and profit rankers.

The model target is tied to an executable horizon rather than the old
ultra-short label.  Ranking is included because a few large winners can drive
portfolio profit even when a binary hit-rate objective is not sufficient.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

try:
    from catboost import Pool
except Exception:  # pragma: no cover - the model classes provide the runtime error
    Pool = None

from src.model.buy_catboost import CatBoostClassifier, CatBoostRanker
from src.pipeline.target_barrier_profile import (
    BarrierReplayConfig,
    DEFAULT_FEATURES,
    build_entry_candidates,
    chronological_splits,
    simulate_barrier,
    summarize_outcomes,
)


DEFAULT_MODEL_PARAMS = {
    "iterations": 400,
    "learning_rate": 0.05,
    "depth": 6,
    "l2_leaf_reg": 10.0,
    "random_strength": 1.0,
    "od_type": "Iter",
    "od_wait": 40,
}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if math.isfinite(parsed) else float(default)


def signed_log_return(return_pct: float) -> float:
    """Compress heavy tails while preserving the sign and ordering."""

    fraction = max(-0.999, min(100.0, float(return_pct) / 100.0))
    return float(math.copysign(math.log1p(abs(fraction)), fraction))


def _feature_frame(candidates: Sequence[Mapping[str, Any]], feature_names: Sequence[str]) -> pd.DataFrame:
    rows = []
    for candidate in candidates:
        features = candidate.get("features") or {}
        rows.append({name: _finite(features.get(name)) for name in feature_names})
    return pd.DataFrame(rows, columns=list(feature_names), dtype=float)


def _split_indices(
    candidates: Sequence[Mapping[str, Any]],
    *,
    purge_seconds: int = 0,
) -> dict[str, list[int]]:
    ordered = sorted(
        range(len(candidates)),
        key=lambda index: (int(candidates[index].get("sample_time", 0)), str(candidates[index].get("token"))),
    )
    if not ordered:
        return {"train": [], "validation": [], "final": []}
    train_end = max(1, int(len(ordered) * 0.60))
    validation_end = min(len(ordered), max(train_end + 1, int(len(ordered) * 0.80)))
    validation_start = int(candidates[ordered[train_end]]["sample_time"]) if train_end < len(ordered) else None
    final_start = int(candidates[ordered[validation_end]]["sample_time"]) if validation_end < len(ordered) else None
    train = ordered[:train_end]
    validation = ordered[train_end:validation_end]
    final = ordered[validation_end:]
    if purge_seconds > 0:
        if validation_start is not None:
            train = [
                index for index in train
                if int(candidates[index].get("sample_time", 0)) + int(purge_seconds) < validation_start
            ]
        if final_start is not None:
            validation = [
                index for index in validation
                if int(candidates[index].get("sample_time", 0)) + int(purge_seconds) < final_start
            ]
    return {"train": train, "validation": validation, "final": final}


def _group_ids(candidates: Sequence[Mapping[str, Any]], indices: Sequence[int]) -> list[int]:
    groups: dict[str, int] = {}
    result = []
    for index in indices:
        timestamp = _finite(candidates[index].get("sample_time"))
        day = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
        if day not in groups:
            groups[day] = len(groups) + 1
        result.append(groups[day])
    return result


def _ordered_group_indices(candidates: Sequence[Mapping[str, Any]], indices: Sequence[int]) -> list[int]:
    return sorted(
        indices,
        key=lambda index: (
            datetime.fromtimestamp(_finite(candidates[index].get("sample_time")), tz=timezone.utc).strftime("%Y-%m-%d"),
            int(candidates[index].get("sample_time", 0)),
        ),
    )


def _top_k_indices(indices: Sequence[int], scores: Sequence[float], k: int) -> list[int]:
    if not indices or k <= 0:
        return []
    ordered = sorted(indices, key=lambda index: (_finite(scores[index], -math.inf), -index), reverse=True)
    return ordered[: min(int(k), len(ordered))]


def _top_fraction_indices(indices: Sequence[int], scores: Sequence[float], fraction: float) -> list[int]:
    count = max(1, int(math.ceil(len(indices) * float(fraction)))) if indices else 0
    return _top_k_indices(indices, scores, count)


def _top_per_hour_indices(
    candidates: Sequence[Mapping[str, Any]],
    indices: Sequence[int],
    scores: Sequence[float],
    per_hour: int,
) -> list[int]:
    buckets: defaultdict[str, list[int]] = defaultdict(list)
    for index in indices:
        timestamp = _finite(candidates[index].get("sample_time"))
        hour = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H")
        buckets[hour].append(index)
    selected = []
    for bucket in buckets.values():
        selected.extend(_top_k_indices(bucket, scores, per_hour))
    return sorted(selected, key=lambda index: int(candidates[index].get("sample_time", 0)))


def _prediction_summary(
    candidates: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    scores: Sequence[float],
    indices: Sequence[int],
    *,
    fixed_stake_bnb: float,
) -> dict[str, Any]:
    rows = []
    for label, selector in (
        ("top_1pct", lambda: _top_fraction_indices(indices, scores, 0.01)),
        ("top_5pct", lambda: _top_fraction_indices(indices, scores, 0.05)),
        ("top_10pct", lambda: _top_fraction_indices(indices, scores, 0.10)),
        ("top_25", lambda: _top_k_indices(indices, scores, 25)),
        ("top_50", lambda: _top_k_indices(indices, scores, 50)),
        ("top_1_per_hour", lambda: _top_per_hour_indices(candidates, indices, scores, 1)),
        ("top_3_per_hour", lambda: _top_per_hour_indices(candidates, indices, scores, 3)),
    ):
        selected = selector()
        summary = summarize_outcomes(
            [outcomes[index] for index in selected],
            fixed_stake_bnb=fixed_stake_bnb,
        )
        rows.append(
            {
                "selection": label,
                "candidate_count": len(selected),
                "summary": summary["conservative_all_candidates"],
                "complete_only": summary["complete_only"],
            }
        )
    baseline = summarize_outcomes(
        [outcomes[index] for index in indices],
        fixed_stake_bnb=fixed_stake_bnb,
    )
    return {
        "baseline_all": baseline["conservative_all_candidates"],
        "baseline_complete_only": baseline["complete_only"],
        "ranked": rows,
    }


def _portfolio_backtest(
    candidates: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    scores: Sequence[float],
    indices: Sequence[int],
    *,
    threshold: float,
    initial_equity_bnb: float,
    fixed_stake_bnb: float,
    max_open_positions: int | None,
) -> dict[str, Any]:
    """Execute score-threshold signals with cash and open-position limits."""

    if initial_equity_bnb <= 0.0 or fixed_stake_bnb <= 0.0:
        return {
            "threshold": float(threshold),
            "trade_count": 0,
            "signal_count": 0,
            "capacity_skips": 0,
            "cash_insufficient_skips": 0,
            "entry_failures": 0,
            "initial_equity_bnb": float(initial_equity_bnb),
            "final_equity_bnb": float(initial_equity_bnb),
            "net_profit_bnb": 0.0,
            "net_return_pct": 0.0,
        }
    ordered = sorted(indices, key=lambda index: (int(candidates[index].get("sample_time", 0)), str(candidates[index].get("token"))))
    cash = float(initial_equity_bnb)
    open_positions: list[tuple[float, float, float]] = []
    equity_points = [cash]
    signal_count = 0
    capacity_skips = 0
    cash_skips = 0
    entry_failures = 0

    def close_until(timestamp: float) -> None:
        nonlocal cash, open_positions
        remaining = []
        for exit_time, stake, return_pct in open_positions:
            if exit_time <= timestamp:
                cash += stake * max(0.0, 1.0 + return_pct / 100.0)
                equity_points.append(cash)
            else:
                remaining.append((exit_time, stake, return_pct))
        open_positions = remaining

    for index in ordered:
        if _finite(scores[index], -math.inf) < float(threshold):
            continue
        signal_count += 1
        outcome = outcomes[index]
        if outcome.get("status") in {"missing_entry", "entry_timeout", "entry_protection_skip"}:
            entry_failures += 1
            continue
        entry_time = _finite(outcome.get("entry_time"), _finite(candidates[index].get("sample_time")))
        close_until(entry_time)
        if max_open_positions is not None and len(open_positions) >= int(max_open_positions):
            capacity_skips += 1
            continue
        if cash + 1e-12 < fixed_stake_bnb:
            cash_skips += 1
            continue
        cash -= fixed_stake_bnb
        return_pct = _finite(outcome.get("conservative_return_pct"), -100.0)
        exit_time = _finite(outcome.get("exit_time"), entry_time)
        open_positions.append((max(entry_time, exit_time), fixed_stake_bnb, return_pct))

    close_until(float("inf"))
    final_equity = float(cash)
    peak = float(initial_equity_bnb)
    max_drawdown = 0.0
    for point in equity_points:
        peak = max(peak, point)
        if peak > 0.0:
            max_drawdown = max(max_drawdown, (peak - point) / peak)
    trade_count = signal_count - capacity_skips - cash_skips - entry_failures
    return {
        "threshold": float(threshold),
        "trade_count": int(max(0, trade_count)),
        "signal_count": int(signal_count),
        "capacity_skips": int(capacity_skips),
        "cash_insufficient_skips": int(cash_skips),
        "entry_failures": int(entry_failures),
        "initial_equity_bnb": float(initial_equity_bnb),
        "final_equity_bnb": final_equity,
        "net_profit_bnb": float(final_equity - initial_equity_bnb),
        "net_return_pct": float((final_equity / initial_equity_bnb - 1.0) * 100.0),
        "max_drawdown_pct": float(max_drawdown * 100.0),
    }


def _score_thresholds(scores: Sequence[float], *, classifier: bool = False) -> list[float]:
    values = sorted({_finite(value, math.nan) for value in scores if math.isfinite(_finite(value, math.nan))})
    if not values:
        return [1.0]
    if classifier:
        return sorted({0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.98, 1.0})
    thresholds = {
        float(values[0]),
        *(_finite(np.quantile(values, quantile)) for quantile in (0.50, 0.70, 0.80, 0.90, 0.95, 0.98)),
        float(values[-1]),
    }
    return sorted(value for value in thresholds if math.isfinite(value))


def _select_portfolio_threshold(
    candidates: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    scores: Sequence[float],
    validation_indices: Sequence[int],
    *,
    initial_equity_bnb: float,
    fixed_stake_bnb: float,
    max_open_positions: int | None,
    classifier: bool,
    min_trades: int = 5,
) -> tuple[float, list[dict[str, Any]]]:
    grid = [
        _portfolio_backtest(
            candidates,
            outcomes,
            scores,
            validation_indices,
            threshold=threshold,
            initial_equity_bnb=initial_equity_bnb,
            fixed_stake_bnb=fixed_stake_bnb,
            max_open_positions=max_open_positions,
        )
        for threshold in _score_thresholds([scores[index] for index in validation_indices], classifier=classifier)
    ]
    eligible = [row for row in grid if int(row.get("trade_count", 0)) >= int(min_trades)]
    selected = max(
        eligible or grid,
        key=lambda row: (
            _finite(row.get("net_profit_bnb")),
            _finite(row.get("net_return_pct")),
            -int(row.get("trade_count", 0)),
        ),
    ) if grid else {"threshold": 1.0}
    # When every trade-producing threshold loses money, prefer the explicit
    # no-trade point over a negative threshold.  This makes the report's
    # runtime decision fail closed instead of presenting a bad gate as best.
    if selected.get("net_profit_bnb", 0.0) <= 0.0:
        no_trade = max(
            (row for row in grid if int(row.get("trade_count", 0)) == 0),
            key=lambda row: _finite(row.get("net_profit_bnb")),
            default=None,
        )
        if no_trade is not None and _finite(no_trade.get("net_profit_bnb")) >= _finite(selected.get("net_profit_bnb")):
            selected = no_trade
    return float(selected.get("threshold", 1.0)), grid


def _fit_classifier(
    X_train: pd.DataFrame,
    y_train: np.ndarray,
    X_eval: pd.DataFrame,
    y_eval: np.ndarray,
    *,
    random_seed: int,
    params: Mapping[str, Any] | None = None,
):
    model_params = dict(DEFAULT_MODEL_PARAMS)
    model_params.update(dict(params or {}))
    model = CatBoostClassifier(
        loss_function="Logloss",
        eval_metric="AUC",
        random_seed=random_seed,
        verbose=False,
        allow_writing_files=False,
        auto_class_weights="Balanced",
        **model_params,
    )
    eval_set = (X_eval, y_eval) if len(y_eval) and len(set(y_eval.tolist())) > 1 else None
    model.fit(X_train, y_train, eval_set=eval_set, use_best_model=bool(eval_set))
    return model


def _fit_ranker(
    candidates: Sequence[Mapping[str, Any]],
    X: pd.DataFrame,
    y: np.ndarray,
    train_indices: Sequence[int],
    validation_indices: Sequence[int],
    *,
    random_seed: int,
    params: Mapping[str, Any] | None = None,
):
    model_params = dict(DEFAULT_MODEL_PARAMS)
    model_params.update(dict(params or {}))
    fit_indices = _ordered_group_indices(candidates, train_indices)
    eval_indices = _ordered_group_indices(candidates, validation_indices)
    model = CatBoostRanker(
        loss_function="YetiRank",
        eval_metric="NDCG:top=50",
        random_seed=random_seed,
        verbose=False,
        allow_writing_files=False,
        **model_params,
    )
    if Pool is None:
        raise ModuleNotFoundError("catboost is required to train the profit ranker")
    train_pool = Pool(
        X.iloc[fit_indices],
        label=y[fit_indices],
        group_id=_group_ids(candidates, fit_indices),
    )
    eval_pool = (
        Pool(
            X.iloc[eval_indices],
            label=y[eval_indices],
            group_id=_group_ids(candidates, eval_indices),
        )
        if eval_indices
        else None
    )
    model.fit(train_pool, eval_set=eval_pool, use_best_model=bool(eval_pool))
    return model


def train_profit_ranker_experiment(
    lifecycles: Sequence[Mapping[str, Any]],
    *,
    horizon_seconds: int,
    target_return_pct: float = 20.0,
    stop_loss_pct: float = -30.0,
    config: BarrierReplayConfig | None = None,
    feature_names: Sequence[str] = DEFAULT_FEATURES,
    model_params: Mapping[str, Any] | None = None,
    random_seed: int = 42,
    output_dir: str | Path | None = None,
    label_mode: str = "barrier",
) -> dict[str, Any]:
    """Fit a barrier classifier and a profit ranker on chronological splits.

    ``barrier`` uses the configured target as an executable upper barrier.
    ``uncapped_horizon`` removes that upper barrier for the ranker label so a
    multi-x winner is not clipped at +20%; the classifier then labels a
    cost-adjusted horizon return above ``target_return_pct``.
    """

    config = config or BarrierReplayConfig()
    label_mode = str(label_mode or "barrier").strip().lower()
    if label_mode not in {"barrier", "uncapped_horizon"}:
        raise ValueError("label_mode must be barrier or uncapped_horizon")
    candidates = build_entry_candidates(lifecycles, config, include_features=True)
    outcomes = [
        simulate_barrier(
            candidate,
            horizon_seconds=horizon_seconds,
            target_return_pct=(target_return_pct if label_mode == "barrier" else 1_000_000_000.0),
            stop_loss_pct=stop_loss_pct,
            config=config,
        )
        for candidate in candidates
    ]
    feature_names = list(feature_names)
    X = _feature_frame(candidates, feature_names)
    if label_mode == "barrier":
        y_hit = np.asarray([1 if row.get("reason") == "target" else 0 for row in outcomes], dtype=int)
    else:
        y_hit = np.asarray(
            [
                1
                if _finite(row.get("conservative_return_pct"), -100.0) >= float(target_return_pct)
                else 0
                for row in outcomes
            ],
            dtype=int,
        )
    y_profit = np.asarray(
        [signed_log_return(_finite(row.get("conservative_return_pct"), -100.0)) for row in outcomes],
        dtype=float,
    )
    # Purge samples whose future label window crosses the next split boundary.
    # Without this gap, a train/validation label can observe the same market
    # regime that the following split is meant to represent.
    splits = _split_indices(candidates, purge_seconds=int(horizon_seconds))
    train_indices = splits["train"]
    validation_indices = splits["validation"]
    final_indices = splits["final"]
    if len(set(y_hit[train_indices])) < 2:
        raise ValueError("training barrier labels contain only one class")
    classifier = _fit_classifier(
        X.iloc[train_indices],
        y_hit[train_indices],
        X.iloc[validation_indices],
        y_hit[validation_indices],
        random_seed=random_seed,
        params=model_params,
    )
    ranker = _fit_ranker(
        candidates,
        X,
        y_profit,
        train_indices,
        validation_indices,
        random_seed=random_seed,
        params=model_params,
    )
    classifier_scores = classifier.predict_proba(X)[:, 1]
    ranker_scores = ranker.predict(X)

    split_reports: dict[str, Any] = {}
    for split_name, indices in splits.items():
        split_reports[split_name] = {
            "candidate_count": len(indices),
            "label_positive_count": int(sum(y_hit[index] for index in indices)),
            "label_positive_rate": float(np.mean(y_hit[indices])) if indices else 0.0,
            "classifier": _prediction_summary(
                candidates,
                outcomes,
                classifier_scores,
                indices,
                fixed_stake_bnb=config.fixed_stake_bnb,
            ),
            "profit_ranker": _prediction_summary(
                candidates,
                outcomes,
                ranker_scores,
                indices,
                fixed_stake_bnb=config.fixed_stake_bnb,
            ),
        }

    portfolio_reports: dict[str, Any] = {}
    for model_name, scores, is_classifier in (
        ("classifier", classifier_scores, True),
        ("profit_ranker", ranker_scores, False),
    ):
        selected_threshold, threshold_grid = _select_portfolio_threshold(
            candidates,
            outcomes,
            scores,
            validation_indices,
            initial_equity_bnb=config.initial_equity_bnb,
            fixed_stake_bnb=config.fixed_stake_bnb,
            max_open_positions=config.max_open_positions,
            classifier=is_classifier,
        )
        split_portfolios = {}
        for split_name, indices in splits.items():
            split_portfolios[split_name] = _portfolio_backtest(
                candidates,
                outcomes,
                scores,
                indices,
                threshold=selected_threshold,
                initial_equity_bnb=config.initial_equity_bnb,
                fixed_stake_bnb=config.fixed_stake_bnb,
                max_open_positions=config.max_open_positions,
            )
        validation_profit = _finite(split_portfolios["validation"].get("net_profit_bnb"))
        final_profit = _finite(split_portfolios["final"].get("net_profit_bnb"))
        portfolio_reports[model_name] = {
            "selected_threshold": selected_threshold,
            "threshold_grid_validation": threshold_grid,
            "splits": split_portfolios,
            "accepted_for_runtime": bool(validation_profit > 0.0 and final_profit > 0.0),
        }

    result: dict[str, Any] = {
        "schema_version": 1,
        "experiment_type": "bsc_entry_barrier_classifier_and_profit_ranker",
        "inputs": {
            "horizon_seconds": int(horizon_seconds),
            "target_return_pct": float(target_return_pct),
            "stop_loss_pct": float(stop_loss_pct),
            "label_mode": label_mode,
            "candidate_count": len(candidates),
            "feature_names": feature_names,
            "config": {key: getattr(config, key) for key in config.__dataclass_fields__},
            "model_params": dict(model_params or DEFAULT_MODEL_PARAMS),
        },
        "time_splits": {
            name: {
                "candidate_count": len(indices),
                "first_sample_time": int(candidates[indices[0]]["sample_time"]) if indices else None,
                "last_sample_time": int(candidates[indices[-1]]["sample_time"]) if indices else None,
            }
            for name, indices in splits.items()
        },
        "label_diagnostics": {
            "barrier_positive_count": int(np.sum(y_hit)),
            "barrier_positive_rate": float(np.mean(y_hit)) if len(y_hit) else 0.0,
            "conservative_return": summarize_outcomes(
                outcomes,
                fixed_stake_bnb=config.fixed_stake_bnb,
            )["conservative_all_candidates"],
        },
        "splits": split_reports,
        "portfolio_backtest": portfolio_reports,
        "feature_importance": {
            "classifier": sorted(
                (
                    {"feature": name, "importance": float(value)}
                    for name, value in zip(feature_names, classifier.get_feature_importance())
                ),
                key=lambda row: row["importance"],
                reverse=True,
            )[:25],
            "profit_ranker": sorted(
                (
                    {"feature": name, "importance": float(value)}
                    for name, value in zip(
                        feature_names,
                        ranker.get_feature_importance(type="PredictionValuesChange"),
                    )
                ),
                key=lambda row: row["importance"],
                reverse=True,
            )[:25],
        },
        "decision": {
            "runtime_switch": "none",
            "selection_metric": "validation_total_return_pct_then_net_profit_bnb",
            "final_is_holdout": True,
        },
    }
    if output_dir is not None:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        classifier.save_model(str(output / "barrier_classifier.cbm"))
        ranker.save_model(str(output / "profit_ranker.cbm"))
        (output / "feature_schema.json").write_text(
            json.dumps({"feature_names": feature_names}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result["artifacts"] = {
            "classifier": str(output / "barrier_classifier.cbm"),
            "profit_ranker": str(output / "profit_ranker.cbm"),
            "feature_schema": str(output / "feature_schema.json"),
        }
    return result


__all__ = ["signed_log_return", "train_profit_ranker_experiment"]

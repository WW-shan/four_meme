"""Profile delayed, cost-adjusted outcomes across holding horizons.

The profile is deliberately separate from model selection.  It answers whether a
longer *executable* holding window has support in the observed lifecycle data;
future peak returns are reported only as a hindsight diagnostic.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from src.data.dataset_builder import DatasetBuilder


DEFAULT_HORIZONS = (300, 1_800, 7_200, 21_600, 86_400)
RETURN_FIELDS = (
    "live_cost_adjusted_final_return_pct",
    "live_delay_robust_return_pct",
    "live_executable_return_pct",
    "live_risk_adjusted_return_pct",
    "live_cost_adjusted_min_return_pct",
    "live_cost_adjusted_max_return_pct",
    "cost_adjusted_max_return_pct",
)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = round((len(ordered) - 1) * float(probability))
    return float(ordered[max(0, min(len(ordered) - 1, index))])


def distribution(values: Iterable[Any]) -> dict[str, float | int]:
    cleaned = [_finite(value) for value in values]
    if not cleaned:
        return {
            "count": 0,
            "mean": 0.0,
            "median": 0.0,
            "p10": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "p90": 0.0,
            "positive_rate": 0.0,
            "nonpositive_rate": 0.0,
        }
    return {
        "count": int(len(cleaned)),
        "mean": float(statistics.mean(cleaned)),
        "median": float(statistics.median(cleaned)),
        "p10": _percentile(cleaned, 0.10),
        "p25": _percentile(cleaned, 0.25),
        "p75": _percentile(cleaned, 0.75),
        "p90": _percentile(cleaned, 0.90),
        "positive_rate": float(sum(value > 0.0 for value in cleaned) / len(cleaned)),
        "nonpositive_rate": float(sum(value <= 0.0 for value in cleaned) / len(cleaned)),
    }


def _rows_for_horizon(samples: Iterable[Mapping[str, Any]], horizon_seconds: int) -> list[dict]:
    rows = []
    for sample in samples:
        label = sample.get("label") or {}
        if _int(label.get("future_window_seconds", sample.get("meta", {}).get("future_window", 0))) != int(
            horizon_seconds
        ):
            continue
        rows.append(dict(sample))
    return rows


def _token(row: Mapping[str, Any]) -> str:
    return str((row.get("meta") or {}).get("token_address") or "").strip().lower()


def _sample_time(row: Mapping[str, Any]) -> int:
    return _int((row.get("meta") or {}).get("sample_time"))


def _latest_per_token(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    latest: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        token = _token(row)
        if not token:
            continue
        previous = latest.get(token)
        if previous is None or _sample_time(row) >= _sample_time(previous):
            latest[token] = row
    return list(latest.values())


def _rate(rows: Sequence[Mapping[str, Any]], field: str, predicate) -> float:
    if not rows:
        return 0.0
    return float(
        sum(bool(predicate(_finite((row.get("label") or {}).get(field)))) for row in rows)
        / len(rows)
    )


def _time_distribution(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, float | int]:
    values = [
        _finite((row.get("label") or {}).get(field))
        for row in rows
        if _finite((row.get("label") or {}).get(field)) > 0.0
    ]
    return distribution(values)


def summarize_horizon(
    samples: Iterable[Mapping[str, Any]],
    horizon_seconds: int,
    *,
    baseline_tokens: set[str] | None = None,
    minimum_token_support: int = 50,
) -> dict[str, Any]:
    """Summarize one horizon without selecting a live strategy."""

    rows = _rows_for_horizon(samples, horizon_seconds)
    token_rows: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        token = _token(row)
        if token:
            token_rows[token].append(row)
    tokens = set(token_rows)
    latest_rows = _latest_per_token(rows)

    returns = {
        field: distribution((row.get("label") or {}).get(field) for row in rows)
        for field in RETURN_FIELDS
    }
    latest_final = distribution(
        (row.get("label") or {}).get("live_cost_adjusted_final_return_pct") for row in latest_rows
    )
    latest_robust = distribution(
        (row.get("label") or {}).get("live_delay_robust_return_pct") for row in latest_rows
    )
    if baseline_tokens is None:
        token_survival = 1.0
    elif baseline_tokens:
        token_survival = float(len(tokens & baseline_tokens) / len(baseline_tokens))
    else:
        token_survival = 0.0

    giveback = [
        _finite((row.get("label") or {}).get("live_cost_adjusted_max_return_pct"))
        - _finite((row.get("label") or {}).get("live_cost_adjusted_final_return_pct"))
        for row in rows
    ]
    rejection_reasons = []
    if len(tokens) < int(minimum_token_support):
        rejection_reasons.append("insufficient_token_support")
    if returns["live_delay_robust_return_pct"]["mean"] <= 0.0:
        rejection_reasons.append("nonpositive_delay_robust_mean")
    if returns["live_delay_robust_return_pct"]["median"] <= 0.0:
        rejection_reasons.append("nonpositive_delay_robust_median")
    if returns["live_cost_adjusted_final_return_pct"]["mean"] <= 0.0:
        rejection_reasons.append("nonpositive_final_mean")

    row_count = len(rows)
    sample_counts = [len(bucket) for bucket in token_rows.values()]
    result = {
        "horizon_seconds": int(horizon_seconds),
        "horizon_minutes": float(horizon_seconds / 60.0),
        "support": {
            "sample_count": int(row_count),
            "token_count": int(len(tokens)),
            "latest_token_count": int(len(latest_rows)),
            "sample_count_per_token_mean": float(statistics.mean(sample_counts)) if sample_counts else 0.0,
            "sample_count_per_token_median": float(statistics.median(sample_counts)) if sample_counts else 0.0,
            "token_survival_vs_shortest": token_survival,
        },
        "returns": returns,
        "latest_token_returns": {
            "live_cost_adjusted_final_return_pct": latest_final,
            "live_delay_robust_return_pct": latest_robust,
        },
        "path": {
            "mfe_to_final_giveback_pct": distribution(giveback),
            "target_hit_before_stop_rate": _rate(rows, "live_target_hit_before_stop", lambda value: value >= 1.0),
            "stop_hit_before_target_rate": _rate(rows, "live_stop_hit_before_target", lambda value: value >= 1.0),
            "entry_available_rate": _rate(rows, "live_entry_available", lambda value: value >= 1.0),
            "entry_blocked_by_price_protection_rate": _rate(
                rows, "live_entry_blocked_by_price_protection", lambda value: value >= 1.0
            ),
            "time_to_target_seconds": _time_distribution(rows, "live_time_to_target_seconds"),
            "time_to_stop_seconds": _time_distribution(rows, "live_time_to_stop_seconds"),
        },
        "decision": {
            "minimum_token_support": int(minimum_token_support),
            "research_candidate": not rejection_reasons,
            "rejection_reasons": rejection_reasons,
            "hindsight_peak_warning": True,
        },
    }
    return result


def build_horizon_profile(config: Mapping[str, Any]) -> dict[str, Any]:
    """Build a reproducible profile from lifecycle files."""

    horizons = sorted({int(value) for value in config.get("future_windows", DEFAULT_HORIZONS) if int(value) > 0})
    if not horizons:
        raise ValueError("future_windows must contain at least one positive horizon")
    builder = DatasetBuilder(
        lifecycle_dir=str(config.get("lifecycle_dir", "data/training")),
        sample_mode=str(config.get("sample_mode", "trade_event")),
        max_sample_age_seconds=int(config.get("max_sample_age_seconds", 300)),
        max_samples_per_token=int(config.get("max_samples_per_token", 120) or 0) or None,
        future_windows=horizons,
        label_fee_bps=_finite(config.get("label_fee_bps", 100.0)),
        label_slippage_bps=_finite(config.get("label_slippage_bps", 200.0)),
        label_stop_loss_pct=_finite(config.get("label_stop_loss_pct", -30.0)),
        label_target_return_pct=_finite(config.get("label_target_return_pct", 20.0)),
        label_entry_delay_seconds=_int(config.get("label_entry_delay_seconds", 3)),
        label_exit_delay_seconds=_int(config.get("label_exit_delay_seconds", 3)),
        label_live_downside_penalty_weight=_finite(config.get("label_live_downside_penalty_weight", 0.25)),
        label_delay_robust_entry_delay_seconds=config.get("label_delay_robust_entry_delay_seconds", [0, 3, 5]),
        label_delay_robust_min_weight=_finite(config.get("label_delay_robust_min_weight", 1.0)),
        label_fixed_stake_bnb=_finite(config.get("label_fixed_stake_bnb", 0.1)),
        label_entry_fixed_cost_bnb=_finite(config.get("label_entry_fixed_cost_bnb", 0.0)),
        label_exit_fixed_cost_bnb=_finite(config.get("label_exit_fixed_cost_bnb", 0.0)),
        label_entry_price_protection_pct=(
            None
            if config.get("label_entry_price_protection_pct") is None
            else _finite(config.get("label_entry_price_protection_pct"))
        ),
        min_entry_unique_buyers=max(1, _int(config.get("min_entry_unique_buyers", 3))),
        min_entry_buy_count=max(1, _int(config.get("min_entry_buy_count", 5))),
        include_flow_features=bool(config.get("include_flow_features", True)),
    )
    lifecycle_paths = list(config.get("lifecycle_paths") or [])
    if lifecycle_paths:
        builder.load_lifecycle_paths(lifecycle_paths)
    else:
        builder.load_lifecycle_files()

    shortest_rows = _rows_for_horizon(builder.samples, horizons[0])
    baseline_tokens = {_token(row) for row in shortest_rows if _token(row)}
    profile_rows = [
        summarize_horizon(
            builder.samples,
            horizon,
            baseline_tokens=baseline_tokens,
            minimum_token_support=max(1, _int(config.get("minimum_token_support", 50))),
        )
        for horizon in horizons
    ]
    return {
        "schema_version": 1,
        "profile_type": "bsc_delayed_horizon_profile",
        "inputs": {
            "lifecycle_dir": str(config.get("lifecycle_dir", "data/training")),
            "lifecycle_paths": [str(path) for path in lifecycle_paths],
            "sample_mode": builder.sample_mode,
            "max_sample_age_seconds": int(builder.max_sample_age_seconds),
            "max_samples_per_token": builder.max_samples_per_token,
            "future_windows": horizons,
            "label_fee_bps": builder.label_fee_bps,
            "label_slippage_bps": builder.label_slippage_bps,
            "label_stop_loss_pct": builder.label_stop_loss_pct,
            "label_target_return_pct": builder.label_target_return_pct,
            "label_entry_delay_seconds": builder.label_entry_delay_seconds,
            "label_exit_delay_seconds": builder.label_exit_delay_seconds,
            "label_delay_robust_entry_delay_seconds": builder.label_delay_robust_entry_delay_seconds,
            "label_delay_robust_min_weight": builder.label_delay_robust_min_weight,
            "label_live_downside_penalty_weight": builder.label_live_downside_penalty_weight,
            "label_fixed_stake_bnb": builder.label_fixed_stake_bnb,
            "label_entry_price_protection_pct": builder.label_entry_price_protection_pct,
            "min_entry_unique_buyers": builder.min_entry_unique_buyers,
            "min_entry_buy_count": builder.min_entry_buy_count,
            "include_flow_features": builder.include_flow_features,
        },
        "builder_stats": {
            "total_tokens": int(builder.total_tokens),
            "filtered_tokens": int(builder.filtered_tokens),
            "sample_count": int(len(builder.samples)),
        },
        "horizons": profile_rows,
        "decision": {
            "research_candidate_horizons": [
                int(row["horizon_seconds"])
                for row in profile_rows
                if row["decision"]["research_candidate"]
            ],
            "live_switch_recommendation": "none",
            "reason": (
                "A horizon profile is descriptive; live switching requires "
                "independent replay support and execution stress gates."
            ),
        },
    }

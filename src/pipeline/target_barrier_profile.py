"""Entry-time target/stop barrier replay for BSC lifecycle data.

This module evaluates the question the live strategy actually faces: after an
entry signal, does the position reach a useful return before a loss barrier
within a chosen holding window?  Graduation is retained only as a descriptive
field and never participates in filtering, labels, or exits.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import math
import statistics
from typing import Any, Iterable, Mapping, Sequence

from src.data.feature_extractor import extract_features
from src.pipeline.runner_reserve_profile import (
    RunnerReplayConfig,
    _effective_buy_price,
    _effective_sell_multiple,
    _entry_candidate,
    _point_at_or_after,
    _timestamp,
)


DEFAULT_HORIZONS = (3_600, 10_800, 21_600, 43_200, 86_400, 172_800, 259_200)
DEFAULT_TARGETS = (20.0, 50.0, 100.0, 200.0, 500.0, 1_000.0)
DEFAULT_STOPS = (-18.0, -25.0, -30.0, -50.0)
DEFAULT_FEATURES = (
    "volume_10s",
    "volume_30s",
    "volume_1min",
    "buy_pressure",
    "price_change_pct",
    "price_momentum",
    "price_volatility",
    "buy_volume_slope_10_30",
    "buy_volume_slope_30_60",
    "buy_count_slope_10_30",
    "unique_buyers_slope_10_30",
    "recent_new_buyer_ratio_10s",
    "recent_repeat_buyer_ratio_30s",
    "buyer_set_churn_10s_vs_prev50s",
    "holder_concentration_top5",
    "max_holder_ratio",
    "creator_buy_share",
    "creator_sell_share",
    "round_trip_buy_volume_ratio",
    "sell_pressure_30s",
    "sell_pressure_change_10_60",
    "volume_price_divergence",
)


def default_entry_gate_grid() -> list[dict[str, Any]]:
    """Return a small, explicit gate grid instead of treating legacy values as truth."""

    rows = []
    for max_age in (30, 60, 120, 300, 600):
        for min_buyers, min_buys in (
            (1, 2),
            (2, 3),
            (3, 5),
            (4, 8),
        ):
            rows.append(
                {
                    "name": f"buyers{min_buyers}_buys{min_buys}_age{max_age}",
                    "min_entry_unique_buyers": min_buyers,
                    "min_entry_buy_count": min_buys,
                    "max_entry_age_seconds": max_age,
                }
            )
    return rows


@dataclass(frozen=True)
class BarrierReplayConfig:
    """Execution and entry settings shared by every replay row."""

    entry_delay_seconds: int = 3
    exit_delay_seconds: int = 3
    fee_bps: float = 100.0
    slippage_bps: float = 200.0
    min_entry_unique_buyers: int = 3
    min_entry_buy_count: int = 5
    max_entry_age_seconds: int = 300
    incomplete_policy: str = "last_observation"
    fixed_stake_bnb: float = 0.1
    initial_equity_bnb: float = 1.0
    max_open_positions: int | None = 8
    entry_max_fill_wait_seconds: int | None = None
    entry_price_protection_pct: float | None = None

    def __post_init__(self) -> None:
        if self.entry_delay_seconds < 0 or self.exit_delay_seconds < 0:
            raise ValueError("execution delays must be non-negative")
        if self.fee_bps < 0 or self.slippage_bps < 0:
            raise ValueError("execution costs must be non-negative")
        if self.min_entry_unique_buyers < 1 or self.min_entry_buy_count < 1:
            raise ValueError("entry activity gates must be positive")
        if self.max_entry_age_seconds <= 0:
            raise ValueError("max_entry_age_seconds must be positive")
        if self.incomplete_policy not in {"last_observation", "loss"}:
            raise ValueError("incomplete_policy must be last_observation or loss")
        if self.fixed_stake_bnb < 0 or not math.isfinite(self.fixed_stake_bnb):
            raise ValueError("fixed_stake_bnb must be finite and non-negative")
        if self.initial_equity_bnb <= 0 or not math.isfinite(self.initial_equity_bnb):
            raise ValueError("initial_equity_bnb must be finite and positive")
        if self.max_open_positions is not None and self.max_open_positions <= 0:
            raise ValueError("max_open_positions must be positive when set")
        if self.entry_max_fill_wait_seconds is not None and self.entry_max_fill_wait_seconds < 0:
            raise ValueError("entry_max_fill_wait_seconds must be non-negative")
        if self.entry_price_protection_pct is not None and (
            self.entry_price_protection_pct < 0
            or not math.isfinite(self.entry_price_protection_pct)
        ):
            raise ValueError("entry_price_protection_pct must be finite and non-negative")


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if math.isfinite(parsed) else float(default)


def _distribution(values: Iterable[float]) -> dict[str, float | int]:
    cleaned = [float(value) for value in values if math.isfinite(float(value))]
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
        }
    ordered = sorted(cleaned)

    def percentile(probability: float) -> float:
        index = round((len(ordered) - 1) * probability)
        return float(ordered[max(0, min(len(ordered) - 1, index))])

    return {
        "count": len(cleaned),
        "mean": float(statistics.mean(cleaned)),
        "median": float(statistics.median(cleaned)),
        "p10": percentile(0.10),
        "p25": percentile(0.25),
        "p75": percentile(0.75),
        "p90": percentile(0.90),
        "positive_rate": float(sum(value > 0.0 for value in cleaned) / len(cleaned)),
    }


def _path_rows(path: Sequence[Any], start: float, end: float) -> list[tuple[float, float]]:
    rows = []
    for point in path:
        timestamp = _timestamp(point.time)
        price = _finite(point.price)
        if timestamp is None or price <= 0.0:
            continue
        if start <= timestamp <= end:
            rows.append((float(timestamp), price))
    return rows


def _event_execution(
    path: Sequence[Any],
    *,
    event_time: float,
    horizon_end: float,
    exit_delay_seconds: int,
) -> Any | None:
    due = float(event_time + exit_delay_seconds)
    eligible = [
        point
        for point in path
        if (_timestamp(point.time) or float("inf")) >= due
        and (_timestamp(point.time) or float("inf")) <= horizon_end
        and _finite(point.price) > 0.0
    ]
    if not eligible:
        return None
    earliest = min(_timestamp(point.time) for point in eligible)
    # Multiple swaps can share a chain second and persisted rows have no
    # transaction/log index.  Use the lowest price at the execution second.
    same_second = [point for point in eligible if _timestamp(point.time) == earliest]
    return min(same_second, key=lambda point: _finite(point.price))


def build_entry_candidates(
    lifecycles: Iterable[Mapping[str, Any]],
    config: BarrierReplayConfig | None = None,
    *,
    include_features: bool = True,
) -> list[dict[str, Any]]:
    """Build one decision-time candidate per token without graduation filtering."""

    config = config or BarrierReplayConfig()
    entry_config = RunnerReplayConfig(
        entry_delay_seconds=config.entry_delay_seconds,
        exit_delay_seconds=config.exit_delay_seconds,
        fee_bps=config.fee_bps,
        slippage_bps=config.slippage_bps,
        min_entry_unique_buyers=config.min_entry_unique_buyers,
        min_entry_buy_count=config.min_entry_buy_count,
        max_entry_age_seconds=config.max_entry_age_seconds,
    )
    candidates: list[dict[str, Any]] = []
    for lifecycle in lifecycles:
        candidate = _entry_candidate(lifecycle, entry_config)
        if not candidate:
            continue
        sample_time = int(float(candidate["sample_time"]))
        features: dict[str, Any] = {}
        if include_features:
            past_buys = [
                row for row in lifecycle.get("buys") or []
                if isinstance(row, Mapping) and _finite(row.get("timestamp")) <= sample_time
            ]
            past_sells = [
                row for row in lifecycle.get("sells") or []
                if isinstance(row, Mapping) and _finite(row.get("timestamp")) <= sample_time
            ]
            features = extract_features(
                dict(lifecycle),
                past_buys,
                past_sells,
                sample_time,
                include_flow_features=True,
            )
        create_timestamp = int(_finite(lifecycle.get("create_timestamp")))
        candidates.append(
            {
                "token": str(candidate.get("token") or "").strip().lower(),
                "symbol": lifecycle.get("symbol"),
                "sample_time": sample_time,
                "create_timestamp": create_timestamp,
                "path": candidate["path"],
                "features": features,
                "signal_price": float(candidate.get("anchor_price") or 0.0),
                # Kept for audit only.  It is never used by replay decisions.
                "graduated": bool(lifecycle.get("graduated")),
                "graduate_time": lifecycle.get("graduate_time"),
            }
        )
    return sorted(candidates, key=lambda row: (int(row["sample_time"]), str(row["token"])))


def simulate_barrier(
    candidate: Mapping[str, Any],
    *,
    horizon_seconds: int,
    target_return_pct: float,
    stop_loss_pct: float,
    config: BarrierReplayConfig | None = None,
) -> dict[str, Any]:
    """Simulate one entry and the first executable target/stop barrier.

    A path ending before the horizon is explicitly censored.  Its conservative
    return uses the last observed post-entry price (or -100% when none exists)
    and is never mixed silently with complete-path metrics.
    """

    config = config or BarrierReplayConfig()
    if horizon_seconds <= 0:
        raise ValueError("horizon_seconds must be positive")
    if target_return_pct <= 0:
        raise ValueError("target_return_pct must be positive")
    if stop_loss_pct >= 0:
        raise ValueError("stop_loss_pct must be negative")

    path = candidate.get("path") or []
    sample_time = _finite(candidate.get("sample_time"))
    entry_point = _point_at_or_after(path, sample_time + config.entry_delay_seconds)
    if entry_point is None:
        return {
            "status": "missing_entry",
            "complete": False,
            "horizon_seconds": int(horizon_seconds),
            "return_pct": -100.0,
            "conservative_return_pct": -100.0,
            "reason": "missing_entry",
        }
    entry_time = _timestamp(entry_point.time)
    if entry_time is None or _finite(entry_point.price) <= 0.0:
        return {
            "status": "missing_entry",
            "complete": False,
            "horizon_seconds": int(horizon_seconds),
            "return_pct": -100.0,
            "conservative_return_pct": -100.0,
            "reason": "missing_entry",
        }

    entry_wait_seconds = float(entry_time - sample_time)
    if (
        config.entry_max_fill_wait_seconds is not None
        and entry_wait_seconds > float(config.entry_max_fill_wait_seconds)
    ):
        return {
            "status": "entry_timeout",
            "complete": False,
            "horizon_seconds": int(horizon_seconds),
            "entry_time": float(entry_time),
            "entry_wait_seconds": entry_wait_seconds,
            "return_pct": 0.0,
            "conservative_return_pct": 0.0,
            "reason": "entry_timeout",
        }
    signal_price = _finite(candidate.get("signal_price"), 0.0)
    if (
        config.entry_price_protection_pct is not None
        and signal_price > 0.0
        and _finite(entry_point.price) > signal_price * (1.0 + float(config.entry_price_protection_pct))
    ):
        return {
            "status": "entry_protection_skip",
            "complete": False,
            "horizon_seconds": int(horizon_seconds),
            "entry_time": float(entry_time),
            "entry_wait_seconds": entry_wait_seconds,
            "return_pct": 0.0,
            "conservative_return_pct": 0.0,
            "reason": "entry_protection_skip",
        }

    horizon_end = float(entry_time + horizon_seconds)
    last_time = max((_timestamp(point.time) or 0.0 for point in path), default=0.0)
    complete = last_time >= horizon_end
    entry_price = _effective_buy_price(_finite(entry_point.price), config)
    target_multiple = 1.0 + target_return_pct / 100.0
    stop_multiple = 1.0 + stop_loss_pct / 100.0
    rows = _path_rows(path, float(entry_time), horizon_end)
    reason = "horizon" if complete else "censored"
    exit_point = None
    barrier_time = None
    ambiguous = False
    target_hit = False
    stop_hit = False

    # Group same-second observations.  If a target and a stop occur in the
    # same second, choose the stop because log ordering is not guaranteed.
    grouped: dict[float, list[float]] = {}
    for timestamp, price in rows:
        grouped.setdefault(timestamp, []).append(price)
    for timestamp in sorted(grouped):
        multiples = [
            _effective_sell_multiple(price, entry_price, config)
            for price in grouped[timestamp]
        ]
        has_target = any(value >= target_multiple for value in multiples)
        has_stop = any(value <= stop_multiple for value in multiples)
        if not has_target and not has_stop:
            continue
        target_hit = has_target
        stop_hit = has_stop
        ambiguous = has_target and has_stop
        reason = "ambiguous_stop" if ambiguous else ("target" if has_target else "stop")
        barrier_time = float(timestamp)
        exit_point = _event_execution(
            path,
            event_time=float(timestamp),
            horizon_end=horizon_end,
            exit_delay_seconds=config.exit_delay_seconds,
        )
        # A barrier observed without an executable delayed quote is not a
        # guaranteed fill; continue looking for a later executable barrier.
        if exit_point is None:
            reason = "censored" if not complete else "horizon"
            target_hit = stop_hit = ambiguous = False
            barrier_time = None
            continue
        break

    if exit_point is None:
        exit_point = _point_at_or_after(path, horizon_end)
    if exit_point is None:
        post_entry = [point for point in path if (_timestamp(point.time) or 0.0) >= entry_time]
        exit_point = post_entry[-1] if post_entry else None

    if exit_point is not None:
        observed_exit_price = _finite(exit_point.price)
        realized_return = (
            _effective_sell_multiple(observed_exit_price, entry_price, config) - 1.0
        ) * 100.0
    else:
        observed_exit_price = 0.0
        realized_return = -100.0

    if complete:
        conservative_return = realized_return
        status = "ok"
    elif config.incomplete_policy == "loss":
        conservative_return = -100.0
        status = "censored_loss"
    elif exit_point is not None and (_timestamp(exit_point.time) or 0.0) >= entry_time:
        conservative_return = realized_return
        status = "censored"
    else:
        conservative_return = -100.0
        status = "censored_no_exit"

    return {
        "status": status,
        "complete": bool(complete),
        "horizon_seconds": int(horizon_seconds),
        "entry_time": float(entry_time),
        "entry_wait_seconds": entry_wait_seconds,
        "entry_price": float(entry_price),
        "exit_time": _timestamp(exit_point.time) if exit_point is not None else None,
        "exit_price": float(observed_exit_price),
        "return_pct": float(realized_return),
        "conservative_return_pct": float(conservative_return),
        "reason": reason,
        "target_hit": bool(target_hit),
        "stop_hit": bool(stop_hit),
        "ambiguous_barrier": bool(ambiguous),
        "barrier_time_seconds_from_entry": (
            float(barrier_time - entry_time) if barrier_time is not None else None
        ),
        "target_return_pct": float(target_return_pct),
        "stop_loss_pct": float(stop_loss_pct),
    }


def summarize_outcomes(
    outcomes: Sequence[Mapping[str, Any]],
    *,
    fixed_stake_bnb: float = 0.1,
) -> dict[str, Any]:
    """Return complete and conservative metrics without hiding censoring."""

    complete = [row for row in outcomes if bool(row.get("complete")) and row.get("status") == "ok"]
    # Every candidate remains in the conservative denominator, including a
    # missing delayed fill, which is assigned the -100% fallback above.
    conservative = list(outcomes)

    def metrics(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, Any]:
        values = [_finite(row.get(field), -100.0) for row in rows]
        result = _distribution(values)
        # Equal-stake totals answer the portfolio question directly.  The
        # compounded value is a secondary diagnostic because positions may
        # overlap in a real bot.
        result["total_return_pct"] = float(sum(values))
        result["net_profit_bnb"] = float(sum(values) / 100.0 * float(fixed_stake_bnb))
        equity = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for value in values:
            equity *= max(0.0, 1.0 + value / 100.0)
            peak = max(peak, equity)
            if peak > 0.0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)
        result["compounded_return_pct"] = float((equity - 1.0) * 100.0)
        result["max_drawdown_pct"] = float(max_drawdown * 100.0)
        gains = sum(value for value in values if value > 0.0)
        losses = -sum(value for value in values if value < 0.0)
        result["profit_factor"] = float(gains / losses) if losses > 0.0 else (None if gains > 0.0 else 0.0)
        return result

    def rates(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
        denominator = len(rows)
        if denominator == 0:
            return {
                "target_first_rate": 0.0,
                "stop_first_rate": 0.0,
                "ambiguous_barrier_rate": 0.0,
                "horizon_exit_rate": 0.0,
            }
        return {
            "target_first_rate": float(sum(row.get("reason") == "target" for row in rows) / denominator),
            "stop_first_rate": float(sum(row.get("reason") in {"stop", "ambiguous_stop"} for row in rows) / denominator),
            "ambiguous_barrier_rate": float(sum(bool(row.get("ambiguous_barrier")) for row in rows) / denominator),
            "horizon_exit_rate": float(sum(row.get("reason") in {"horizon", "censored"} for row in rows) / denominator),
        }

    status_counts = Counter(str(row.get("status")) for row in outcomes)
    return {
        "candidate_count": len(outcomes),
        "complete_count": len(complete),
        "censored_count": sum(not bool(row.get("complete")) for row in outcomes),
        "status_counts": dict(sorted(status_counts.items())),
        "complete_only": {
            "returns": metrics(complete, "return_pct"),
            "rates": rates(complete),
        },
        "conservative_all_candidates": {
            "returns": metrics(conservative, "conservative_return_pct"),
            "rates": rates(conservative),
        },
    }


def _feature_gate_rows(
    candidates: Sequence[Mapping[str, Any]],
    *,
    feature_names: Sequence[str],
    quantiles: Sequence[float],
    horizon_seconds: int,
    target_return_pct: float,
    stop_loss_pct: float,
    config: BarrierReplayConfig,
    min_samples: int,
    precomputed_outcomes: Mapping[str, Mapping[str, Any]] | None = None,
    threshold_candidates: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    threshold_candidates = list(threshold_candidates or candidates)
    for feature_name in feature_names:
        values = [
            _finite((candidate.get("features") or {}).get(feature_name), math.nan)
            for candidate in candidates
        ]
        threshold_values = [
            _finite((candidate.get("features") or {}).get(feature_name), math.nan)
            for candidate in threshold_candidates
        ]
        finite_values = sorted(value for value in threshold_values if math.isfinite(value))
        if len(finite_values) < min_samples:
            continue
        for direction in ("ge", "le"):
            for quantile in quantiles:
                index = round((len(finite_values) - 1) * float(quantile))
                threshold = float(finite_values[max(0, min(len(finite_values) - 1, index))])
                selected = [
                    candidate
                    for candidate, value in zip(candidates, values)
                    if math.isfinite(value)
                    and (value >= threshold if direction == "ge" else value <= threshold)
                ]
                if len(selected) < min_samples:
                    continue
                outcomes = [
                    precomputed_outcomes.get(str(candidate.get("token")), {})
                    if precomputed_outcomes is not None
                    else simulate_barrier(
                        candidate,
                        horizon_seconds=horizon_seconds,
                        target_return_pct=target_return_pct,
                        stop_loss_pct=stop_loss_pct,
                        config=config,
                    )
                    for candidate in selected
                ]
                summary = summarize_outcomes(
                    outcomes,
                    fixed_stake_bnb=config.fixed_stake_bnb,
                )["conservative_all_candidates"]
                returns = summary["returns"]
                rows.append(
                    {
                        "feature": feature_name,
                        "direction": direction,
                        "quantile": float(quantile),
                        "threshold": threshold,
                        "candidate_count": len(selected),
                        "total_return_pct": returns["total_return_pct"],
                        "net_profit_bnb": returns["net_profit_bnb"],
                        "median_return_pct": returns["median"],
                        "mean_return_pct": returns["mean"],
                        "p10_return_pct": returns["p10"],
                        "positive_rate": returns["positive_rate"],
                        "target_first_rate": summary["rates"]["target_first_rate"],
                    }
                )
    return rows


def chronological_splits(
    candidates: Sequence[Mapping[str, Any]],
    *,
    purge_seconds: int = 0,
) -> dict[str, list[Mapping[str, Any]]]:
    """Split by decision time and purge labels crossing the next boundary."""

    ordered = sorted(candidates, key=lambda row: (int(row.get("sample_time", 0)), str(row.get("token"))))
    if not ordered:
        return {"train": [], "validation": [], "final": []}
    train_end = max(1, int(len(ordered) * 0.60))
    validation_end = max(train_end + 1, int(len(ordered) * 0.80))
    validation_end = min(len(ordered), validation_end)
    train = ordered[:train_end]
    validation = ordered[train_end:validation_end]
    final = ordered[validation_end:]
    if purge_seconds > 0:
        validation_start = int(ordered[train_end].get("sample_time", 0)) if train_end < len(ordered) else None
        final_start = int(ordered[validation_end].get("sample_time", 0)) if validation_end < len(ordered) else None
        if validation_start is not None:
            train = [
                row for row in train
                if int(row.get("sample_time", 0)) + int(purge_seconds) < validation_start
            ]
        if final_start is not None:
            validation = [
                row for row in validation
                if int(row.get("sample_time", 0)) + int(purge_seconds) < final_start
            ]
    return {"train": train, "validation": validation, "final": final}


def evaluate_entry_gate_grid(
    lifecycles: Sequence[Mapping[str, Any]],
    *,
    gate_grid: Sequence[Mapping[str, Any]] | None = None,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    target_return_pct: float = 20.0,
    stop_loss_pct: float = -30.0,
    config: BarrierReplayConfig | None = None,
    min_validation_samples: int = 50,
) -> dict[str, Any]:
    """Compare entry activity gates using train/validation/final time slices.

    Gate selection is based on validation equal-stake total return first.  Final metrics are
    reported after selection and are not used to choose the gate.
    """

    config = config or BarrierReplayConfig()
    gates = list(gate_grid or default_entry_gate_grid())
    reports: list[dict[str, Any]] = []
    for index, gate in enumerate(gates):
        if not isinstance(gate, Mapping):
            continue
        try:
            gate_config = replace(
                config,
                min_entry_unique_buyers=max(1, int(gate.get("min_entry_unique_buyers", config.min_entry_unique_buyers))),
                min_entry_buy_count=max(1, int(gate.get("min_entry_buy_count", config.min_entry_buy_count))),
                max_entry_age_seconds=max(1, int(gate.get("max_entry_age_seconds", config.max_entry_age_seconds))),
            )
        except (TypeError, ValueError):
            continue
        candidates = build_entry_candidates(lifecycles, gate_config, include_features=False)
        splits = chronological_splits(candidates, purge_seconds=max(0, int(max(horizons, default=0))))
        horizon_rows: dict[str, Any] = {}
        for horizon in sorted({int(value) for value in horizons if int(value) > 0}):
            split_metrics: dict[str, Any] = {}
            for split_name, split_candidates in splits.items():
                outcomes = [
                    simulate_barrier(
                        candidate,
                        horizon_seconds=horizon,
                        target_return_pct=target_return_pct,
                        stop_loss_pct=stop_loss_pct,
                        config=gate_config,
                    )
                    for candidate in split_candidates
                ]
                split_metrics[split_name] = summarize_outcomes(
                    outcomes,
                    fixed_stake_bnb=gate_config.fixed_stake_bnb,
                )["conservative_all_candidates"]
            horizon_rows[str(horizon)] = split_metrics
        reports.append(
            {
                "index": index,
                "name": str(gate.get("name") or f"gate_{index}"),
                "rule": {
                    "min_entry_unique_buyers": gate_config.min_entry_unique_buyers,
                    "min_entry_buy_count": gate_config.min_entry_buy_count,
                    "max_entry_age_seconds": gate_config.max_entry_age_seconds,
                },
                "candidate_count": len(candidates),
                "graduated_count_diagnostic_only": sum(bool(row.get("graduated")) for row in candidates),
                "horizons": horizon_rows,
            }
        )

    selected_by_horizon: dict[str, Any] = {}
    for horizon in sorted({int(value) for value in horizons if int(value) > 0}):
        eligible = [
            row for row in reports
            if int(row["horizons"].get(str(horizon), {}).get("validation", {}).get("returns", {}).get("count", 0))
            >= int(min_validation_samples)
        ]
        eligible.sort(
            key=lambda row: (
                _finite(row["horizons"][str(horizon)]["validation"]["returns"].get("total_return_pct")),
                _finite(row["horizons"][str(horizon)]["validation"]["returns"].get("net_profit_bnb")),
                _finite(row["horizons"][str(horizon)]["validation"]["returns"].get("p10")),
                _finite(row["horizons"][str(horizon)]["validation"]["returns"].get("positive_rate")),
                -int(row.get("candidate_count", 0)),
            ),
            reverse=True,
        )
        selected = eligible[0] if eligible else None
        selected_by_horizon[str(horizon)] = {
            "selected_by_validation": (
                {
                    "name": selected["name"],
                    "rule": selected["rule"],
                    "validation": selected["horizons"][str(horizon)]["validation"],
                    "final": selected["horizons"][str(horizon)]["final"],
                }
                if selected
                else None
            ),
            "minimum_validation_samples": int(min_validation_samples),
        }
    return {
        "target_return_pct": float(target_return_pct),
        "stop_loss_pct": float(stop_loss_pct),
        "gate_count": len(reports),
        "gates": reports,
        "selected_by_horizon": selected_by_horizon,
        "selection_uses_final": False,
    }


def profile_target_barriers(
    lifecycles: Iterable[Mapping[str, Any]],
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    targets: Sequence[float] = DEFAULT_TARGETS,
    stops: Sequence[float] = DEFAULT_STOPS,
    config: BarrierReplayConfig | None = None,
    feature_names: Sequence[str] = DEFAULT_FEATURES,
    feature_quantiles: Sequence[float] = (0.50, 0.70, 0.80, 0.90),
    feature_target_return_pct: float = 20.0,
    feature_stop_loss_pct: float = -30.0,
    min_feature_gate_samples: int = 50,
    entry_gate_grid: Sequence[Mapping[str, Any]] | None = None,
    min_entry_gate_validation_samples: int = 50,
    stress_scenarios: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build barrier grid and time-split feature-gate diagnostics."""

    config = config or BarrierReplayConfig()
    horizons = sorted({int(value) for value in horizons if int(value) > 0})
    targets = sorted({float(value) for value in targets if float(value) > 0.0})
    stops = sorted({float(value) for value in stops if float(value) < 0.0}, reverse=True)
    if not horizons or not targets or not stops:
        raise ValueError("horizons, targets and stops must contain valid values")
    lifecycle_rows = list(lifecycles)
    candidates = build_entry_candidates(lifecycle_rows, config)
    splits = chronological_splits(candidates, purge_seconds=max(horizons))
    split_indices = {
        name: {id(candidate) for candidate in rows}
        for name, rows in splits.items()
    }
    barrier_rows: list[dict[str, Any]] = []
    for horizon in horizons:
        for target in targets:
            for stop in stops:
                outcomes = [
                    simulate_barrier(
                        candidate,
                        horizon_seconds=horizon,
                        target_return_pct=target,
                        stop_loss_pct=stop,
                        config=config,
                    )
                    for candidate in candidates
                ]
                summary = summarize_outcomes(
                    outcomes,
                    fixed_stake_bnb=config.fixed_stake_bnb,
                )
                split_metrics = {}
                for split_name, candidate_rows in splits.items():
                    split_outcomes = [
                        outcome
                        for candidate, outcome in zip(candidates, outcomes)
                        if id(candidate) in split_indices[split_name]
                    ]
                    split_metrics[split_name] = summarize_outcomes(
                        split_outcomes,
                        fixed_stake_bnb=config.fixed_stake_bnb,
                    )["conservative_all_candidates"]
                barrier_rows.append(
                    {
                        "horizon_seconds": int(horizon),
                        "target_return_pct": float(target),
                        "stop_loss_pct": float(stop),
                        "time_split_metrics": split_metrics,
                        **summary,
                    }
                )

    feature_reports: dict[str, Any] = {}
    for horizon in horizons:
        baseline_outcomes = [
            simulate_barrier(
                candidate,
                horizon_seconds=horizon,
                target_return_pct=feature_target_return_pct,
                stop_loss_pct=feature_stop_loss_pct,
                config=config,
            )
            for candidate in candidates
        ]
        outcome_by_token = {
            str(candidate.get("token")): outcome
            for candidate, outcome in zip(candidates, baseline_outcomes)
        }
        gate_reports: dict[str, Any] = {}
        for split_name, split_candidates in splits.items():
            gate_rows = _feature_gate_rows(
                split_candidates,
                feature_names=feature_names,
                quantiles=feature_quantiles,
                horizon_seconds=horizon,
                target_return_pct=feature_target_return_pct,
                stop_loss_pct=feature_stop_loss_pct,
                config=config,
                min_samples=min_feature_gate_samples,
                precomputed_outcomes=outcome_by_token,
                threshold_candidates=splits["train"],
            )
            gate_rows.sort(
                key=lambda row: (
                    _finite(row.get("total_return_pct")),
                    _finite(row.get("net_profit_bnb")),
                    _finite(row.get("p10_return_pct")),
                    _finite(row.get("positive_rate")),
                    -int(row.get("candidate_count", 0)),
                ),
                reverse=True,
            )
            gate_reports[split_name] = {
                "candidate_count": len(split_candidates),
                "baseline": summarize_outcomes(
                    [outcome_by_token[str(candidate.get("token"))] for candidate in split_candidates],
                    fixed_stake_bnb=config.fixed_stake_bnb,
                )["conservative_all_candidates"],
                "top_gates": gate_rows[:10],
            }

        selected = None
        validation_top = gate_reports["validation"]["top_gates"]
        if validation_top:
            selected = validation_top[0]
            selected_candidates = [
                candidate
                for candidate in splits["final"]
                if math.isfinite(_finite((candidate.get("features") or {}).get(selected["feature"]), math.nan))
                and (
                    _finite((candidate.get("features") or {}).get(selected["feature"]), math.nan) >= selected["threshold"]
                    if selected["direction"] == "ge"
                    else _finite((candidate.get("features") or {}).get(selected["feature"]), math.nan) <= selected["threshold"]
                )
            ]
            selected_outcomes = [outcome_by_token[str(candidate.get("token"))] for candidate in selected_candidates]
            gate_reports["final_selected_gate"] = {
                "rule": selected,
                "candidate_count": len(selected_candidates),
                "summary": summarize_outcomes(
                    selected_outcomes,
                    fixed_stake_bnb=config.fixed_stake_bnb,
                )["conservative_all_candidates"],
            }
        feature_reports[str(horizon)] = {
            "target_return_pct": float(feature_target_return_pct),
            "stop_loss_pct": float(feature_stop_loss_pct),
            "splits": gate_reports,
        }

    entry_gate_reports = evaluate_entry_gate_grid(
        lifecycle_rows,
        gate_grid=entry_gate_grid,
        horizons=horizons,
        target_return_pct=feature_target_return_pct,
        stop_loss_pct=feature_stop_loss_pct,
        config=config,
        min_validation_samples=min_entry_gate_validation_samples,
    )

    barrier_selection: dict[str, Any] = {}
    for horizon in horizons:
        rows = [row for row in barrier_rows if row["horizon_seconds"] == int(horizon)]
        eligible = [
            row for row in rows
            if int(row["time_split_metrics"].get("validation", {}).get("returns", {}).get("count", 0))
            >= int(min_entry_gate_validation_samples)
        ]
        eligible.sort(
            key=lambda row: (
                _finite(row["time_split_metrics"]["validation"]["returns"].get("total_return_pct")),
                _finite(row["time_split_metrics"]["validation"]["returns"].get("net_profit_bnb")),
                _finite(row["time_split_metrics"]["validation"]["returns"].get("p10")),
            ),
            reverse=True,
        )
        selected = eligible[0] if eligible else None
        barrier_selection[str(horizon)] = {
            "selected_by_validation": (
                {
                    "target_return_pct": selected["target_return_pct"],
                    "stop_loss_pct": selected["stop_loss_pct"],
                    "validation": selected["time_split_metrics"]["validation"],
                    "final": selected["time_split_metrics"]["final"],
                }
                if selected
                else None
            ),
            "minimum_validation_samples": int(min_entry_gate_validation_samples),
        }

    stress_reports: list[dict[str, Any]] = []
    scenarios = list(stress_scenarios or (
        {"name": "base", "fee_bps": config.fee_bps, "slippage_bps": config.slippage_bps},
        {"name": "high_cost", "fee_bps": 150.0, "slippage_bps": 500.0},
        {"name": "extreme_cost", "fee_bps": 250.0, "slippage_bps": 800.0},
    ))
    for scenario in scenarios:
        if not isinstance(scenario, Mapping):
            continue
        try:
            stress_config = replace(
                config,
                fee_bps=max(0.0, float(scenario.get("fee_bps", config.fee_bps))),
                slippage_bps=max(0.0, float(scenario.get("slippage_bps", config.slippage_bps))),
            )
        except (TypeError, ValueError):
            continue
        horizon_rows: dict[str, Any] = {}
        for horizon in horizons:
            outcomes = [
                simulate_barrier(
                    candidate,
                    horizon_seconds=horizon,
                    target_return_pct=feature_target_return_pct,
                    stop_loss_pct=feature_stop_loss_pct,
                    config=stress_config,
                )
                for candidate in candidates
            ]
            horizon_rows[str(horizon)] = summarize_outcomes(
                outcomes,
                fixed_stake_bnb=stress_config.fixed_stake_bnb,
            )
        stress_reports.append(
            {
                "name": str(scenario.get("name") or "stress"),
                "fee_bps": stress_config.fee_bps,
                "slippage_bps": stress_config.slippage_bps,
                "target_return_pct": float(feature_target_return_pct),
                "stop_loss_pct": float(feature_stop_loss_pct),
                "horizons": horizon_rows,
            }
        )

    return {
        "schema_version": 1,
        "profile_type": "bsc_entry_target_barrier_profile",
        "inputs": {
            "horizons": horizons,
            "targets": targets,
            "stops": stops,
            "config": {
                key: getattr(config, key)
                for key in config.__dataclass_fields__
            },
            "feature_target_return_pct": float(feature_target_return_pct),
            "feature_stop_loss_pct": float(feature_stop_loss_pct),
            "feature_names": list(feature_names),
            "feature_quantiles": [float(value) for value in feature_quantiles],
            "min_feature_gate_samples": int(min_feature_gate_samples),
            "min_entry_gate_validation_samples": int(min_entry_gate_validation_samples),
            "entry_gate_grid": list(entry_gate_grid or default_entry_gate_grid()),
        },
        "data_quality": {
            "candidate_count": len(candidates),
            "graduated_count_diagnostic_only": sum(bool(candidate.get("graduated")) for candidate in candidates),
            "non_graduated_count_diagnostic_only": sum(not bool(candidate.get("graduated")) for candidate in candidates),
            "uses_graduation_as_filter": False,
            "uses_decision_time_features_only": True,
        },
        "time_splits": {
            name: {
                "candidate_count": len(rows),
                "first_sample_time": int(rows[0]["sample_time"]) if rows else None,
                "last_sample_time": int(rows[-1]["sample_time"]) if rows else None,
            }
            for name, rows in splits.items()
        },
        "barrier_grid": barrier_rows,
        "barrier_selection": barrier_selection,
        "feature_gate_diagnostics": feature_reports,
        "entry_gate_diagnostics": entry_gate_reports,
        "cost_stress": stress_reports,
        "decision": {
            "live_switch_recommendation": "none",
            "reason": "Research profile only; require positive final conservative median and cost stress before runtime changes.",
        },
    }


__all__ = [
    "BarrierReplayConfig",
    "DEFAULT_FEATURES",
    "DEFAULT_HORIZONS",
    "DEFAULT_STOPS",
    "DEFAULT_TARGETS",
    "build_entry_candidates",
    "chronological_splits",
    "default_entry_gate_grid",
    "evaluate_entry_gate_grid",
    "profile_target_barriers",
    "simulate_barrier",
    "summarize_outcomes",
]

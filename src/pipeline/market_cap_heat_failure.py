"""Price-observation diagnostics for the legacy market-cap heat probe.

The earlier aggregate probe used the next observed lifecycle event as the exit
for a fixed horizon.  This module keeps a hard horizon boundary and reports
both an as-of mark and a nearby-event diagnostic. Neither is an executed trade:
reserves, order size, quote denomination, fees and venue are not validated here.
The original 22-event subset must never be described as 22 executable trades.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import math
import statistics
from typing import Any, Iterable, Mapping, Sequence

from src.pipeline.market_cap_heat_gate import (
    MarketCapHeatConfig,
    _at_or_after,
    _at_or_before,
    _candidate_for_threshold,
    _finite,
    _heat_passes,
    _price_path,
    _sorted_rows,
)


@dataclass(frozen=True)
class FailureAttributionConfig:
    threshold_usd: float = 50_000.0
    post_confirmation_window_seconds: int = 60
    exit_grace_seconds: int = 3
    adverse_return_pct: float = -20.0
    pump_return_pct: float = 10.0
    stale_snapshot_seconds: int = 60
    heat: MarketCapHeatConfig = field(
        default_factory=lambda: MarketCapHeatConfig(horizons_seconds=(900, 3_600, 21_600, 86_400))
    )

    def __post_init__(self) -> None:
        if self.threshold_usd <= 0.0:
            raise ValueError("threshold_usd must be positive")
        if self.post_confirmation_window_seconds <= 0 or self.exit_grace_seconds < 0:
            raise ValueError("confirmation/exit windows are invalid")
        if self.adverse_return_pct >= 0.0 or self.pump_return_pct <= 0.0:
            raise ValueError("failure return thresholds are invalid")


def _effective_return(price: float, entry_price: float, config: MarketCapHeatConfig) -> float:
    if price <= 0.0 or entry_price <= 0.0:
        return -1.0
    return (
        price * (1.0 - config.slippage_rate) * (1.0 - config.fee_rate)
        / (entry_price * (1.0 + config.slippage_rate) * (1.0 + config.fee_rate))
    ) - 1.0


def _bounded_event(
    times: Sequence[float],
    prices: Sequence[float],
    target: float,
    grace_seconds: int,
) -> tuple[float, float] | None:
    point = _at_or_after(times, prices, target)
    if point is None or point[0] > float(target) + int(grace_seconds):
        return None
    return point


def _snapshot_event(
    times: Sequence[float],
    prices: Sequence[float],
    target: float,
) -> tuple[tuple[float, float] | None, str, float | None]:
    before = _at_or_before(times, prices, target)
    if before is None:
        return None, "missing", None
    return before, "event" if before[0] == target else "carry_forward", max(0.0, target - before[0])


def _path_stats(
    times: Sequence[float],
    prices: Sequence[float],
    *,
    entry_time: float,
    entry_price: float,
    target: float,
    config: MarketCapHeatConfig,
) -> dict[str, float | None]:
    points = [
        (timestamp, price)
        for timestamp, price in zip(times, prices)
        if entry_time < timestamp <= target
    ]
    values = [_effective_return(price, entry_price, config) for _, price in points]
    if not values:
        return {
            "mfe_return": None,
            "mae_return": None,
            "mfe_time": None,
            "mae_time": None,
            "observation_count": 0,
        }
    max_index = max(range(len(values)), key=values.__getitem__)
    min_index = min(range(len(values)), key=values.__getitem__)
    return {
        "mfe_return": float(values[max_index]),
        "mae_return": float(values[min_index]),
        "mfe_time": float(points[max_index][0]),
        "mae_time": float(points[min_index][0]),
        "observation_count": len(values),
    }


def _post_confirmation_flow(
    buys: Sequence[Mapping[str, Any]],
    sells: Sequence[Mapping[str, Any]],
    start: float,
    window_seconds: int,
) -> dict[str, float | int]:
    end = start + int(window_seconds)
    future_buys = [row for row in buys if start < _finite(row.get("timestamp"), 0.0) <= end]
    future_sells = [row for row in sells if start < _finite(row.get("timestamp"), 0.0) <= end]
    buy_volume = sum(_finite(row.get("bnb_amount"), 0.0) for row in future_buys)
    sell_volume = sum(_finite(row.get("bnb_amount"), 0.0) for row in future_sells)
    total = buy_volume + sell_volume
    return {
        "post_buy_count": len(future_buys),
        "post_sell_count": len(future_sells),
        "post_unique_buyers": len({str(row.get("account") or "").lower() for row in future_buys if row.get("account")}),
        "post_buy_volume": buy_volume,
        "post_sell_volume": sell_volume,
        "post_sell_pressure": sell_volume / total if total else 0.0,
        "post_signed_imbalance": (buy_volume - sell_volume) / total if total else 0.0,
    }


def attribute_candidate(
    lifecycle: Mapping[str, Any],
    *,
    config: FailureAttributionConfig | None = None,
) -> dict[str, Any] | None:
    """Return one observation row; leave execution and actual P&L unknown."""
    config = config or FailureAttributionConfig()
    candidate = _candidate_for_threshold(lifecycle, config.threshold_usd, config.heat)
    if candidate is None or not _heat_passes(candidate, config.heat, flow=True):
        return None
    times, prices = _price_path(lifecycle)
    confirmation_time = _finite(candidate.get("confirmation_time"), 0.0)
    confirmation_price = _finite(candidate.get("confirmation_price"), 0.0)
    if confirmation_time <= 0.0 or confirmation_price <= 0.0:
        return None

    entry_time = confirmation_time + config.heat.execution_delay_seconds
    entry_mark = _at_or_before(times, prices, entry_time)
    if entry_mark is None:
        return None
    entry_price = entry_mark[1]
    buys = _sorted_rows(lifecycle, "buys")
    sells = _sorted_rows(lifecycle, "sells")
    next_observed = _at_or_after(times, prices, entry_time)
    next_buy = next(
        (
            (_finite(row.get("timestamp"), 0.0), _finite(row.get("price"), 0.0))
            for row in buys
            if _finite(row.get("timestamp"), 0.0) >= entry_time and _finite(row.get("price"), 0.0) > 0.0
        ),
        None,
    )

    row: dict[str, Any] = {
        **candidate,
        "planned_entry_time": entry_time,
        "entry_mark_price": entry_price,
        "entry_mark_time": entry_mark[0],
        "actual_execution_status": "unknown",
        "actual_pnl_bnb": None,
        "evidence_status": "price_observation_only",
        "evidence_limitations": [
            "quote_asset_and_fx_unverified",
            "legacy_selector_not_equal_to_runtime_universe",
            "no_size_specific_quote_or_fill",
            "venue_and_complete_collection_coverage_unverified",
        ],
        "next_observed_time": next_observed[0] if next_observed else None,
        "next_observed_price": next_observed[1] if next_observed else None,
        "next_observed_wait_seconds": next_observed[0] - entry_time if next_observed else None,
        "next_buy_time": next_buy[0] if next_buy else None,
        "next_buy_price": next_buy[1] if next_buy else None,
        "next_buy_wait_seconds": next_buy[0] - entry_time if next_buy else None,
        "graduated": bool(lifecycle.get("graduated")),
        "graduate_time": lifecycle.get("graduate_time"),
    }
    # Preserve the old lookup for diagnostics without presenting it as our fill.
    row["legacy_next_event_time"] = row.pop("entry_time", None)
    row["legacy_next_event_price"] = row.pop("entry_price", None)
    row.update(_post_confirmation_flow(buys, sells, confirmation_time, config.post_confirmation_window_seconds))

    tags: list[str] = []
    for horizon in config.heat.horizons_seconds:
        target = entry_time + int(horizon)
        snapshot, snapshot_kind, snapshot_age = _snapshot_event(
            times, prices, target
        )
        if snapshot is not None and snapshot[0] <= entry_time:
            # A stale quote from before entry is not a future outcome.
            snapshot, snapshot_kind, snapshot_age = None, "missing_before_entry", None
        nearby = _bounded_event(times, prices, target, config.exit_grace_seconds)
        stats = _path_stats(
            times,
            prices,
            entry_time=entry_time,
            entry_price=entry_price,
            target=target,
            config=config.heat,
        )
        prefix = f"h{int(horizon)}"
        row[f"{prefix}_target_time"] = target
        row[f"{prefix}_snapshot_kind"] = snapshot_kind
        row[f"{prefix}_snapshot_age_seconds"] = snapshot_age
        row[f"{prefix}_snapshot_return"] = (
            _effective_return(snapshot[1], entry_price, config.heat) if snapshot else None
        )
        row[f"{prefix}_near_target_price_return"] = (
            _effective_return(nearby[1], entry_price, config.heat) if nearby else None
        )
        for key, value in stats.items():
            row[f"{prefix}_{key}"] = value
        if horizon == 900:
            if stats["mae_return"] is not None and stats["mae_return"] <= config.adverse_return_pct / 100.0:
                tags.append("early_adverse_move")
            if stats["mfe_return"] is not None and stats["mfe_return"] >= config.pump_return_pct / 100.0:
                row["early_pump_seen"] = True
        if horizon == 3_600:
            snapshot_return = row[f"{prefix}_snapshot_return"]
            near_return = row[f"{prefix}_near_target_price_return"]
            if snapshot_return is None:
                tags.append("missing_1h_snapshot")
            elif snapshot_return < 0.0:
                tags.append("negative_1h_snapshot")
            if near_return is None:
                tags.append("no_near_target_1h_observation")
            elif near_return < 0.0:
                tags.append("negative_near_target_1h_price_proxy")
            if row.get("early_pump_seen") and snapshot_return is not None and snapshot_return < 0.0:
                tags.append("pump_then_reversal")
            if snapshot_age is not None and snapshot_age > config.stale_snapshot_seconds:
                tags.append("stale_1h_snapshot")
        if horizon == 21_600:
            snapshot_return = row[f"{prefix}_snapshot_return"]
            if (
                row.get("h3600_snapshot_return") is not None
                and row["h3600_snapshot_return"] >= 0.0
                and snapshot_return is not None
                and snapshot_return < 0.0
            ):
                tags.append("short_lived_heat")
        if horizon == 86_400:
            snapshot_return = row[f"{prefix}_snapshot_return"]
            if (
                row.get("h21600_snapshot_return") is not None
                and row["h21600_snapshot_return"] >= 0.0
                and snapshot_return is not None
                and snapshot_return < 0.0
            ):
                tags.append("long_horizon_decay")

    if _finite(row.get("post_sell_pressure"), 0.0) > 0.60:
        tags.append("post_confirmation_sell_wave")
    if row.get("next_observed_wait_seconds") is not None and row["next_observed_wait_seconds"] > config.stale_snapshot_seconds:
        tags.append("delayed_next_observation")
    row["failure_tags"] = sorted(set(tags))
    row["is_failure_1h_snapshot"] = bool(row.get("h3600_snapshot_return") is not None and row["h3600_snapshot_return"] < 0.0)
    return row


def summarize_attribution(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tag_counts = Counter(tag for row in rows for tag in row.get("failure_tags") or [])
    def distribution(field: str) -> dict[str, Any]:
        values = [
            _finite(row.get(field), math.nan)
            for row in rows
            if row.get(field) is not None and math.isfinite(_finite(row.get(field), math.nan))
        ]
        return {
            "count": len(values),
            "mean": float(statistics.mean(values)) if values else None,
            "median": float(statistics.median(values)) if values else None,
            "positive_rate": float(sum(value > 0.0 for value in values) / len(values)) if values else None,
        }
    return {
        "candidate_count": len(rows),
        "execution_unknown_count": len(rows),
        "model_selection_eligible": False,
        "return_scope": "unvalidated_price_proxies_not_capital_returns",
        "snapshot_1h": distribution("h3600_snapshot_return"),
        "near_target_observations_1h": distribution("h3600_near_target_price_return"),
        "snapshot_6h": distribution("h21600_snapshot_return"),
        "snapshot_24h": distribution("h86400_snapshot_return"),
        "failure_tag_counts": dict(sorted(tag_counts.items())),
        "negative_1h_snapshot_count": sum(bool(row.get("is_failure_1h_snapshot")) for row in rows),
    }


def attribute_lifecycles(
    lifecycles: Iterable[Mapping[str, Any]],
    *,
    config: FailureAttributionConfig | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [row for lifecycle in lifecycles if (row := attribute_candidate(lifecycle, config=config)) is not None]
    rows.sort(key=lambda row: (_finite(row.get("h3600_snapshot_return"), 1e9), str(row.get("token") or "")))
    return rows, summarize_attribution(rows)


__all__ = [
    "FailureAttributionConfig",
    "attribute_candidate",
    "attribute_lifecycles",
    "summarize_attribution",
]

"""Legacy price/activity profiling; not validated market-cap or execution data.

The old lifecycle format labels quote quantity as bnb_amount even for ERC-20
quotes. BNB/USD conversion below is only a legacy assumption, not evidence of
the token's denomination. Use the profitability evidence audit before making
any economic interpretation. Replay inputs need raw-log and metadata repair.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import math
import statistics
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_HORIZONS_SECONDS = (3_600, 21_600, 86_400)
DEFAULT_THRESHOLDS_USD = (10_000.0, 30_000.0, 50_000.0, 100_000.0)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _event_time(row: Mapping[str, Any]) -> float:
    return _finite(row.get("timestamp"), 0.0)


def _event_price(row: Mapping[str, Any]) -> float:
    return _finite(row.get("price"), 0.0)


def _price_path(lifecycle: Mapping[str, Any]) -> tuple[list[float], list[float]]:
    # Stable time sorting preserves recorded ties. Sorting by price previously
    # invented ascending moves within a second. Raw block/log provenance is
    # still required to establish the real intra-second order.
    points = sorted(
        [
            (_event_time(row), _event_price(row))
            for row in lifecycle.get("price_history") or []
            if (
                isinstance(row, Mapping)
                and _event_time(row) > 0.0
                and _event_price(row) > 0.0
            )
        ],
        key=lambda point: point[0],
    )
    return [row[0] for row in points], [row[1] for row in points]


def _at_or_before(times: Sequence[float], prices: Sequence[float], timestamp: float) -> tuple[float, float] | None:
    index = bisect_right(times, float(timestamp)) - 1
    if index < 0:
        return None
    return float(times[index]), float(prices[index])


def _at_or_after(times: Sequence[float], prices: Sequence[float], timestamp: float) -> tuple[float, float] | None:
    index = bisect_left(times, float(timestamp))
    if index >= len(times):
        return None
    return float(times[index]), float(prices[index])


def implied_market_cap_bnb(price: float, total_supply_raw: float) -> float:
    """Legacy FDV proxy: valid in BNB only if native quote/18 decimals are verified."""
    return max(0.0, _finite(price) * _finite(total_supply_raw) / 1e18)


@dataclass(frozen=True)
class MarketCapHeatConfig:
    bnb_usd: float = 734.17
    confirmation_seconds: int = 30
    execution_delay_seconds: int = 3
    heat_window_seconds: int = 30
    min_unique_buyers: int = 3
    min_buy_count: int = 5
    min_signed_imbalance: float = 0.20
    max_sell_pressure: float = 0.60
    fee_rate: float = 0.01
    slippage_rate: float = 0.02
    horizons_seconds: tuple[int, ...] = DEFAULT_HORIZONS_SECONDS

    def __post_init__(self) -> None:
        if self.bnb_usd <= 0.0 or not math.isfinite(self.bnb_usd):
            raise ValueError("bnb_usd must be positive and finite")
        if self.confirmation_seconds < 0 or self.execution_delay_seconds < 0 or self.heat_window_seconds <= 0:
            raise ValueError("confirmation and heat windows are invalid")
        if self.min_unique_buyers < 1 or self.min_buy_count < 1:
            raise ValueError("heat activity thresholds must be positive")
        if not 0.0 <= self.max_sell_pressure <= 1.0:
            raise ValueError("max_sell_pressure must be between 0 and 1")
        if not 0.0 <= self.min_signed_imbalance <= 1.0:
            raise ValueError("min_signed_imbalance must be between 0 and 1")
        if self.fee_rate < 0.0 or self.slippage_rate < 0.0:
            raise ValueError("cost rates must be non-negative")


def _sorted_rows(lifecycle: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    return sorted(
        [row for row in lifecycle.get(key) or [] if isinstance(row, Mapping)],
        key=lambda row: (_event_time(row), str(row.get("transaction_hash") or "")),
    )


def _candidate_for_threshold(
    lifecycle: Mapping[str, Any],
    threshold_usd: float,
    config: MarketCapHeatConfig,
) -> dict[str, Any] | None:
    total_supply_raw = _finite(lifecycle.get("total_supply"), 0.0)
    if total_supply_raw <= 0.0 or threshold_usd <= 0.0:
        return None
    threshold_bnb = float(threshold_usd) / config.bnb_usd
    buys = _sorted_rows(lifecycle, "buys")
    sells = _sorted_rows(lifecycle, "sells")
    times, prices = _price_path(lifecycle)
    if not buys or not times:
        return None

    crossing: tuple[float, float, float] | None = None
    for buy in buys:
        timestamp = _event_time(buy)
        price = _event_price(buy)
        market_cap_bnb = implied_market_cap_bnb(price, total_supply_raw)
        # Allow a tiny floating-point tolerance at an exact threshold crossing.
        if timestamp > 0.0 and price > 0.0 and market_cap_bnb >= threshold_bnb * (1.0 - 1e-12):
            crossing = (timestamp, price, market_cap_bnb)
            break
    if crossing is None:
        return None

    crossing_time, _, crossing_mcap_bnb = crossing
    confirmation_time = crossing_time + config.confirmation_seconds
    confirmation = _at_or_before(times, prices, confirmation_time)
    if confirmation is None:
        return None
    _, confirmation_price = confirmation
    confirmation_mcap_bnb = implied_market_cap_bnb(confirmation_price, total_supply_raw)
    if confirmation_mcap_bnb < threshold_bnb * (1.0 - 1e-12):
        return None

    window_start = confirmation_time - config.heat_window_seconds
    recent_buys = [row for row in buys if window_start <= _event_time(row) <= confirmation_time]
    recent_sells = [row for row in sells if window_start <= _event_time(row) <= confirmation_time]
    buyers = {str(row.get("account") or "").lower() for row in recent_buys if row.get("account")}
    buy_volume = sum(_finite(row.get("bnb_amount"), 0.0) for row in recent_buys)
    sell_volume = sum(_finite(row.get("bnb_amount"), 0.0) for row in recent_sells)
    total_flow = buy_volume + sell_volume
    sell_pressure = sell_volume / total_flow if total_flow > 0.0 else 0.0
    signed_imbalance = (buy_volume - sell_volume) / total_flow if total_flow > 0.0 else 0.0
    entry = _at_or_after(times, prices, confirmation_time + config.execution_delay_seconds)
    return {
        "token": str(lifecycle.get("token_address") or lifecycle.get("token") or "").lower(),
        "symbol": lifecycle.get("symbol"),
        "crossing_time": crossing_time,
        "confirmation_time": confirmation_time,
        "entry_time": entry[0] if entry else None,
        "entry_price": entry[1] if entry else None,
        "threshold_usd": float(threshold_usd),
        "threshold_bnb": threshold_bnb,
        "crossing_mcap_bnb": crossing_mcap_bnb,
        "confirmation_price": confirmation_price,
        "confirmation_mcap_bnb": confirmation_mcap_bnb,
        "confirmation_mcap_usd": confirmation_mcap_bnb * config.bnb_usd,
        "buyers_30": len(buyers),
        "buy_count_30": len(recent_buys),
        "buy_volume_30": buy_volume,
        "sell_volume_30": sell_volume,
        "sell_pressure_30": sell_pressure,
        "signed_imbalance_30": signed_imbalance,
    }


def _with_returns(
    candidate: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    config: MarketCapHeatConfig,
) -> dict[str, Any]:
    result = dict(candidate)
    times, prices = _price_path(lifecycle)
    entry_time = _finite(candidate.get("entry_time"), 0.0)
    entry_price = _finite(candidate.get("entry_price"), 0.0)
    for horizon in config.horizons_seconds:
        value: float | None = None
        if entry_time > 0.0 and entry_price > 0.0:
            exit_point = _at_or_after(times, prices, entry_time + int(horizon))
            if exit_point is not None:
                value = (
                    exit_point[1] * (1.0 - config.slippage_rate) * (1.0 - config.fee_rate)
                    / (entry_price * (1.0 + config.slippage_rate) * (1.0 + config.fee_rate))
                ) - 1.0
        result[f"return_{int(horizon)}"] = value
    return result


def _heat_passes(candidate: Mapping[str, Any], config: MarketCapHeatConfig, *, flow: bool) -> bool:
    if int(candidate.get("buyers_30", 0) or 0) < config.min_unique_buyers:
        return False
    if int(candidate.get("buy_count_30", 0) or 0) < config.min_buy_count:
        return False
    if flow and (
        _finite(candidate.get("signed_imbalance_30"), 0.0) < config.min_signed_imbalance
        or _finite(candidate.get("sell_pressure_30"), 0.0) > config.max_sell_pressure
    ):
        return False
    return True


def _distribution(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"complete": 0, "mean": None, "median": None, "positive_rate": None}
    return {
        "complete": len(values),
        "mean": float(statistics.mean(values)),
        "median": float(statistics.median(values)),
        "positive_rate": float(sum(value > 0.0 for value in values) / len(values)),
    }


def profile_market_cap_heat_gate(
    lifecycles: Iterable[Mapping[str, Any]],
    *,
    thresholds_usd: Sequence[float] = DEFAULT_THRESHOLDS_USD,
    config: MarketCapHeatConfig | None = None,
) -> dict[str, Any]:
    config = config or MarketCapHeatConfig()
    lifecycle_rows = list(lifecycles)
    output: dict[str, Any] = {
        "schema_version": 2,
        "evidence_status": "legacy_price_diagnostic_unvalidated",
        "model_selection_eligible": False,
        "execution_verified": False,
        "limitations": [
            "quote_asset_and_historical_fx_unverified",
            "raw_trade_decoding_requires_audit",
            "same_second_order_requires_raw_provenance",
            "next_event_lookup_does_not_establish_fixed_horizon_execution",
            "counts_do_not_establish_independent_buyers_or_x_heat",
        ],
        "safe_for_live_switch": False,
        "config": {
            "bnb_usd": config.bnb_usd,
            "confirmation_seconds": config.confirmation_seconds,
            "execution_delay_seconds": config.execution_delay_seconds,
            "heat_window_seconds": config.heat_window_seconds,
            "min_unique_buyers": config.min_unique_buyers,
            "min_buy_count": config.min_buy_count,
            "min_signed_imbalance": config.min_signed_imbalance,
            "max_sell_pressure": config.max_sell_pressure,
            "fee_rate": config.fee_rate,
            "slippage_rate": config.slippage_rate,
            "horizons_seconds": list(config.horizons_seconds),
        },
        "thresholds": {},
    }
    for threshold_usd in thresholds_usd:
        candidates = []
        for lifecycle in lifecycle_rows:
            candidate = _candidate_for_threshold(lifecycle, float(threshold_usd), config)
            if candidate is not None:
                candidates.append(_with_returns(candidate, lifecycle, config))
        threshold_result: dict[str, Any] = {
            "crossing_and_confirmed_count": len(candidates),
            "modes": {},
        }
        for mode, flow in (("activity", False), ("activity_flow", True)):
            selected = [candidate for candidate in candidates if _heat_passes(candidate, config, flow=flow)]
            mode_result: dict[str, Any] = {"selected_count": len(selected)}
            for horizon in config.horizons_seconds:
                values = [
                    _finite(candidate.get(f"return_{int(horizon)}"), math.nan)
                    for candidate in selected
                    if candidate.get(f"return_{int(horizon)}") is not None
                ]
                values = [value for value in values if math.isfinite(value)]
                mode_result[str(int(horizon))] = _distribution(values)
            threshold_result["modes"][mode] = mode_result
        output["thresholds"][str(float(threshold_usd))] = threshold_result
    return output


__all__ = [
    "DEFAULT_HORIZONS_SECONDS",
    "DEFAULT_THRESHOLDS_USD",
    "MarketCapHeatConfig",
    "implied_market_cap_bnb",
    "profile_market_cap_heat_gate",
]

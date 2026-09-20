"""Conditional health policy for retaining a runner after graduation.

FourMeme lifecycle events stop being a useful market feed once a token moves
to a DEX.  This policy therefore requires a fresh, point-in-time DEX snapshot
before it will retain an already activated runner.  It is deliberately pure:
callers decide whether a ``close`` decision is shadowed or executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Mapping


RETENTION_DISABLED = "disabled"
RETENTION_NONE = "none"
RETENTION_HOLD = "hold"
RETENTION_CLOSE = "close"


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _timestamp(value: Any) -> float | None:
    if isinstance(value, datetime):
        return value.timestamp()
    parsed = _finite(value)
    if parsed is not None:
        return parsed
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed_dt = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
    return parsed_dt.timestamp()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "graduated"}


@dataclass(frozen=True)
class RetentionHealthConfig:
    """Guardrails for an activated runner after the curve-to-DEX boundary."""

    enabled: bool = False
    max_hold_seconds: float = 4 * 86_400.0
    max_data_age_seconds: float = 120.0
    min_liquidity_usd: float = 10_000.0
    min_volume_liquidity_ratio_5m: float = 0.20
    min_new_traders_1h: float = 2.0
    max_sell_pressure_5m: float = 0.60
    max_drawdown_from_peak: float = 0.35

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_hold_seconds) or self.max_hold_seconds <= 0.0:
            raise ValueError("max_hold_seconds must be positive")
        if not math.isfinite(self.max_data_age_seconds) or self.max_data_age_seconds <= 0.0:
            raise ValueError("max_data_age_seconds must be positive")
        if not math.isfinite(self.min_liquidity_usd) or self.min_liquidity_usd < 0.0:
            raise ValueError("min_liquidity_usd must be finite and non-negative")
        if (
            not math.isfinite(self.min_volume_liquidity_ratio_5m)
            or self.min_volume_liquidity_ratio_5m < 0.0
        ):
            raise ValueError("min_volume_liquidity_ratio_5m must be finite and non-negative")
        if not math.isfinite(self.min_new_traders_1h) or self.min_new_traders_1h < 0.0:
            raise ValueError("min_new_traders_1h must be finite and non-negative")
        if not math.isfinite(self.max_sell_pressure_5m) or not 0.0 <= self.max_sell_pressure_5m <= 1.0:
            raise ValueError("max_sell_pressure_5m must be between 0 and 1")
        if not math.isfinite(self.max_drawdown_from_peak) or not 0.0 < self.max_drawdown_from_peak < 1.0:
            raise ValueError("max_drawdown_from_peak must be between 0 and 1")


@dataclass(frozen=True)
class RetentionDecision:
    """Decision returned for one point-in-time post-graduation snapshot."""

    action: str
    reason: str
    health_score: float | None = None
    checks: Mapping[str, bool] = field(default_factory=dict)
    missing_fields: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)
    updates: Mapping[str, Any] = field(default_factory=dict)

    @property
    def should_close(self) -> bool:
        return self.action == RETENTION_CLOSE


class PostGraduationRetentionPolicy:
    """Evaluate DEX health without mutating a position or market snapshot."""

    _LIQUIDITY_FIELDS = (
        "dex_liquidity_usd",
        "liquidity_usd",
        "pair_liquidity_usd",
        "liquidity",
    )
    _RATIO_FIELDS = (
        "volume_liquidity_ratio_5m",
        "dex_volume_liquidity_ratio_5m",
        "volume_to_liquidity_5m",
    )
    _VOLUME_FIELDS = (
        "dex_volume_5m_usd",
        "volume_5m_usd",
        "volume_5m",
    )
    _NEW_TRADER_FIELDS = (
        "new_traders_1h",
        "new_trader_growth_1h",
        "dex_new_traders_1h",
    )
    _SELL_PRESSURE_FIELDS = (
        "sell_pressure_5m",
        "dex_sell_pressure_5m",
        "sell_pressure",
    )
    _BUY_VOLUME_FIELDS = ("dex_buy_volume_5m_usd", "buy_volume_5m_usd", "buy_volume_5m")
    _SELL_VOLUME_FIELDS = ("dex_sell_volume_5m_usd", "sell_volume_5m_usd", "sell_volume_5m")
    _OBSERVED_FIELDS = (
        "observed_at",
        "dex_observed_at",
        "market_timestamp",
        "updated_at",
        "timestamp",
    )
    _SOURCE_FIELDS = ("data_source", "dex_data_source", "source")

    def __init__(self, config: RetentionHealthConfig | None = None):
        self.config = config or RetentionHealthConfig()

    @staticmethod
    def _first_value(market: Mapping[str, Any], names: tuple[str, ...]) -> tuple[float | None, str | None]:
        for name in names:
            value = _finite(market.get(name))
            if value is not None:
                return value, name
        return None, None

    @staticmethod
    def _first_timestamp(market: Mapping[str, Any], names: tuple[str, ...]) -> tuple[float | None, str | None]:
        for name in names:
            value = _timestamp(market.get(name))
            if value is not None:
                return value, name
        return None, None

    @staticmethod
    def _entry_time(position: Mapping[str, Any]) -> tuple[float | None, str | None]:
        for name in ("runner_graduated_at", "retention_started_at", "entry_time"):
            value = _timestamp(position.get(name))
            if value is not None:
                return value, name
        return None, None

    @staticmethod
    def _drawdown(
        position: Mapping[str, Any],
        market: Mapping[str, Any],
        current_price: Any,
    ) -> tuple[float | None, str | None]:
        value, source = PostGraduationRetentionPolicy._first_value(
            market,
            ("drawdown_from_peak", "peak_drawdown", "dex_drawdown_from_peak"),
        )
        if value is not None:
            return value, source
        price = _finite(current_price)
        peak = max(
            _finite(position.get("runner_peak_price"), 0.0) or 0.0,
            _finite(position.get("peak_price"), 0.0) or 0.0,
        )
        if price is None or price <= 0.0 or peak <= 0.0:
            return None, None
        return max(0.0, 1.0 - price / peak), "current_price_vs_runner_peak"

    def _provenance(
        self,
        *,
        market: Mapping[str, Any],
        now_ts: float,
        observed_ts: float | None,
        data_age: float | None,
        metric_sources: Mapping[str, str | None],
    ) -> dict[str, Any]:
        source = next((str(market[name]) for name in self._SOURCE_FIELDS if market.get(name)), "unknown")
        provenance = {
            "evaluated_at": now_ts,
            "observed_at": observed_ts,
            "data_age_seconds": data_age,
            "data_source": source,
            "metric_sources": dict(metric_sources),
        }
        for name in ("chain_id", "pair_address", "pair", "pair_created_at", "block_number", "log_index", "tx_hash"):
            if market.get(name) is not None:
                provenance[name] = market[name]
        return provenance

    @staticmethod
    def _base_updates(
        *,
        now_ts: float,
        health_score: float | None,
        provenance: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "retention_state": "active",
            "retention_last_checked_at": now_ts,
            "retention_health_score": health_score,
            "retention_data_age_seconds": provenance.get("data_age_seconds"),
            "retention_data_source": provenance.get("data_source"),
            "retention_metric_sources": dict(provenance.get("metric_sources") or {}),
        }

    def evaluate(
        self,
        position: Mapping[str, Any],
        *,
        market: Mapping[str, Any] | None,
        current_price: Any,
        now: Any,
    ) -> RetentionDecision:
        """Return a fail-closed transition for one DEX health observation."""

        if not self.config.enabled:
            return RetentionDecision(RETENTION_DISABLED, "post_graduation_retention_disabled")

        market = market or {}
        now_ts = _timestamp(now)
        if now_ts is None:
            return RetentionDecision(RETENTION_CLOSE, "retention_invalid_evaluation_time")

        if str(position.get("runner_state") or "").lower() != "active":
            return RetentionDecision(RETENTION_NONE, "retention_runner_not_active")

        graduated = market.get("graduated", position.get("runner_graduated", position.get("graduated")))
        if not _as_bool(graduated):
            return RetentionDecision(RETENTION_NONE, "retention_waiting_for_graduation")

        observed_ts, observed_source = self._first_timestamp(market, self._OBSERVED_FIELDS)
        explicit_age, age_source = self._first_value(market, ("data_age_seconds", "dex_data_age_seconds"))
        if explicit_age is not None:
            data_age = explicit_age
        elif observed_ts is not None:
            data_age = now_ts - observed_ts
            age_source = observed_source
        else:
            data_age = None

        liquidity, liquidity_source = self._first_value(market, self._LIQUIDITY_FIELDS)
        ratio, ratio_source = self._first_value(market, self._RATIO_FIELDS)
        if ratio is None and liquidity is not None and liquidity > 0.0:
            volume, volume_source = self._first_value(market, self._VOLUME_FIELDS)
            if volume is not None:
                ratio = volume / liquidity
                ratio_source = f"{volume_source}/{liquidity_source}"

        new_traders, new_traders_source = self._first_value(market, self._NEW_TRADER_FIELDS)
        sell_pressure, sell_pressure_source = self._first_value(market, self._SELL_PRESSURE_FIELDS)
        if sell_pressure is None:
            buy_volume, buy_volume_source = self._first_value(market, self._BUY_VOLUME_FIELDS)
            sell_volume, sell_volume_source = self._first_value(market, self._SELL_VOLUME_FIELDS)
            if buy_volume is not None and sell_volume is not None and buy_volume + sell_volume > 0.0:
                sell_pressure = sell_volume / (buy_volume + sell_volume)
                sell_pressure_source = f"{sell_volume_source}/({buy_volume_source}+{sell_volume_source})"

        drawdown, drawdown_source = self._drawdown(position, market, current_price)
        values = {
            "liquidity_usd": liquidity,
            "volume_liquidity_ratio_5m": ratio,
            "new_traders_1h": new_traders,
            "sell_pressure_5m": sell_pressure,
            "drawdown_from_peak": drawdown,
            "data_age_seconds": data_age,
            "observed_at": observed_ts,
        }
        missing = tuple(sorted(name for name, value in values.items() if value is None))
        metric_sources = {
            "liquidity_usd": liquidity_source,
            "volume_liquidity_ratio_5m": ratio_source,
            "new_traders_1h": new_traders_source,
            "sell_pressure_5m": sell_pressure_source,
            "drawdown_from_peak": drawdown_source,
            "data_age_seconds": age_source,
            "observed_at": observed_source,
        }
        provenance = self._provenance(
            market=market,
            now_ts=now_ts,
            observed_ts=observed_ts,
            data_age=data_age,
            metric_sources=metric_sources,
        )

        if missing:
            return RetentionDecision(
                RETENTION_CLOSE,
                "retention_missing_health_data",
                missing_fields=missing,
                provenance=provenance,
            )

        invalid = []
        if liquidity < 0.0:
            invalid.append("liquidity_usd")
        if ratio < 0.0:
            invalid.append("volume_liquidity_ratio_5m")
        if new_traders < 0.0:
            invalid.append("new_traders_1h")
        if not 0.0 <= sell_pressure <= 1.0:
            invalid.append("sell_pressure_5m")
        if not 0.0 <= drawdown <= 1.0:
            invalid.append("drawdown_from_peak")
        if data_age < 0.0:
            invalid.append("data_age_seconds")
        if invalid:
            invalid_fields = tuple(sorted(set(invalid)))
            return RetentionDecision(
                RETENTION_CLOSE,
                "retention_invalid_health_data",
                missing_fields=invalid_fields,
                provenance=provenance,
            )

        checks = {
            "liquidity": liquidity >= self.config.min_liquidity_usd,
            "volume": ratio >= self.config.min_volume_liquidity_ratio_5m,
            "new_traders": new_traders >= self.config.min_new_traders_1h,
            "sell_pressure": sell_pressure <= self.config.max_sell_pressure_5m,
            "drawdown": drawdown <= self.config.max_drawdown_from_peak,
            "freshness": data_age <= self.config.max_data_age_seconds,
        }
        health_score = sum(checks.values()) / len(checks)
        updates = self._base_updates(now_ts=now_ts, health_score=health_score, provenance=provenance)

        entry_ts, entry_source = self._entry_time(position)
        if entry_ts is None:
            return RetentionDecision(
                RETENTION_CLOSE,
                "retention_missing_position_time",
                health_score=health_score,
                checks=checks,
                missing_fields=("entry_time",),
                provenance={**provenance, "position_time_source": entry_source},
                updates=updates,
            )
        if now_ts - entry_ts >= self.config.max_hold_seconds:
            return RetentionDecision(
                RETENTION_CLOSE,
                "retention_max_hold",
                health_score=health_score,
                checks=checks,
                provenance={**provenance, "position_time_source": entry_source},
                updates=updates,
            )

        reason_by_check = (
            ("freshness", "retention_stale_health_data"),
            ("liquidity", "retention_liquidity_decay"),
            ("volume", "retention_volume_decay"),
            ("new_traders", "retention_new_trader_decay"),
            ("sell_pressure", "retention_sell_pressure"),
            ("drawdown", "retention_peak_drawdown"),
        )
        for check_name, reason in reason_by_check:
            if not checks[check_name]:
                return RetentionDecision(
                    RETENTION_CLOSE,
                    reason,
                    health_score=health_score,
                    checks=checks,
                    provenance={**provenance, "position_time_source": entry_source},
                    updates=updates,
                )

        return RetentionDecision(
            RETENTION_HOLD,
            "retention_healthy_hold",
            health_score=health_score,
            checks=checks,
            provenance={**provenance, "position_time_source": entry_source},
            updates=updates,
        )


__all__ = [
    "RETENTION_CLOSE",
    "RETENTION_DISABLED",
    "RETENTION_HOLD",
    "RETENTION_NONE",
    "PostGraduationRetentionPolicy",
    "RetentionDecision",
    "RetentionHealthConfig",
]

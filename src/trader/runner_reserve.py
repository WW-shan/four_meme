"""Small, deterministic policy for retaining a post-breakout runner reserve.

The policy is intentionally independent from the bot and from model artifacts.
It only consumes point-in-time position/market state and returns a decision plus
state updates. Keeping it pure makes the live integration easy to shadow and
lets replay code use exactly the same transition rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Any, Mapping


RUNNER_DISABLED = "disabled"
RUNNER_ARMED = "armed"
RUNNER_ACTIVE = "active"


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _timestamp(value: Any) -> float | None:
    if isinstance(value, datetime):
        return value.timestamp()
    parsed = _finite(value)
    if parsed is not None:
        return parsed
    return None


@dataclass(frozen=True)
class RunnerReserveConfig:
    """Runtime guardrails for a reserve after the first executable breakout."""

    enabled: bool = False
    activation_multiple: float = 2.0
    partial_exit_ratio: float = 0.70
    stop_drawdown_from_peak: float = 0.30
    floor_return_after_activation: float = 0.0
    max_hold_seconds: float = 86_400.0
    max_sell_pressure_30s: float = 0.55
    min_flow_event_count_30s: int = 1

    def __post_init__(self) -> None:
        if self.activation_multiple <= 1.0 or not math.isfinite(self.activation_multiple):
            raise ValueError("activation_multiple must be finite and greater than 1")
        if not 0.0 < self.partial_exit_ratio < 1.0:
            raise ValueError("partial_exit_ratio must be between 0 and 1")
        if not 0.0 < self.stop_drawdown_from_peak < 1.0:
            raise ValueError("stop_drawdown_from_peak must be between 0 and 1")
        if not math.isfinite(self.floor_return_after_activation) or self.floor_return_after_activation < -1.0:
            raise ValueError("floor_return_after_activation must be finite and >= -1")
        if not math.isfinite(self.max_hold_seconds) or self.max_hold_seconds <= 0.0:
            raise ValueError("max_hold_seconds must be positive")
        if not math.isfinite(self.max_sell_pressure_30s) or not 0.0 <= self.max_sell_pressure_30s <= 1.0:
            raise ValueError("max_sell_pressure_30s must be between 0 and 1")
        if int(self.min_flow_event_count_30s) < 0:
            raise ValueError("min_flow_event_count_30s must be non-negative")


@dataclass(frozen=True)
class RunnerReserveDecision:
    """One policy decision; `updates` are applied only after execution succeeds."""

    action: str
    reason: str
    partial_exit_ratio: float | None = None
    updates: Mapping[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.action == "hold" and self.updates.get("runner_state") == RUNNER_ACTIVE


class RunnerReservePolicy:
    """Evaluate activation and retention without mutating the supplied state."""

    def __init__(self, config: RunnerReserveConfig | None = None):
        self.config = config or RunnerReserveConfig()

    @staticmethod
    def _market_value(market: Mapping[str, Any], *names: str) -> float | None:
        for name in names:
            value = _finite(market.get(name))
            if value is not None:
                return value
        return None

    def _healthy(self, market: Mapping[str, Any]) -> bool:
        """Reject an active reserve only when a supplied flow metric is toxic."""
        sell_pressure = self._market_value(
            market,
            "sell_pressure_30s",
            "flow_sell_pressure_30s",
        )
        if sell_pressure is not None and sell_pressure > self.config.max_sell_pressure_30s:
            return False
        event_count = self._market_value(market, "flow_event_count_30s")
        if event_count is not None and event_count < float(self.config.min_flow_event_count_30s):
            return False
        return True

    @staticmethod
    def _entry_price(position: Mapping[str, Any]) -> float | None:
        for key in ("entry_price", "tp_base_price", "signal_price"):
            price = _finite(position.get(key))
            if price is not None and price > 0.0:
                return price
        return None

    @staticmethod
    def _position_age_seconds(position: Mapping[str, Any], now: float) -> float | None:
        entry = _timestamp(position.get("entry_time"))
        if entry is None:
            return None
        return max(0.0, float(now) - entry)

    def _close(self, reason: str, *, updates: Mapping[str, Any] | None = None) -> RunnerReserveDecision:
        return RunnerReserveDecision("close", reason, updates=updates or {})

    def evaluate(
        self,
        position: Mapping[str, Any],
        *,
        current_price: float,
        now: Any,
        market: Mapping[str, Any] | None = None,
    ) -> RunnerReserveDecision:
        """Return a transition for a point-in-time price/flow observation.

        The bot enforces its hard stop before calling this policy. The policy's
        own floor and trailing stop protect only the retained reserve after the
        activation partial exit.
        """

        if not self.config.enabled:
            return RunnerReserveDecision("disabled", "runner_reserve_disabled")

        price = _finite(current_price)
        entry_price = self._entry_price(position)
        now_ts = _timestamp(now)
        if price is None or price <= 0.0 or entry_price is None or now_ts is None:
            return RunnerReserveDecision("none", "runner_state_missing_price_or_time")

        state = str(position.get("runner_state") or RUNNER_ARMED).lower()
        if state in {RUNNER_DISABLED, "closed", "released"}:
            return RunnerReserveDecision("none", "runner_state_closed")

        if state != RUNNER_ACTIVE:
            activation_price = entry_price * self.config.activation_multiple
            if price < activation_price:
                return RunnerReserveDecision("none", "runner_activation_not_reached")
            return RunnerReserveDecision(
                "partial_exit",
                "runner_activation_breakout",
                partial_exit_ratio=self.config.partial_exit_ratio,
                updates={
                    "runner_state": RUNNER_ACTIVE,
                    "runner_peak_price": price,
                    "runner_activated_at": now_ts,
                    "runner_activation_price": price,
                },
            )

        peak_price = max(
            price,
            _finite(position.get("runner_peak_price"), 0.0) or 0.0,
            _finite(position.get("peak_price"), 0.0) or 0.0,
        )
        updates = {"runner_state": RUNNER_ACTIVE, "runner_peak_price": peak_price}
        age_seconds = self._position_age_seconds(position, now_ts)
        if age_seconds is not None and age_seconds >= self.config.max_hold_seconds:
            return self._close("runner_max_hold", updates=updates)

        floor_price = entry_price * (1.0 + self.config.floor_return_after_activation)
        if price <= floor_price:
            return self._close("runner_floor_breach", updates=updates)

        trailing_price = peak_price * (1.0 - self.config.stop_drawdown_from_peak)
        if price <= trailing_price:
            return self._close("runner_peak_drawdown", updates=updates)

        if not self._healthy(market or {}):
            return self._close("runner_flow_health_exit", updates=updates)

        return RunnerReserveDecision("hold", "runner_healthy_hold", updates=updates)

    def should_survive_graduation(self, position: Mapping[str, Any]) -> bool:
        """Only an already activated reserve may cross the bonding-curve boundary."""
        return bool(self.config.enabled and str(position.get("runner_state") or "").lower() == RUNNER_ACTIVE)

    def defers_short_hold_timeout(self, position: Mapping[str, Any], age_seconds: float) -> bool:
        """Keep the ordinary short timeout from killing an active reserve."""
        if not self.config.enabled or str(position.get("runner_state") or "").lower() != RUNNER_ACTIVE:
            return False
        return float(age_seconds) < self.config.max_hold_seconds

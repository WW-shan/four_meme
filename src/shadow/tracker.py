"""Shadow position tracker applying exit rules against real quote updates."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.shadow.exits import ExitAction, ExitRuleConfig, ExitState, evaluate_exit
from src.shadow.executor import ShadowFill, ShadowPosition


@dataclass
class ShadowTracker:
    store: object | None = None
    config: ExitRuleConfig = field(default_factory=ExitRuleConfig)
    positions: dict[str, ShadowPosition] = field(default_factory=dict)
    exit_states: dict[str, ExitState] = field(default_factory=dict)

    def open(self, fill: ShadowFill, *, baseline_liquidity: float | None = None) -> ShadowPosition:
        position = ShadowPosition(fill.token, fill, peak_price=fill.price)
        self.positions[fill.token] = position
        self.exit_states[fill.token] = ExitState(
            entry_price=fill.price, opened_at=fill.at, peak_price=fill.price,
            baseline_liquidity=baseline_liquidity,
        )
        self._record("shadow_open", fill.token, {"fill": fill.__dict__, "baseline_liquidity": baseline_liquidity}, fill.at)
        self.realized_quote = getattr(self, "realized_quote", {})
        self.realized_quote[fill.token] = 0.0
        self.entry_tokens = getattr(self, "entry_tokens", {})
        self.entry_tokens[fill.token] = fill.token_amount
        self.entry_quote = getattr(self, "entry_quote", {})
        self.entry_quote[fill.token] = fill.quote_amount
        self.opened_at = getattr(self, "opened_at", {})
        self.opened_at[fill.token] = fill.at
        return position

    def update(self, token: str, price: float, *, now: float, liquidity_usd: float | None = None) -> ExitAction | None:
        position = self.positions.get(token)
        state = self.exit_states.get(token)
        if position is None or state is None:
            return None
        position.update_price(price)
        state.peak_price = position.peak_price
        action = evaluate_exit(state, price, now=now, liquidity_usd=liquidity_usd, config=self.config)
        if action is None:
            return None
        fraction = min(action.fraction, state.remaining_fraction)
        state.remaining_fraction -= fraction
        position.remaining_fraction = state.remaining_fraction
        if action.reason.startswith("take_profit_"):
            index = int(action.reason.rsplit("_", 1)[1]) - 1
            state.filled_tp_levels.add(index)
        position.exits.append({"reason": action.reason, "fraction": fraction, "price": price, "at": now})
        entry_tokens = getattr(self, "entry_tokens", {}).get(token, 0.0)
        fee_pct = position.entry_fill.fee_pct
        proceeds = fraction * entry_tokens * price * (1 - fee_pct / 100)
        realized = getattr(self, "realized_quote", {})
        realized[token] = realized.get(token, 0.0) + proceeds
        self._record("shadow_exit", token,
                     {"reason": action.reason, "fraction": fraction, "price": price, "realized_quote": proceeds}, now)
        if state.remaining_fraction <= 1e-9:
            entry_quote = getattr(self, "entry_quote", {}).get(token, 0.0)
            latency = max(0.0, now - getattr(self, "opened_at", {}).get(token, now))
            self._record("shadow_close", token, {
                "reason": action.reason, "price": price,
                "pnl_quote": realized[token] - entry_quote,
                "quote_amount": entry_quote,
                "latency_seconds": latency,
            }, now)
        return ExitAction(action.reason, fraction, price, now)

    def _record(self, kind: str, token: str, payload: dict, at: float) -> None:
        if self.store is not None and hasattr(self.store, "append"):
            self.store.append(kind, token, payload, at, at)

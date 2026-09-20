"""Exit rules: TP ladder, stop, trailing, time exit, rug watcher, circuit breaker."""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_TP_LADDER = ((0.50, 0.25), (1.00, 0.25), (3.00, 0.25), (9.00, 0.15))


@dataclass(frozen=True)
class ExitRuleConfig:
    tp_ladder: tuple[tuple[float, float], ...] = DEFAULT_TP_LADDER
    stop_loss_pct: float = 0.40
    trailing_after_gain: float = 2.00
    trailing_drawdown_pct: float = 0.30
    max_hold_seconds: float = 1800.0
    time_exit_min_gain: float = 0.50
    rug_liquidity_drop_pct: float = 0.30


@dataclass(frozen=True)
class ExitAction:
    reason: str
    fraction: float
    price: float
    at: float


@dataclass
class ExitState:
    entry_price: float
    opened_at: float
    peak_price: float
    baseline_liquidity: float | None = None
    filled_tp_levels: set[int] = field(default_factory=set)
    remaining_fraction: float = 1.0


def evaluate_exit(state: ExitState, price: float, *, now: float,
                  liquidity_usd: float | None = None,
                  config: ExitRuleConfig | None = None) -> ExitAction | None:
    cfg = config or ExitRuleConfig()
    if price <= 0:
        raise ValueError("price must be positive")
    if state.remaining_fraction <= 0:
        return None

    if (liquidity_usd is not None and state.baseline_liquidity
            and state.baseline_liquidity > 0
            and liquidity_usd <= state.baseline_liquidity * (1 - cfg.rug_liquidity_drop_pct)):
        return ExitAction("rug_liquidity_drop", state.remaining_fraction, price, now)

    gain = price / state.entry_price - 1.0
    if gain <= -cfg.stop_loss_pct:
        return ExitAction("stop_loss", state.remaining_fraction, price, now)

    drawdown = 1 - price / state.peak_price if state.peak_price > 0 else 0.0
    if state.peak_price >= state.entry_price * (1 + cfg.trailing_after_gain) and drawdown >= cfg.trailing_drawdown_pct:
        return ExitAction("trailing_stop", state.remaining_fraction, price, now)

    for index, (target_gain, fraction) in enumerate(cfg.tp_ladder):
        if index in state.filled_tp_levels or gain < target_gain:
            continue
        return ExitAction(f"take_profit_{index + 1}", min(fraction, state.remaining_fraction), price, now)

    if now - state.opened_at >= cfg.max_hold_seconds and gain < cfg.time_exit_min_gain:
        return ExitAction("time_exit", state.remaining_fraction, price, now)
    return None


@dataclass
class DrawdownCircuit:
    daily_loss_pct: float = 0.10
    weekly_loss_pct: float = 0.25
    consecutive_losses_halt: int = 20
    daily_loss: float = 0.0
    weekly_loss: float = 0.0
    consecutive_losses: int = 0

    def record(self, pnl_pct: float) -> None:
        if pnl_pct < 0:
            self.consecutive_losses += 1
            self.daily_loss += abs(pnl_pct)
            self.weekly_loss += abs(pnl_pct)
        else:
            self.consecutive_losses = 0

    def reset_day(self) -> None:
        self.daily_loss = 0.0

    def reset_week(self) -> None:
        self.weekly_loss = 0.0

    def can_open(self) -> tuple[bool, str | None]:
        if self.daily_loss >= self.daily_loss_pct:
            return False, "daily_drawdown"
        if self.weekly_loss >= self.weekly_loss_pct:
            return False, "weekly_drawdown"
        if self.consecutive_losses >= self.consecutive_losses_halt:
            return False, "consecutive_losses"
        return True, None

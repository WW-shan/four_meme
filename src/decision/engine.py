"""Deterministic decisions with explicit reason codes, expiry and mode."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import time

from src.safety.orchestrator import SafetyReport


@dataclass(frozen=True)
class RiskBudget:
    per_trade_pct: float = 1.0
    max_concurrent_positions: int = 10
    max_new_per_minute: int = 3
    daily_loss_pct: float = 0.10
    weekly_loss_pct: float = 0.25
    consecutive_losses_halt: int = 20


@dataclass(frozen=True)
class PortfolioState:
    open_positions: int = 0
    new_positions_last_minute: int = 0
    daily_loss_pct: float = 0.0
    weekly_loss_pct: float = 0.0
    consecutive_losses: int = 0


@dataclass(frozen=True)
class Decision:
    token: str
    action: str  # buy | watch | reject
    mode: str  # shadow | live
    size_quote: float
    reason_codes: tuple[str, ...]
    safety_verdict: str
    funding_confirmed: bool
    expires_at: float
    created_at: float
    schema_version: int = 2

    def to_dict(self) -> dict:
        return asdict(self)

    def expired(self, now: float | None = None) -> bool:
        return (time.time() if now is None else float(now)) >= self.expires_at


def risk_allows(budget: RiskBudget, state: PortfolioState) -> tuple[bool, str | None]:
    if state.open_positions >= budget.max_concurrent_positions:
        return False, "max_concurrent_positions"
    if state.new_positions_last_minute >= budget.max_new_per_minute:
        return False, "max_new_per_minute"
    if state.daily_loss_pct >= budget.daily_loss_pct:
        return False, "daily_loss_circuit"
    if state.weekly_loss_pct >= budget.weekly_loss_pct:
        return False, "weekly_loss_circuit"
    if state.consecutive_losses >= budget.consecutive_losses_halt:
        return False, "consecutive_losses_circuit"
    return True, None


def decide(
    *,
    token: str,
    report: SafetyReport,
    funding_confirmed: bool,
    mcap_usd: float | None,
    mcap_min_usd: float,
    mcap_max_usd: float,
    budget: RiskBudget,
    state: PortfolioState,
    mode: str = "shadow",
    now: float | None = None,
    ttl_seconds: float = 45.0,
) -> Decision:
    if mode not in {"shadow", "live"}:
        raise ValueError("mode must be shadow or live")
    created = time.time() if now is None else float(now)
    reasons: list[str] = []
    if report.mode != "safe":
        # A learning-mode report only blocks on honeypot_sim, so almost every unknown field
        # still "passes". Trading off that verdict would be fail-open, so the decision layer
        # refuses to buy no matter what the report says.
        reasons.append("safety_mode_not_safe")
    if report.verdict != "pass":
        reasons.append("safety_reject")
    if not funding_confirmed:
        reasons.append("funding_not_confirmed")
    if mcap_usd is None:
        reasons.append("mcap_unknown")
    elif not mcap_min_usd <= mcap_usd <= mcap_max_usd:
        reasons.append("mcap_out_of_band")
    allowed, risk_reason = risk_allows(budget, state)
    if not allowed:
        reasons.append(risk_reason or "risk_block")
    action = "buy" if not reasons else ("watch" if "funding_not_confirmed" in reasons and report.verdict == "pass" else "reject")
    size = 0.0 if action != "buy" else float(budget.per_trade_pct)
    return Decision(
        token=token,
        action=action,
        mode=mode,
        size_quote=size,
        reason_codes=tuple(reasons),
        safety_verdict=report.verdict,
        funding_confirmed=bool(funding_confirmed),
        expires_at=created + float(ttl_seconds),
        created_at=created,
    )

"""Live enablement gate. Shadow validation must pass before any real capital."""

from __future__ import annotations

from dataclasses import dataclass

from src.shadow.report import ShadowGateReport


@dataclass(frozen=True)
class LiveGateConfig:
    enabled: bool = False
    operator_confirmed: bool = False
    max_per_trade_pct: float = 1.0
    max_concurrent_positions: int = 3
    daily_loss_halt_pct: float = 0.10


@dataclass(frozen=True)
class LiveGateResult:
    allowed: bool
    reason_codes: tuple[str, ...]


def evaluate_live_gate(config: LiveGateConfig, shadow_report: ShadowGateReport) -> LiveGateResult:
    reasons = []
    if not config.enabled:
        reasons.append("live_disabled")
    if not config.operator_confirmed:
        reasons.append("operator_not_confirmed")
    if shadow_report.verdict != "pass":
        reasons.append("shadow_gate_not_passed")
    if not 0 < config.max_per_trade_pct <= 5:
        reasons.append("per_trade_cap_invalid")
    if config.max_concurrent_positions < 1:
        reasons.append("concurrency_cap_invalid")
    return LiveGateResult(not reasons, tuple(reasons))

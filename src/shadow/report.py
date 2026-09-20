"""Shadow validation gate metrics."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ShadowGateConfig:
    min_trades: int = 50
    min_net_expectancy: float = 0.0
    max_top_token_share: float = 0.50
    max_half_delta: float = 0.05
    max_latency_p95_seconds: float = 2.0


@dataclass(frozen=True)
class ShadowGateReport:
    trades: int
    net_expectancy: float
    win_rate: float
    top_token_share: float
    half_delta: float
    latency_p95_seconds: float
    verdict: str
    reason_codes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "trades": self.trades, "net_expectancy": self.net_expectancy, "win_rate": self.win_rate,
            "top_token_share": self.top_token_share, "half_delta": self.half_delta,
            "latency_p95_seconds": self.latency_p95_seconds, "verdict": self.verdict,
            "reason_codes": list(self.reason_codes),
        }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return float(ordered[index])


def build_gate_report(records: list[dict], config: ShadowGateConfig | None = None) -> ShadowGateReport:
    """records: [{pnl_quote, quote_amount, token, latency_seconds}] closed shadow trades."""
    cfg = config or ShadowGateConfig()
    trades = len(records)
    totals = [float(record.get("pnl_quote", 0.0)) for record in records]
    stakes = [abs(float(record.get("quote_amount", 0.0))) or 1.0 for record in records]
    net = sum(totals) / sum(stakes) if stakes else 0.0
    wins = sum(1 for value in totals if value > 0)
    win_rate = wins / trades if trades else 0.0
    by_token: dict[str, float] = {}
    for record, pnl in zip(records, totals):
        by_token[record.get("token", "unknown")] = by_token.get(record.get("token", "unknown"), 0.0) + pnl
    positive_total = sum(value for value in by_token.values() if value > 0)
    top_share = (max(by_token.values()) / positive_total) if positive_total > 0 else 1.0
    half = trades // 2
    first = sum(totals[:half]) / max(1, sum(stakes[:half]))
    second = sum(totals[half:]) / max(1, sum(stakes[half:]))
    half_delta = abs(first - second)
    latency_p95 = _percentile([float(record.get("latency_seconds", 0.0)) for record in records], 95)
    reasons = []
    if trades < cfg.min_trades:
        reasons.append("insufficient_trades")
    if net < cfg.min_net_expectancy:
        reasons.append("net_expectancy_below_gate")
    if top_share > cfg.max_top_token_share:
        reasons.append("top_token_dependency")
    if half_delta > cfg.max_half_delta:
        reasons.append("unstable_across_halves")
    if latency_p95 > cfg.max_latency_p95_seconds:
        reasons.append("latency_p95_too_high")
    return ShadowGateReport(trades, net, win_rate, top_share, half_delta, latency_p95,
                            "pass" if not reasons else "reject", tuple(reasons))


def records_from_store(store, chain: str | None = None) -> list[dict]:
    """Reconstruct gate inputs from shadow_close records written by ShadowTracker."""
    records = []
    for row in store.rows("shadow_close", limit=10_000, chain=chain):
        payload = row.get("payload") or {}
        records.append({
            "chain": payload.get("chain"),
            "token": payload.get("token") or row.get("entity"),
            "pnl_quote": float(payload.get("pnl_quote", 0.0)),
            "quote_amount": float(payload.get("quote_amount", 0.0)),
            "latency_seconds": float(payload.get("latency_seconds", 0.0)),
        })
    return records

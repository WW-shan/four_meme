"""Scanner thresholds. Values are hypotheses from public research, not proof."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path


@dataclass
class FilterThresholds:
    liquidity_min_usd: float = 8_000.0
    dev_holding_max_pct: float = 1.0
    buy_tax_max_pct: float = 5.0
    sell_tax_max_pct: float = 5.0
    tax_spread_max_pp: float = 2.0
    mcap_min_usd: float = 10_000.0
    mcap_max_usd: float = 500_000.0
    mcap_preferred_min_usd: float = 20_000.0
    mcap_preferred_max_usd: float = 80_000.0
    top1_max_pct: float = 12.0
    top10_max_pct: float = 30.0
    non_lp_max_pct: float = 8.0
    bundle_current_held_max_pct: float = 20.0
    early_sniper_max: int = 10
    dev_rug_rate_max: float = 0.30
    recent_trades_window_seconds: int = 300


@dataclass
class ScannerConfig:
    mode: str = "learning"  # learning | safe
    deep_audit_per_round: int = 6
    cache_ttl_seconds: int = 60
    thresholds: FilterThresholds = field(default_factory=FilterThresholds)

    def __post_init__(self):
        if self.mode not in {"learning", "safe"}:
            raise ValueError("mode must be learning or safe")
        if self.deep_audit_per_round < 1:
            raise ValueError("deep_audit_per_round must be positive")
        if self.cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must be non-negative")
        if self.thresholds.liquidity_min_usd < 0:
            raise ValueError("liquidity_min_usd must be non-negative")

    def to_dict(self) -> dict:
        return {"mode": self.mode, "deep_audit_per_round": self.deep_audit_per_round,
                "cache_ttl_seconds": self.cache_ttl_seconds, "thresholds": asdict(self.thresholds)}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "ScannerConfig":
        if path is None:
            return cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        thresholds = FilterThresholds(**data.pop("thresholds", {}))
        return cls(thresholds=thresholds, **data)

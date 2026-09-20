"""Scanner pipeline: radar events -> snapshot -> safety report -> decision -> shadow."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Callable, Mapping

from config.scanner_config import ScannerConfig
from src.decision.engine import Decision, PortfolioState, RiskBudget, decide
from src.radar.collector import RadarCollector
from src.radar.store import ScannerStore
from src.safety.fetchers import SnapshotFetcher
from src.safety.orchestrator import SafetyReport, build_report
from src.safety.snapshot import build_snapshot


# Fallback chain ids, used only when config/chains.json cannot be read. Provider endpoints
# are keyed by chain id, so guessing one would score a token with another chain's data.
DEFAULT_CHAIN_IDS = {
    "bsc": 56,
    "eth": 1,
    "base": 8453,
    "arbitrum": 42161,
    "robinhood": 4663,
    "arc": 5042,
    "stable": 988,
}


def load_chain_ids(path: str | Path | None = None) -> dict[str, int]:
    """Map chain name -> chain id from config/chains.json, falling back to DEFAULT_CHAIN_IDS."""
    ids = dict(DEFAULT_CHAIN_IDS)
    target = Path(path) if path is not None else Path(__file__).resolve().parents[2] / "config" / "chains.json"
    try:
        payload = json.loads(Path(target).read_text(encoding="utf-8"))
    except Exception:
        return ids
    for item in payload.get("chains") or []:
        name, chain_id = item.get("chain"), item.get("chain_id")
        if not name:
            continue
        try:
            value = int(chain_id)
        except (TypeError, ValueError):
            continue
        if value > 0:
            ids[str(name)] = value
    return ids


@dataclass
class ScannerPipeline:
    store: ScannerStore
    config: ScannerConfig
    fetcher: SnapshotFetcher | None = None
    onchain_reader: Callable[[str], Mapping] | None = None
    clock: Callable[[], float] = time.time
    chain: str = "bsc"
    chain_id: int | None = None

    def __post_init__(self):
        if self.chain_id is None:
            # An unknown chain stays None so every chain-keyed fetcher fails closed instead
            # of querying BSC for a token that lives somewhere else.
            self.chain_id = load_chain_ids().get(self.chain)
        self.radar = RadarCollector(self.store, chain=self.chain)

    async def handle_event(self, event_name: str, event_data: dict) -> bool:
        return await self.radar.handle_event(event_name, event_data)

    def snapshot(self, token: str, *, override: Mapping | None = None) -> dict:
        fetched = (self.fetcher.fetch_all(token, chain_id=self.chain_id, chain=self.chain)
                   if self.fetcher is not None else {})
        onchain = self.onchain_reader(token) if self.onchain_reader is not None else {}
        snapshot = build_snapshot(token, fetched, onchain=onchain)
        snapshot["chain"] = self.chain
        if override:
            # Explicit None is meaningful: renounced authority / unknown tax.
            snapshot.update(dict(override))
        self.store.append("snapshot", token, snapshot, self.clock(), self.clock())
        return snapshot

    def audit(self, token: str, *, override: Mapping | None = None) -> SafetyReport:
        snapshot = self.snapshot(token, override=override)
        report = build_report(token, snapshot, self.config.thresholds, mode=self.config.mode, now=self.clock())
        payload = report.to_dict()
        payload["chain"] = self.chain
        self.store.append("safety_report", token, payload, report.created_at, self.clock())
        return report

    def decide(self, token: str, *, report: SafetyReport, funding_confirmed: bool,
               mcap_usd: float | None, budget: RiskBudget | None = None,
               state: PortfolioState | None = None, mode: str = "shadow") -> Decision:
        budget = budget or RiskBudget()
        state = state or PortfolioState()
        decision = decide(
            token=token, report=report, funding_confirmed=funding_confirmed, mcap_usd=mcap_usd,
            mcap_min_usd=self.config.thresholds.mcap_min_usd,
            mcap_max_usd=self.config.thresholds.mcap_max_usd,
            budget=budget, state=state, mode=mode, now=self.clock(),
        )
        payload = decision.to_dict()
        payload["chain"] = self.chain
        self.store.append("decision", token, payload, decision.created_at, self.clock())
        return decision

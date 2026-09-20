"""Scanner pipeline: radar events -> snapshot -> safety report -> decision -> shadow."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Mapping

from config.scanner_config import ScannerConfig
from src.decision.engine import Decision, PortfolioState, RiskBudget, decide
from src.radar.collector import RadarCollector
from src.radar.store import ScannerStore
from src.safety.fetchers import SnapshotFetcher
from src.safety.orchestrator import SafetyReport, build_report
from src.safety.snapshot import build_snapshot


@dataclass
class ScannerPipeline:
    store: ScannerStore
    config: ScannerConfig
    fetcher: SnapshotFetcher | None = None
    onchain_reader: Callable[[str], Mapping] | None = None
    clock: Callable[[], float] = time.time

    def __post_init__(self):
        self.radar = RadarCollector(self.store)

    async def handle_event(self, event_name: str, event_data: dict) -> bool:
        return await self.radar.handle_event(event_name, event_data)

    def snapshot(self, token: str, *, override: Mapping | None = None) -> dict:
        fetched = self.fetcher.fetch_all(token) if self.fetcher is not None else {}
        onchain = self.onchain_reader(token) if self.onchain_reader is not None else {}
        snapshot = build_snapshot(token, fetched, onchain=onchain)
        if override:
            # Explicit None is meaningful: renounced authority / unknown tax.
            snapshot.update(dict(override))
        self.store.append("snapshot", token, snapshot, self.clock(), self.clock())
        return snapshot

    def audit(self, token: str, *, override: Mapping | None = None) -> SafetyReport:
        snapshot = self.snapshot(token, override=override)
        report = build_report(token, snapshot, self.config.thresholds, mode=self.config.mode, now=self.clock())
        self.store.append("safety_report", token, report.to_dict(), report.created_at, self.clock())
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
        self.store.append("decision", token, decision.to_dict(), decision.created_at, self.clock())
        return decision

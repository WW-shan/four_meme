"""Run filters and produce a fail-closed safety report."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time
from typing import Any, Mapping

from config.scanner_config import FilterThresholds
from src.safety.filters import (
    CRITICAL_FILTERS,
    FILTER_WEIGHTS,
    LEARNING_CRITICAL,
    FilterResult,
    evaluate_filters,
)


@dataclass(frozen=True)
class SafetyReport:
    token: str
    verdict: str  # pass | reject
    mode: str
    score: float
    max_score: float
    results: tuple[FilterResult, ...]
    created_at: float
    reason_codes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["results"] = [result.to_dict() for result in self.results]
        return payload


def build_report(token: str, snapshot: Mapping[str, Any], thresholds: FilterThresholds,
                 mode: str = "safe", now: float | None = None) -> SafetyReport:
    if mode not in {"safe", "learning"}:
        raise ValueError("mode must be safe or learning")
    results = evaluate_filters(snapshot, thresholds, mode)
    critical = LEARNING_CRITICAL if mode == "learning" else CRITICAL_FILTERS
    blocking = [r for r in results if r.filter_id in critical and r.status in {"fail", "error"}]
    score = sum(FILTER_WEIGHTS.get(r.filter_id, 1) for r in results if r.status == "pass")
    max_score = sum(FILTER_WEIGHTS.get(r.filter_id, 1) for r in results)
    reasons = tuple(f"{r.filter_id}:{r.status}" for r in blocking)
    return SafetyReport(
        token=token,
        verdict="reject" if blocking else "pass",
        mode=mode,
        score=float(score),
        max_score=float(max_score),
        results=tuple(results),
        created_at=time.time() if now is None else float(now),
        reason_codes=reasons,
    )

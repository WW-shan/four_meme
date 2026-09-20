"""Per-wallet scout scoring with explicit sample-size confidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

MIN_SAMPLE = 30


@dataclass(frozen=True)
class FirstTouchEvent:
    wallet: str
    token: str
    chain_time: float
    followers_60m: int = 0
    followers_4h: int = 0
    winrate_7d: float | None = None


@dataclass(frozen=True)
class ScoutScore:
    wallet: str
    n_first_touches: int
    swarm_rate: float
    tier: str
    sufficient_sample: bool
    stability_delta: float | None

    @property
    def confirmed(self) -> bool:
        return self.sufficient_sample and self.tier in {"S", "A"}


def _swarm(event: FirstTouchEvent) -> bool:
    return int(event.followers_60m or 0) >= 3


def compute_stability(events: list[FirstTouchEvent]) -> float | None:
    if len(events) < MIN_SAMPLE * 2:
        return None
    ordered = sorted(events, key=lambda event: event.chain_time)
    half = len(ordered) // 2
    first, second = ordered[:half], ordered[half:]
    rate_first = sum(_swarm(e) for e in first) / len(first)
    rate_second = sum(_swarm(e) for e in second) / len(second)
    return abs(rate_first - rate_second)


def compute_scout_score(wallet: str, events: Iterable[FirstTouchEvent]) -> ScoutScore:
    samples = [event for event in events if event.wallet == wallet]
    n = len(samples)
    rate = (sum(_swarm(event) for event in samples) / n) if n else 0.0
    if n < MIN_SAMPLE:
        tier = "insufficient"
    elif rate >= 0.40:
        tier = "S"
    elif rate >= 0.30:
        tier = "A"
    elif rate >= 0.20:
        tier = "B"
    else:
        tier = "C"
    return ScoutScore(wallet=wallet, n_first_touches=n, swarm_rate=rate, tier=tier,
                      sufficient_sample=n >= MIN_SAMPLE, stability_delta=compute_stability(samples))


def is_funding_confirmation(score: ScoutScore, event: FirstTouchEvent) -> bool:
    """Research rule: a confirmed scout's first touch is the funding signal."""
    return score.confirmed and event.wallet == score.wallet and _swarm(event)

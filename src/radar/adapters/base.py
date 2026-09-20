"""Chain adapter interface and launch records."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Mapping


@dataclass(frozen=True)
class LaunchEvent:
    chain: str
    platform: str
    token: str
    pool: str | None
    creator: str | None
    quote_asset: str | None
    chain_time: float
    received_at: float
    source_event: str
    evidence: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.chain or not self.token:
            raise ValueError("chain and token are required")
        if self.chain_time < 0:
            raise ValueError("chain_time must be non-negative")

    def to_dict(self) -> dict:
        return {
            "chain": self.chain, "platform": self.platform, "token": self.token, "pool": self.pool,
            "creator": self.creator, "quote_asset": self.quote_asset,
            "chain_time": self.chain_time, "received_at": self.received_at,
            "source_event": self.source_event, "evidence": dict(self.evidence),
        }


class ChainAdapter:
    chain: str = ""
    family: str = ""
    enabled: bool = False

    async def discover(self) -> list[LaunchEvent]:
        raise NotImplementedError

    def healthcheck(self) -> dict:
        return {"chain": self.chain, "family": self.family, "enabled": self.enabled}


def event_from_dict(payload: Mapping[str, Any], *, chain: str, platform: str) -> LaunchEvent:
    return LaunchEvent(
        chain=chain,
        platform=platform,
        token=str(payload.get("token") or payload.get("mint") or ""),
        pool=payload.get("pool"),
        creator=payload.get("creator"),
        quote_asset=payload.get("quote_asset"),
        chain_time=float(payload.get("chain_time") or 0.0),
        received_at=float(payload.get("received_at") or time.time()),
        source_event=str(payload.get("source_event") or "unknown"),
        evidence=dict(payload.get("evidence") or {}),
    )

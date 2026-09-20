"""Solana launch normalization (adapter layer; live Geyser wiring is deployment-gated)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import time

from src.attention.identity import address


@dataclass(frozen=True)
class SolanaLaunch:
    chain: str
    mint: str
    creator: str
    quote_mint: str | None
    chain_time: float
    received_at: float
    signature: str
    source_event: str
    schema_version: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


def launch_from_solana_event(event: dict) -> SolanaLaunch | None:
    if not isinstance(event, dict):
        return None
    mint = event.get("mint")
    if not mint:
        return None
    try:
        mint = address("sol", mint)
    except ValueError:
        return None
    creator = event.get("creator")
    if creator:
        try:
            creator = address("sol", creator)
        except ValueError:
            creator = ""
    quote = event.get("quote_mint")
    try:
        quote = address("sol", quote) if quote else None
    except ValueError:
        quote = None
    try:
        chain_time = float(event.get("chain_time") or 0.0)
        received_at = float(event.get("received_at") or time.time())
    except (TypeError, ValueError):
        chain_time, received_at = 0.0, time.time()
    return SolanaLaunch("sol", mint, creator or "", quote, chain_time, received_at,
                        str(event.get("signature") or ""), str(event.get("source_event") or "unknown"))

"""Normalized radar events with chain time and local receive time separated."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import time
from typing import Any, Mapping

from src.data.fourmeme_quote import classify_quote


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


@dataclass(frozen=True)
class TokenLaunch:
    chain: str
    token: str
    creator: str
    quote_asset: str | None
    quote_symbol: str
    chain_time: float
    received_at: float
    block_number: int
    log_index: int
    transaction_hash: str
    source_event: str
    schema_version: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


def launch_from_event(event_name: str, event_data: Mapping[str, Any], *, chain: str = "bsc") -> TokenLaunch | None:
    """Build a launch record from a listener event, or None for unrelated events."""
    args = event_data.get("args") or {}
    if event_name == "TokenCreate":
        token = _text(args.get("token"))
        creator = _text(args.get("creator"))
        source = "TokenCreate"
    elif event_name == "LiquidityAdded":
        token = _text(args.get("base"))
        creator = _text(args.get("creator"))
        source = "LiquidityAdded"
    else:
        return None
    if not token:
        return None
    quote = args.get("quote")
    chain_time = event_data.get("timestamp")
    try:
        chain_time = float(chain_time)
    except (TypeError, ValueError):
        chain_time = 0.0
    received = event_data.get("received_at")
    try:
        received = float(received)
    except (TypeError, ValueError):
        received = time.time()
    return TokenLaunch(
        chain=chain,
        token=token,
        creator=creator,
        quote_asset=(str(quote) if quote else None),
        quote_symbol=(classify_quote(quote) if quote else "unknown"),
        chain_time=chain_time,
        received_at=received,
        block_number=int(event_data.get("blockNumber") or -1),
        log_index=int(event_data.get("logIndex") or -1),
        transaction_hash=_text(event_data.get("transactionHash")),
        source_event=source,
    )

"""Generic Uniswap/Pancake-style PairCreated adapter (verified factory required)."""

from __future__ import annotations

import time
from typing import Any, Mapping

from web3 import Web3

from src.radar.adapters.base import ChainAdapter, LaunchEvent

PAIR_CREATED_TOPIC = Web3.keccak(text="PairCreated(address,address,address,uint256)").hex().removeprefix("0x")


def _hex(value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value).removeprefix("0x").lower()


class EvmPairAdapter(ChainAdapter):
    family = "evm"

    def __init__(self, chain: str, platform: str, factory: str, quote_tokens: tuple[str, ...], enabled: bool = False):
        if not factory.startswith("0x") or len(factory) != 42:
            raise ValueError("a verified factory address is required")
        self.chain = chain
        self.platform = platform
        self.factory = factory.lower()
        self.quote_tokens = {token.lower() for token in quote_tokens}
        self.enabled = enabled

    def normalize_pair_created(self, log: Mapping[str, Any], *, received_at: float | None = None) -> LaunchEvent | None:
        topics = list(log.get("topics") or [])
        if len(topics) < 3 or _hex(topics[0]) != PAIR_CREATED_TOPIC:
            return None
        token0 = "0x" + _hex(topics[1])[-40:]
        token1 = "0x" + _hex(topics[2])[-40:]
        data = _hex(log.get("data") or "")
        pool = "0x" + data[24:64] if len(data) >= 64 else None
        candidates = [token for token in (token0, token1) if token not in self.quote_tokens]
        if len(candidates) != 1:
            return None
        return LaunchEvent(
            chain=self.chain,
            platform=self.platform,
            token=candidates[0],
            pool=pool,
            creator=None,
            quote_asset=token0 if candidates[0] == token1 else token1,
            chain_time=0.0,
            received_at=time.time() if received_at is None else float(received_at),
            source_event="PairCreated",
            evidence={"factory": self.factory, "token0": token0, "token1": token1},
        )

    async def discover(self) -> list[LaunchEvent]:
        return []

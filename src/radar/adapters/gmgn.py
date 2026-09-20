"""GMGN multi-chain discovery adapter (read-only; requires a personal API key)."""

from __future__ import annotations

import time
import uuid

from src.radar.adapters.base import ChainAdapter, LaunchEvent

SUPPORTED_CHAINS = ("sol", "bsc", "base", "eth", "arbitrum", "hyperevm", "robinhood", "arc", "stable")


class GmgnDiscoveryAdapter(ChainAdapter):
    family = "discovery"

    def __init__(self, chain: str, api_key: str, session=None, interval: str = "1h", limit: int = 50,
                 platforms: tuple[str, ...] = (), enabled: bool = False):
        if chain not in SUPPORTED_CHAINS:
            raise ValueError(f"unsupported GMGN chain: {chain}")
        if interval not in {"1m", "5m", "1h", "6h", "24h"}:
            raise ValueError("invalid interval")
        self.chain = chain
        self.family = "solana" if chain == "sol" else "evm"
        self.api_key = api_key
        self.session = session
        self.interval = interval
        self.limit = limit
        self.platforms = tuple(platforms)
        self.enabled = enabled

    def _params(self):
        param = {"chain": self.chain, "interval": self.interval, "limit": self.limit}
        if self.platforms:
            param["platforms"] = list(self.platforms)
        return {"timestamp": int(time.time()), "client_id": str(uuid.uuid4())}

    def fetch(self) -> list[dict]:
        if not self.api_key or self.session is None:
            return []
        response = self.session.post(
            "https://openapi.gmgn.ai/v1/market/hot_searches",
            params=self._params(),
            headers={"X-APIKEY": self.api_key, "User-Agent": "meme-scanner/1.0"},
            json={"params": [{"chain": self.chain, "interval": self.interval, "limit": self.limit}]},
            timeout=20,
        )
        if getattr(response, "status_code", None) != 200:
            return []
        payload = response.json() or {}
        blocks = payload.get("data") or []
        if not blocks:
            return []
        return blocks[0].get("tokens") or []

    async def discover(self) -> list[LaunchEvent]:
        events = []
        for token in self.fetch():
            address = str(token.get("address") or "")
            if not address:
                continue
            events.append(LaunchEvent(
                chain=self.chain,
                platform=str(token.get("launchpad_platform") or token.get("launchpad") or "unknown"),
                token=address,
                pool=None,
                creator=token.get("creator"),
                quote_asset=token.get("launch_quote_address"),
                chain_time=float(token.get("creation_timestamp") or token.get("open_timestamp") or 0.0),
                received_at=time.time(),
                source_event="gmgn_hot_search",
                evidence={"visiting_count": token.get("visiting_count"), "rank": token.get("rank"),
                          "smart_degen_count": token.get("smart_degen_count"),
                          "renowned_count": token.get("renowned_count")},
            ))
        return events

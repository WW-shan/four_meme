"""Chain configuration and adapter registry with fail-closed enablement."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Iterable

from src.radar.adapters.base import ChainAdapter

EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOLANA_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
FACT_STATUSES = {"verified", "unverified", "unknown"}
ADDRESS_PATTERNS = {"evm": EVM_ADDRESS, "solana": SOLANA_ADDRESS}


@dataclass
class LaunchpadSpec:
    name: str
    address: str | None = None
    fact_status: str = "unknown"
    enabled: bool = False
    family: str = "evm"
    source: str | None = None
    # GMGN platform slug this contract belongs to, so discovery filters and launch records can
    # be attributed back to a named platform instead of a bare address.
    platform: str | None = None

    def __post_init__(self):
        if self.fact_status not in FACT_STATUSES:
            raise ValueError(f"invalid fact_status: {self.fact_status}")
        if self.family not in ADDRESS_PATTERNS:
            raise ValueError(f"invalid launchpad family: {self.family}")
        if self.address is not None and not ADDRESS_PATTERNS[self.family].fullmatch(self.address):
            raise ValueError(f"invalid {self.family} launchpad address: {self.address}")
        if self.enabled and self.fact_status != "verified":
            raise ValueError("unverified launchpad cannot be enabled")
        if self.fact_status == "verified" and not self.source:
            raise ValueError("verified launchpad requires a source")


@dataclass
class ChainConfig:
    chain: str
    family: str
    chain_id: int | None = None
    rpc: str | None = None
    explorer: str | None = None
    native: str | None = None
    gmgn_supported: bool = False
    platforms: list[str] = field(default_factory=list)
    launchpads: list[LaunchpadSpec] = field(default_factory=list)
    enabled: bool = False

    def __post_init__(self):
        if self.family not in {"evm", "solana"}:
            raise ValueError("family must be evm or solana")
        if self.family == "evm" and self.chain_id is not None and self.chain_id <= 0:
            raise ValueError("chain_id must be positive")


def load_chains(path: str | Path) -> dict[str, ChainConfig]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    chains: dict[str, ChainConfig] = {}
    for item in data.get("chains", []):
        launchpads = [LaunchpadSpec(**spec) for spec in item.pop("launchpads", [])]
        config = ChainConfig(launchpads=launchpads, **item)
        chains[config.chain] = config
    return chains


class AdapterRegistry:
    def __init__(self, configs: dict[str, ChainConfig] | None = None):
        self.configs = configs or {}
        self._adapters: dict[str, ChainAdapter] = {}

    def register(self, adapter: ChainAdapter) -> None:
        config = self.configs.get(adapter.chain)
        if config is None:
            raise ValueError(f"no chain config for {adapter.chain}")
        adapter.enabled = config.enabled
        self._adapters[adapter.chain] = adapter

    def get(self, chain: str) -> ChainAdapter | None:
        return self._adapters.get(chain)

    def enabled_adapters(self) -> list[ChainAdapter]:
        return [adapter for adapter in self._adapters.values() if adapter.enabled]

    def health(self) -> list[dict]:
        return [adapter.healthcheck() for adapter in self._adapters.values()]

    async def discover_all(self) -> list:
        events = []
        for adapter in self.enabled_adapters():
            events.extend(await adapter.discover())
        return events

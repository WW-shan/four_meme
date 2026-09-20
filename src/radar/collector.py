"""Radar collector: persist launches, graduations and trade provenance."""

from __future__ import annotations

from src.radar.events import launch_from_event
from src.radar.store import ScannerStore


class RadarCollector:
    def __init__(self, store: ScannerStore, chain: str = "bsc"):
        self.store = store
        self.chain = chain
        self.launches = 0
        self.graduations = 0

    async def handle_event(self, event_name: str, event_data: dict) -> bool:
        launch = launch_from_event(event_name, event_data, chain=self.chain)
        if launch is None:
            return False
        entity = f"{launch.chain}:{launch.token}"
        kind = "launch" if launch.source_event == "TokenCreate" else "graduation"
        self.store.append(kind, entity, launch.to_dict(), launch.chain_time or launch.received_at, launch.received_at)
        if kind == "launch":
            self.launches += 1
        else:
            self.graduations += 1
        return True

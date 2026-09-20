"""Chain-qualified wallet registry with explicit labels."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time

from src.attention.identity import address

LABELS = frozenset({"dev", "deployer", "kol", "smart", "unknown"})


@dataclass
class WalletRecord:
    chain: str
    wallet: str
    label: str = "unknown"
    source: str = "manual"
    first_seen: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class WalletRegistry:
    def __init__(self):
        self._records: dict[tuple[str, str], WalletRecord] = {}

    def upsert(self, chain: str, wallet: str, *, label: str = "unknown",
               source: str = "manual", metadata: dict | None = None,
               first_seen: float | None = None) -> WalletRecord:
        if label not in LABELS:
            raise ValueError(f"unsupported wallet label: {label}")
        normalized = address(chain, wallet)
        key = (chain, normalized)
        existing = self._records.get(key)
        record = WalletRecord(
            chain=chain,
            wallet=normalized,
            label=label,
            source=source,
            first_seen=existing.first_seen if existing else (time.time() if first_seen is None else float(first_seen)),
            metadata={**(existing.metadata if existing else {}), **(metadata or {})},
        )
        self._records[key] = record
        return record

    def get(self, chain: str, wallet: str) -> WalletRecord | None:
        return self._records.get((chain, address(chain, wallet)))

    def by_label(self, label: str) -> list[WalletRecord]:
        return [record for record in self._records.values() if record.label == label]

    def __len__(self) -> int:
        return len(self._records)

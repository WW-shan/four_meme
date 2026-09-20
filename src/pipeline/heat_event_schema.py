"""Canonical, causal event records for heat-source research.

The schema keeps source time separate from local observation time.  This is
important for replay: a provider snapshot must never be treated as if it were
known at the provider's ``created_at`` time unless that timestamp is explicit.
The module is intentionally offline-only; it does not decide whether a trade
should be submitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1
ALLOWED_SOURCES = frozenset(
    {
        "bsc_fourmeme",
        "dexscreener",
        "geckoterminal",
        "gmgn",
        "x",
        "market_regime",
    }
)


def _finite_timestamp(value: Any, *, field_name: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a finite timestamp") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        number = parsed.timestamp()
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{field_name} must be a positive finite timestamp")
    return number


def _normalise_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalise_address(value: Any) -> str | None:
    text = _normalise_text(value)
    return text.lower() if text else None


def _normalise_hash(value: Any) -> str | None:
    text = _normalise_text(value)
    if not text:
        return None
    return text[2:].lower() if text.lower().startswith("0x") else text.lower()


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _payload_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _event_id(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HeatEvent:
    """One observed or reconstructed heat event.

    ``source_event_time`` is when the source says the event happened.  For a
    live source, ``observed_time`` is when this process received it.  Historical
    lifecycle reconstruction leaves ``observed_time`` empty and marks the
    event as reconstructed, so latency is never fabricated.
    """

    source: str
    event_type: str
    token: str | None = None
    wallet: str | None = None
    source_event_time: float | None = None
    observed_time: float | None = None
    latency_seconds: float | None = None
    evidence_url: str | None = None
    raw_payload_ref: str | None = None
    adapter_version: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    block_number: int | None = None
    log_index: int | None = None
    transaction_hash: str | None = None
    time_quality: str = "source_timestamp"
    reconstructed: bool = False

    def __post_init__(self) -> None:
        source = _normalise_text(self.source)
        event_type = _normalise_text(self.event_type)
        if source not in ALLOWED_SOURCES:
            raise ValueError(f"unsupported heat event source: {source!r}")
        if not event_type:
            raise ValueError("event_type must be non-empty")

        source_time = _finite_timestamp(self.source_event_time, field_name="source_event_time")
        observed_time = _finite_timestamp(self.observed_time, field_name="observed_time")
        if source_time is None and observed_time is None:
            raise ValueError("source_event_time or observed_time is required")
        if source_time is not None and observed_time is not None and observed_time < source_time:
            raise ValueError("observed_time cannot precede source_event_time")

        if not isinstance(self.payload, Mapping):
            raise ValueError("payload must be a mapping")
        if self.latency_seconds is not None:
            try:
                latency = float(self.latency_seconds)
            except (TypeError, ValueError) as exc:
                raise ValueError("latency_seconds must be finite and non-negative") from exc
            if not math.isfinite(latency) or latency < 0.0:
                raise ValueError("latency_seconds must be finite and non-negative")
            if source_time is not None and observed_time is not None and abs(latency - (observed_time - source_time)) > 1.0:
                raise ValueError("latency_seconds does not match source/observed timestamps")

        for name, value in (("block_number", self.block_number), ("log_index", self.log_index)):
            if value is None:
                continue
            try:
                integer = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be an integer") from exc
            if integer < 0:
                raise ValueError(f"{name} must be non-negative")

        object.__setattr__(self, "source", source)
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "token", _normalise_address(self.token))
        object.__setattr__(self, "wallet", _normalise_address(self.wallet))
        object.__setattr__(self, "transaction_hash", _normalise_hash(self.transaction_hash))
        object.__setattr__(self, "evidence_url", _normalise_text(self.evidence_url))
        object.__setattr__(self, "raw_payload_ref", _normalise_text(self.raw_payload_ref))
        object.__setattr__(self, "adapter_version", _normalise_text(self.adapter_version))
        object.__setattr__(self, "source_event_time", source_time)
        object.__setattr__(self, "observed_time", observed_time)
        if source_time is not None and observed_time is not None and self.latency_seconds is None:
            object.__setattr__(self, "latency_seconds", observed_time - source_time)
        if self.reconstructed and observed_time is not None:
            raise ValueError("reconstructed events cannot claim an observed_time")

    @property
    def payload_hash(self) -> str:
        return _payload_hash(self.payload)

    @property
    def dedupe_key(self) -> str:
        """Identity for repeated deliveries of the same source event."""
        identity = {
            "source": self.source,
            "event_type": self.event_type,
            "token": self.token,
            "wallet": self.wallet,
            "source_event_time": self.source_event_time,
            "block_number": self.block_number,
            "log_index": self.log_index,
            "transaction_hash": self.transaction_hash,
            "payload_hash": self.payload_hash,
        }
        return _event_id(identity)

    @property
    def event_id(self) -> str:
        return _event_id(self.to_dict(include_ids=False))

    def to_dict(self, *, include_ids: bool = True) -> dict[str, Any]:
        row: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "source": self.source,
            "event_type": self.event_type,
            "token": self.token,
            "wallet": self.wallet,
            "source_event_time": self.source_event_time,
            "observed_time": self.observed_time,
            "latency_seconds": self.latency_seconds,
            "evidence_url": self.evidence_url,
            "raw_payload_ref": self.raw_payload_ref,
            "adapter_version": self.adapter_version,
            "payload": dict(self.payload),
            "payload_hash": self.payload_hash,
            "block_number": self.block_number,
            "log_index": self.log_index,
            "transaction_hash": self.transaction_hash,
            "time_quality": self.time_quality,
            "reconstructed": self.reconstructed,
        }
        if include_ids:
            row["dedupe_key"] = self.dedupe_key
            row["event_id"] = self.event_id
        return row

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


def event_from_lifecycle_row(
    lifecycle: Mapping[str, Any],
    *,
    event_type: str,
    row: Mapping[str, Any] | None = None,
) -> HeatEvent:
    """Convert a chain lifecycle row without fabricating local observation time."""
    token = lifecycle.get("token_address") or lifecycle.get("token")
    row = row or {}
    source_time = row.get("timestamp", lifecycle.get("create_timestamp"))
    payload = {
        key: value
        for key, value in row.items()
        if key not in {"timestamp", "account", "block_number", "log_index", "transaction_hash"}
    }
    block_number = row.get("block_number", row.get("blockNumber"))
    if block_number is None and event_type == "token_create":
        block_number = lifecycle.get("create_block")
    return HeatEvent(
        source="bsc_fourmeme",
        event_type=event_type,
        token=token,
        wallet=row.get("account"),
        source_event_time=source_time,
        evidence_url=None,
        payload=payload,
        block_number=block_number,
        log_index=row.get("log_index", row.get("logIndex")),
        transaction_hash=row.get("transaction_hash", row.get("transactionHash")),
        time_quality="chain_block_timestamp",
        reconstructed=True,
    )


def lifecycle_to_heat_events(lifecycle: Mapping[str, Any]) -> list[HeatEvent]:
    """Export causal launch/trade/graduation events from one lifecycle."""
    events: list[HeatEvent] = []
    create_time = lifecycle.get("create_timestamp") or lifecycle.get("created_at")
    if create_time:
        events.append(
            event_from_lifecycle_row(
                lifecycle,
                event_type="token_create",
                row={
                    "timestamp": create_time,
                    "type": "token_create",
                    "creator": lifecycle.get("creator"),
                    "name": lifecycle.get("name"),
                    "symbol": lifecycle.get("symbol"),
                    "launch_fee": lifecycle.get("launch_fee"),
                    "total_supply": lifecycle.get("total_supply"),
                },
            )
        )
    for event_type, key in (("token_buy", "buys"), ("token_sell", "sells")):
        for row in lifecycle.get(key) or []:
            if isinstance(row, Mapping):
                events.append(event_from_lifecycle_row(lifecycle, event_type=event_type, row=row))
    if lifecycle.get("graduated") and lifecycle.get("graduate_time"):
        events.append(
            event_from_lifecycle_row(
                lifecycle,
                event_type="trade_stop",
                row={"timestamp": lifecycle.get("graduate_time"), "type": "trade_stop"},
            )
        )
    return sorted(
        events,
        key=lambda event: (
            float(event.source_event_time or 0.0),
            int(event.block_number if event.block_number is not None else -1),
            int(event.log_index if event.log_index is not None else -1),
            event.event_id,
        ),
    )


class HeatEventWriter:
    """Append-only JSONL writer with in-process deduplication."""

    def __init__(self, path: str | Path, *, load_existing: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = set()
        if load_existing and self.path.exists():
            self._load_existing_keys()

    def _load_existing_keys(self) -> None:
        """Resume safely after a reconnect or interrupted export."""
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    key = row.get("dedupe_key") if isinstance(row, Mapping) else None
                    if key:
                        self._seen.add(str(key))
        except OSError:
            # A concurrent rotation can make an old file briefly unavailable.
            # The next append still remains valid and the caller can retry.
            return

    def append(self, event: HeatEvent) -> bool:
        if event.dedupe_key in self._seen:
            return False
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.to_json() + "\n")
        self._seen.add(event.dedupe_key)
        return True

    def append_many(self, events: Iterable[HeatEvent]) -> int:
        pending: list[tuple[str, str]] = []
        for event in events:
            if event.dedupe_key in self._seen:
                continue
            pending.append((event.dedupe_key, event.to_json()))
        if not pending:
            return 0
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(serialized for _, serialized in pending) + "\n")
        self._seen.update(key for key, _ in pending)
        return len(pending)


__all__ = [
    "ALLOWED_SOURCES",
    "HeatEvent",
    "HeatEventWriter",
    "SCHEMA_VERSION",
    "event_from_lifecycle_row",
    "lifecycle_to_heat_events",
]

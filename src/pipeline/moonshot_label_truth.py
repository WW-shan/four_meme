from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


THRESHOLDS = (2, 5, 10, 20, 50, 100)

SOURCE_HINTS = {
    "bitquery": "bitquery_export",
    "codex": "codex_export",
    "coingecko": "coingecko_export",
    "geckoterminal": "coingecko_export",
    "cmc": "cmc_export",
    "coinmarketcap": "cmc_export",
}

BASE_FIELD_ALIASES = {
    "chain": ("chain", "network", "blockchain", "platform"),
    "token_address": (
        "token_address",
        "tokenAddress",
        "contract_address",
        "contractAddress",
        "token.address",
        "baseToken.address",
        "currency.address",
    ),
    "pair_address": ("pair_address", "pairAddress", "pair.address", "pool.address"),
    "launch_time": (
        "launch_time",
        "launchTime",
        "launchTimestamp",
        "createdAt",
        "created_at",
        "creationTime",
        "launch_date",
        "deployedAt",
    ),
    "first_observed_price": (
        "first_observed_price",
        "initialPriceUsd",
        "firstPriceUsd",
        "first_price_usd",
        "startPriceUsd",
        "price_usd_start",
    ),
    "max_observed_price": (
        "max_observed_price",
        "athPriceUsd",
        "maxPriceUsd",
        "all_time_high_price_usd",
        "highPriceUsd",
        "price_usd_max",
    ),
    "migration_time": (
        "migration_time",
        "migrationTimestamp",
        "migratedAt",
        "migrated_at",
        "graduatedAt",
    ),
    "evidence_url": (
        "evidence_url",
        "evidenceUrl",
        "sourceUrl",
        "source_url",
        "article_url",
        "url",
        "link",
    ),
    "source_fetched_at": (
        "source_fetched_at",
        "fetchedAt",
        "fetched_at",
        "exportedAt",
        "exported_at",
        "observed_at",
        "updatedAt",
    ),
}

SOURCE_PROFILE_ALIASES = {
    "bitquery_fourmeme": {},
    "codex_launchpad": {},
    "coingecko_fourmeme": {
        "token_address": (
            "data.attributes.address",
            "attributes.address",
        ),
        "pair_address": (
            "data.attributes.pool_address",
            "data.attributes.poolAddress",
            "attributes.pool_address",
            "attributes.poolAddress",
        ),
        "launch_time": (
            "data.attributes.created_at",
            "data.attributes.createdAt",
            "attributes.created_at",
            "attributes.createdAt",
        ),
        "first_observed_price": (
            "data.attributes.first_price_usd",
            "data.attributes.initial_price_usd",
            "attributes.first_price_usd",
            "attributes.initial_price_usd",
        ),
        "max_observed_price": (
            "data.attributes.ath_price_usd",
            "data.attributes.max_price_usd",
            "attributes.ath_price_usd",
            "attributes.max_price_usd",
        ),
        "evidence_url": (
            "data.attributes.coingecko_url",
            "data.links.self",
            "attributes.coingecko_url",
        ),
        "source_fetched_at": (
            "data.attributes.updated_at",
            "data.attributes.observed_at",
            "attributes.updated_at",
            "attributes.observed_at",
        ),
    },
}

CHAIN_ALIASES = {
    "bnb smart chain": "bsc",
    "binance smart chain": "bsc",
    "bsc": "bsc",
}


@dataclass
class LabelReject:
    token_address: str
    reason: str
    source: str = "local_lifecycle"
    details: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "token_address": self.token_address,
            "reason": self.reason,
            "source": self.source,
            "details": dict(self.details),
        }


@dataclass
class MoonshotLabelRow:
    chain: str
    token_address: str
    launch_time: object
    first_observed_price: float
    max_observed_price: float
    source: str
    source_fetched_at: object
    pair_address: Optional[str] = None
    migration_time: Optional[object] = None
    evidence_url: Optional[str] = None
    source_profile: Optional[str] = None
    provenance: List[Dict] = field(default_factory=list)
    threshold_times: Dict[str, object] = field(default_factory=dict)

    @property
    def max_multiple(self) -> float:
        if self.first_observed_price <= 0:
            return 0.0
        return float(self.max_observed_price) / float(self.first_observed_price)

    def to_dict(self) -> Dict:
        data = {
            "chain": self.chain,
            "token_address": self.token_address,
            "pair_address": self.pair_address,
            "launch_time": self.launch_time,
            "first_observed_price": float(self.first_observed_price),
            "max_observed_price": float(self.max_observed_price),
            "max_multiple": self.max_multiple,
            "migration_time": self.migration_time,
            "evidence_url": self.evidence_url,
            "source": self.source,
            "source_profile": self.source_profile,
            "source_fetched_at": self.source_fetched_at,
            "provenance": list(self.provenance),
        }
        for threshold in THRESHOLDS:
            hit_key = f"hit_{threshold}x"
            time_key = f"time_to_{threshold}x"
            data[hit_key] = bool(self.max_multiple >= float(threshold))
            data[time_key] = self.threshold_times.get(time_key)
        return data


def _normalize_address(value: object) -> str:
    return str(value or "").strip().lower()


def _normalize_chain(value: object) -> str:
    text = str(value or "bsc").strip().lower()
    return CHAIN_ALIASES.get(text, text or "bsc")


def _nested_value(row: Dict, key: str):
    current = row
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _first_present(row: Dict, keys: Sequence[str]):
    for key in keys:
        value = _nested_value(row, key) if "." in key else row.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _source_from_hint(source_hint: object) -> str:
    hint = str(source_hint or "").strip().lower()
    for key, source in SOURCE_HINTS.items():
        if key in hint:
            return source
    return "external_export"


def _canonical_source(raw_source: object, source_hint: object) -> str:
    source_text = str(raw_source or "").strip()
    hint_source = _source_from_hint(source_hint)
    if not source_text:
        return hint_source
    mapped = _source_from_hint(source_text)
    if mapped != "external_export":
        return mapped
    return source_text


def _profile_name(source_hint: object, raw_source: object = None) -> str:
    text = str(source_hint or raw_source or "external").strip().lower()
    return text or "external"


def _aliases_for_profile(profile_name: str) -> Dict[str, Tuple[str, ...]]:
    aliases = {key: tuple(value) for key, value in BASE_FIELD_ALIASES.items()}
    for field_name, extra_aliases in SOURCE_PROFILE_ALIASES.get(profile_name, {}).items():
        aliases[field_name] = tuple(extra_aliases) + aliases.get(field_name, ())
    return aliases


def external_source_profile(source_hint: object = None) -> Dict:
    profile_name = _profile_name(source_hint)
    aliases = _aliases_for_profile(profile_name)
    return {
        "source_profile": profile_name,
        "source": _source_from_hint(profile_name),
        "aliases": {key: list(value) for key, value in sorted(aliases.items())},
        "required": [
            "token_address",
            "launch_time",
            "first_observed_price",
            "max_observed_price",
            "evidence_url",
            "source_fetched_at",
        ],
    }


def _parse_time(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _positive_float(value: object) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _event_timestamp(event: Dict) -> Optional[int]:
    try:
        timestamp = int(event.get("timestamp", 0) or 0)
    except (TypeError, ValueError):
        return None
    return timestamp if timestamp > 0 else None


def _price_events(lifecycle: Dict) -> List[Dict]:
    events = []
    for side, rows in (("buy", lifecycle.get("buys", [])), ("sell", lifecycle.get("sells", []))):
        for row in rows or []:
            timestamp = _event_timestamp(row)
            price = _positive_float(row.get("price"))
            if timestamp is None or price is None:
                continue
            events.append({"timestamp": timestamp, "price": price, "side": side})
    return sorted(events, key=lambda event: int(event["timestamp"]))


def threshold_time(events: Sequence[Dict], first_price: float, threshold_multiple: float):
    target = float(first_price) * float(threshold_multiple)
    for event in sorted(events, key=lambda item: int(item.get("timestamp", 0) or 0)):
        price = _positive_float(event.get("price"))
        timestamp = _event_timestamp(event)
        if price is None or timestamp is None:
            continue
        if price >= target:
            return timestamp
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def extract_local_lifecycle_label(
    lifecycle: Dict,
    *,
    chain: str = "bsc",
    source: str = "local_lifecycle",
    source_fetched_at=None,
) -> Tuple[Optional[MoonshotLabelRow], Optional[LabelReject]]:
    token_address = _normalize_address(lifecycle.get("token_address"))
    launch_time = int(lifecycle.get("create_timestamp", lifecycle.get("created_at", 0)) or 0)
    events = [event for event in _price_events(lifecycle) if int(event["timestamp"]) >= launch_time]
    if not events:
        return None, LabelReject(token_address=token_address, reason="missing_first_price", source=source)

    first = events[0]
    first_price = float(first["price"])
    max_price = max(float(event["price"]) for event in events)
    row = MoonshotLabelRow(
        chain=str(chain or "bsc").lower(),
        token_address=token_address,
        launch_time=launch_time,
        first_observed_price=first_price,
        max_observed_price=max_price,
        source=str(source or "local_lifecycle"),
        source_fetched_at=source_fetched_at or _now_iso(),
        provenance=[{"source": str(source or "local_lifecycle"), "source_fetched_at": source_fetched_at or None}],
    )

    threshold_times = {}
    for threshold in THRESHOLDS:
        threshold_times[f"time_to_{threshold}x"] = threshold_time(events, first_price, threshold)
    row.threshold_times.update(threshold_times)
    row.provenance[0]["threshold_times"] = {
        f"time_to_{threshold}x": threshold_times[f"time_to_{threshold}x"] for threshold in THRESHOLDS
    }
    return row, None


def normalize_external_label(raw: Dict) -> Tuple[Optional[MoonshotLabelRow], Optional[LabelReject]]:
    source = str(raw.get("source") or "external_export")
    token_address = _normalize_address(raw.get("token_address"))
    evidence_url = str(raw.get("evidence_url") or "").strip()
    if not evidence_url:
        return None, LabelReject(token_address=token_address, reason="missing_evidence_url", source=source)

    launch_time = raw.get("launch_time")
    source_fetched_at = raw.get("source_fetched_at")
    launch_dt = _parse_time(launch_time)
    fetched_dt = _parse_time(source_fetched_at)
    if launch_dt is None or fetched_dt is None or fetched_dt < launch_dt:
        return None, LabelReject(
            token_address=token_address,
            reason="invalid_source_timestamp",
            source=source,
            details={"launch_time": launch_time, "source_fetched_at": source_fetched_at},
        )

    first_price = _positive_float(raw.get("first_observed_price"))
    max_price = _positive_float(raw.get("max_observed_price"))
    if first_price is None:
        return None, LabelReject(token_address=token_address, reason="missing_first_price", source=source)
    if max_price is None:
        return None, LabelReject(token_address=token_address, reason="missing_max_price", source=source)

    row = MoonshotLabelRow(
        chain=str(raw.get("chain") or "bsc").strip().lower(),
        token_address=token_address,
        pair_address=_normalize_address(raw.get("pair_address")) or None,
        launch_time=launch_time,
        first_observed_price=first_price,
        max_observed_price=max_price,
        migration_time=raw.get("migration_time"),
        evidence_url=evidence_url,
        source=source,
        source_profile=raw.get("source_profile"),
        source_fetched_at=source_fetched_at,
        provenance=[
            {
                "source": source,
                "source_profile": raw.get("source_profile"),
                "source_fetched_at": source_fetched_at,
                "evidence_url": evidence_url,
                "max_multiple": max_price / first_price,
            }
        ],
    )
    return row, None


def normalize_external_label_export(
    raw: Dict,
    *,
    source_hint: object = None,
) -> Tuple[Optional[MoonshotLabelRow], Optional[LabelReject]]:
    source_profile = _profile_name(source_hint, raw.get("source_format") or raw.get("source"))
    aliases = _aliases_for_profile(source_profile)
    source_format = str(source_hint or raw.get("source_format") or raw.get("source") or "external").strip().lower()
    canonical = {
        "chain": _normalize_chain(_first_present(raw, aliases["chain"])),
        "token_address": _first_present(raw, aliases["token_address"]),
        "pair_address": _first_present(raw, aliases["pair_address"]),
        "launch_time": _first_present(raw, aliases["launch_time"]),
        "first_observed_price": _first_present(raw, aliases["first_observed_price"]),
        "max_observed_price": _first_present(raw, aliases["max_observed_price"]),
        "migration_time": _first_present(raw, aliases["migration_time"]),
        "evidence_url": _first_present(raw, aliases["evidence_url"]),
        "source": _canonical_source(raw.get("source"), source_hint),
        "source_profile": source_profile,
        "source_fetched_at": _first_present(raw, aliases["source_fetched_at"]),
    }
    row, reject = normalize_external_label(canonical)
    if row is not None:
        row.provenance[0]["source_format"] = source_format
        row.provenance[0]["raw_source"] = str(source_hint or raw.get("source") or "")
        row.provenance[0]["source_profile"] = source_profile
    if reject is not None:
        reject.details["source_format"] = source_format
        reject.details["source_profile"] = source_profile
    return row, reject


def _load_export_path(path: Path) -> List[Dict]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows
    if suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(row) for row in payload]
    if isinstance(payload, dict):
        if isinstance(payload.get("rows"), list):
            return [dict(row) for row in payload["rows"]]
        if isinstance(payload.get("labels"), list):
            return [dict(row) for row in payload["labels"]]
        return [payload]
    return []


def load_external_label_exports(paths: Iterable[object]) -> Tuple[List[MoonshotLabelRow], List[LabelReject]]:
    rows: List[MoonshotLabelRow] = []
    rejects: List[LabelReject] = []
    for path_value in paths or []:
        path = Path(path_value)
        for raw in _load_export_path(path):
            row, reject = normalize_external_label_export(raw, source_hint=path.stem)
            if row is not None:
                rows.append(row)
            if reject is not None:
                rejects.append(reject)
    return rows, rejects


def _merge_key(row: MoonshotLabelRow) -> Tuple[str, str]:
    return (str(row.chain).lower(), _normalize_address(row.token_address))


def _better_label_row(existing: MoonshotLabelRow, incoming: MoonshotLabelRow) -> MoonshotLabelRow:
    incoming_has_evidence = bool(incoming.evidence_url)
    existing_has_evidence = bool(existing.evidence_url)
    if incoming_has_evidence and not existing_has_evidence:
        return incoming
    if existing_has_evidence and not incoming_has_evidence:
        return existing
    if incoming.max_multiple > existing.max_multiple:
        return incoming
    return existing


def merge_label_rows(
    local_rows: Iterable[MoonshotLabelRow],
    external_rows: Iterable[MoonshotLabelRow],
) -> Tuple[List[MoonshotLabelRow], List[Dict]]:
    merged: Dict[Tuple[str, str], MoonshotLabelRow] = {}
    source_groups: Dict[Tuple[str, str], List[MoonshotLabelRow]] = {}
    warnings: List[Dict] = []
    for row in list(local_rows or []) + list(external_rows or []):
        if row is None:
            continue
        key = _merge_key(row)
        source_groups.setdefault(key, []).append(row)
        if key not in merged:
            merged[key] = row
            continue
        merged[key] = _better_label_row(merged[key], row)

    for key, rows in source_groups.items():
        selected = merged[key]
        selected.provenance = []
        multiples = []
        for row in rows:
            selected.provenance.extend(list(row.provenance or [{
                "source": row.source,
                "source_fetched_at": row.source_fetched_at,
                "evidence_url": row.evidence_url,
                "max_multiple": row.max_multiple,
            }]))
            multiples.append(float(row.max_multiple))
        if len(multiples) >= 2:
            low = min(multiples)
            high = max(multiples)
            if low > 0 and ((high - low) / low) > 0.20:
                warnings.append({
                    "reason": "label_source_disagreement",
                    "chain": key[0],
                    "token_address": key[1],
                    "min_max_multiple": low,
                    "max_max_multiple": high,
                })

    return sorted(merged.values(), key=lambda row: (str(row.chain), str(row.token_address))), warnings


def _row_to_dict(row: MoonshotLabelRow) -> Dict:
    return row.to_dict()


def label_report(rows: Iterable[MoonshotLabelRow], rejects: Iterable[LabelReject], warnings: Optional[List[Dict]] = None) -> Dict:
    row_list = [row for row in rows if row is not None]
    reject_list = [reject for reject in rejects if reject is not None]
    row_dicts = [_row_to_dict(row) for row in row_list]
    threshold_counts = {
        f">={threshold}x": sum(1 for row in row_dicts if row.get(f"hit_{threshold}x"))
        for threshold in THRESHOLDS
    }
    sources = sorted({str(row.get("source", "")) for row in row_dicts if row.get("source")})
    source_counts = {
        source: sum(1 for row in row_dicts if row.get("source") == source)
        for source in sources
    }
    reject_reasons = sorted({str(reject.reason) for reject in reject_list if reject.reason})
    reject_reason_counts = {
        reason: sum(1 for reject in reject_list if reject.reason == reason)
        for reason in reject_reasons
    }
    return {
        "summary": {
            "accepted_count": len(row_dicts),
            "reject_count": len(reject_list),
            "source_count": len(sources),
        },
        "threshold_counts": threshold_counts,
        "source_counts": source_counts,
        "reject_reason_counts": reject_reason_counts,
        "rows": row_dicts,
        "rejects": [reject.to_dict() for reject in reject_list],
        "warnings": list(warnings or []),
        "provenance_sources": sources,
    }

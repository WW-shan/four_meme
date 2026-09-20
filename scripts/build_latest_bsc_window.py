#!/usr/bin/env python3
"""Build a deduplicated, creation-time-bounded BSC lifecycle window."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Dict, Iterable, Optional, Tuple


LIFECYCLE_PREFIXES = ("lifecycle_", "lifecycle_incremental_")


def _parse_timestamp(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            return None
        return timestamp if timestamp > 0 else None

    text = str(value).strip()
    if not text:
        return None
    try:
        timestamp = int(float(text))
        return timestamp if timestamp > 0 else None
    except ValueError:
        pass

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _lifecycle_create_timestamp(row: Dict) -> Optional[int]:
    for key in ("create_timestamp", "created_at", "launch_time", "launchTime"):
        timestamp = _parse_timestamp(row.get(key))
        if timestamp is not None:
            return timestamp
    return None


def _activity_count(row: Dict) -> int:
    return len(row.get("buys") or []) + len(row.get("sells") or [])


def _last_update(row: Dict) -> int:
    timestamp = _parse_timestamp(row.get("last_update")) or 0
    for event in (row.get("buys") or []) + (row.get("sells") or []):
        timestamp = max(timestamp, _parse_timestamp(event.get("timestamp")) or 0)
    return timestamp


def _should_replace(existing: Dict, incoming: Dict) -> bool:
    existing_key = (_activity_count(existing), _last_update(existing))
    incoming_key = (_activity_count(incoming), _last_update(incoming))
    return incoming_key >= existing_key


def _event_sort_key(row: object, index: int) -> tuple[int, float, int]:
    """Sort valid chain events by timestamp while keeping malformed items last."""
    if isinstance(row, dict):
        timestamp = _parse_timestamp(row.get("timestamp"))
        if timestamp is not None:
            return (0, float(timestamp), index)
    return (1, float("inf"), index)


def _normalize_lifecycle_row(row: Dict) -> Dict:
    """Canonicalize event order and repair stale chain ``last_update`` values.

    Incremental snapshots can arrive after a backfill batch and therefore carry
    events in append order rather than chain order.  Replay code assumes a
    chronological path, so the latest-window artifact normalizes each selected
    row without changing event payloads.
    """
    normalized = dict(row)
    for key in ("buys", "sells", "price_history"):
        values = list(normalized.get(key) or [])
        normalized[key] = [
            value for _index, value in sorted(
                enumerate(values), key=lambda item: _event_sort_key(item[1], item[0])
            )
        ]
    return _recompute_lifecycle_aggregates(normalized)


def _event_identity(event: object) -> str:
    """Build a stable identity for deduplicating repeated snapshot events."""
    if not isinstance(event, dict):
        return ""
    fields = {
        "timestamp": _parse_timestamp(event.get("timestamp")),
        "account": str(event.get("account") or "").lower(),
        "token_amount": event.get("token_amount"),
        "bnb_amount": event.get("bnb_amount"),
        "price": event.get("price"),
        "type": str(event.get("type") or ""),
        "block_number": event.get("block_number", event.get("blockNumber")),
        "log_index": event.get("log_index", event.get("logIndex")),
        "transaction_hash": str(
            event.get("transaction_hash", event.get("transactionHash")) or ""
        ).lower(),
    }
    return json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _merge_event_lists(existing: object, incoming: object) -> list[Dict]:
    """Union repeated lifecycle snapshots without dropping disjoint fragments."""
    merged: Dict[str, Dict] = {}
    for value in list(existing or []) + list(incoming or []):
        if not isinstance(value, dict):
            continue
        identity = _event_identity(value)
        if identity and identity not in merged:
            merged[identity] = dict(value)
    values = list(merged.values())
    values.sort(key=lambda value: _event_sort_key(value, 0))
    return values


def _number(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if parsed == parsed and abs(parsed) != float("inf") else float(default)


def _recompute_lifecycle_aggregates(row: Dict) -> Dict:
    """Recompute fields derived from the merged chain event sequences."""
    buys = [item for item in row.get("buys") or [] if isinstance(item, dict)]
    sells = [item for item in row.get("sells") or [] if isinstance(item, dict)]
    buys.sort(key=lambda value: _event_sort_key(value, 0))
    sells.sort(key=lambda value: _event_sort_key(value, 0))
    row["buys"] = buys
    row["sells"] = sells
    row["price_history"] = _merge_event_lists(row.get("price_history"), [])
    row["total_buy_volume_bnb"] = sum(_number(item.get("bnb_amount")) for item in buys)
    row["total_sell_volume_bnb"] = sum(_number(item.get("bnb_amount")) for item in sells)
    row["total_buy_count"] = len(buys)
    row["total_sell_count"] = len(sells)
    row["unique_buyers"] = sorted(
        {str(item.get("account") or "") for item in buys if str(item.get("account") or "")}
    )
    row["unique_sellers"] = sorted(
        {str(item.get("account") or "") for item in sells if str(item.get("account") or "")}
    )
    trades = sorted(buys + sells, key=lambda value: _event_sort_key(value, 0))
    prices = [_number(item.get("price")) for item in trades if _number(item.get("price")) > 0.0]
    if prices:
        row["price_current"] = prices[-1]
        row["price_first"] = _number(buys[0].get("price")) if buys else prices[0]
        row["price_max"] = max(prices)
        row["price_min"] = min(prices)
    else:
        row["price_current"] = 0.0
        row["price_first"] = 0.0
        row["price_max"] = 0.0
        row["price_min"] = 0.0
    timestamps = [
        timestamp
        for item in trades + list(row.get("price_history") or [])
        for timestamp in [_parse_timestamp(item.get("timestamp"))]
        if timestamp is not None
    ]
    create_timestamp = _parse_timestamp(row.get("create_timestamp"))
    if create_timestamp is not None:
        timestamps.append(create_timestamp)
    previous_update = _parse_timestamp(row.get("last_update"))
    if previous_update is not None:
        timestamps.append(previous_update)
    if timestamps:
        row["last_update"] = max(timestamps)
    windows = {
        "volume_1min": 60,
        "volume_5min": 300,
        "volume_15min": 900,
        "volume_30min": 1800,
        "volume_1h": 3600,
    }
    anchor = _parse_timestamp(row.get("last_update")) or create_timestamp or 0
    for field, seconds in windows.items():
        row[field] = sum(
            _number(item.get("bnb_amount"))
            for item in buys
            if (_parse_timestamp(item.get("timestamp")) or 0) >= anchor - seconds
        )
    return row


def _merge_lifecycle_rows(existing: Dict, incoming: Dict) -> tuple[Dict, bool]:
    """Merge cumulative snapshots and reactivation fragments for one token."""
    existing = _normalize_lifecycle_row(existing)
    incoming = _normalize_lifecycle_row(incoming)
    old_event_count = _activity_count(existing)
    incoming_event_count = _activity_count(incoming)
    preferred = incoming if _should_replace(existing, incoming) else existing
    merged = dict(preferred)
    merged["buys"] = _merge_event_lists(existing.get("buys"), incoming.get("buys"))
    merged["sells"] = _merge_event_lists(existing.get("sells"), incoming.get("sells"))
    merged["price_history"] = _merge_event_lists(
        existing.get("price_history"), incoming.get("price_history")
    )
    create_values = [
        value
        for value in (
            _parse_timestamp(existing.get("create_timestamp")),
            _parse_timestamp(incoming.get("create_timestamp")),
        )
        if value is not None
    ]
    if create_values:
        merged["create_timestamp"] = min(create_values)
    merged["graduated"] = bool(existing.get("graduated") or incoming.get("graduated"))
    graduate_values = [
        value
        for value in (
            _parse_timestamp(existing.get("graduate_time")),
            _parse_timestamp(incoming.get("graduate_time")),
        )
        if value is not None
    ]
    if graduate_values:
        merged["graduate_time"] = max(graduate_values)
    merged = _recompute_lifecycle_aggregates(merged)
    merged_event_count = _activity_count(merged)
    fragmented = merged_event_count > max(old_event_count, incoming_event_count)
    return merged, fragmented


def lifecycle_paths(input_dir: Path, output_dir: Optional[Path] = None) -> list[Path]:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve() if output_dir is not None else None
    paths = []
    for path in sorted(input_dir.rglob("*.jsonl")):
        if output_dir is not None and (path == output_dir or output_dir in path.parents):
            continue
        if not path.name.startswith(LIFECYCLE_PREFIXES):
            continue
        paths.append(path)
    return paths


def collect_latest_lifecycles(
    input_dir: Path,
    *,
    cutoff_timestamp: int,
    as_of_timestamp: int,
    output_dir: Optional[Path] = None,
) -> Tuple[Dict[str, Dict], Dict[str, int]]:
    selected: Dict[str, Dict] = {}
    stats = {
        "input_file_count": 0,
        "input_row_count": 0,
        "malformed_row_count": 0,
        "missing_create_timestamp_count": 0,
        "outside_window_row_count": 0,
        "duplicate_token_count": 0,
        "fragment_token_count": 0,
        "fragment_merge_count": 0,
        "merged_event_count": 0,
        "merged_price_point_count": 0,
    }
    fragment_tokens: set[str] = set()

    for path in lifecycle_paths(input_dir, output_dir):
        stats["input_file_count"] += 1
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                stats["input_row_count"] += 1
                try:
                    row = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError):
                    stats["malformed_row_count"] += 1
                    continue
                if not isinstance(row, dict):
                    stats["malformed_row_count"] += 1
                    continue

                token = str(row.get("token_address") or row.get("token") or "").strip().lower()
                create_timestamp = _lifecycle_create_timestamp(row)
                if not token or create_timestamp is None:
                    stats["missing_create_timestamp_count"] += 1
                    continue
                if create_timestamp < cutoff_timestamp or create_timestamp > as_of_timestamp:
                    stats["outside_window_row_count"] += 1
                    continue

                row = dict(row)
                row["token_address"] = token
                row["create_timestamp"] = create_timestamp
                existing = selected.get(token)
                if existing is not None:
                    stats["duplicate_token_count"] += 1
                    merged, fragmented = _merge_lifecycle_rows(existing, row)
                    if fragmented:
                        fragment_tokens.add(token)
                        stats["fragment_merge_count"] += 1
                        stats["merged_event_count"] += max(
                            0,
                            _activity_count(merged)
                            - max(_activity_count(existing), _activity_count(row)),
                        )
                        stats["merged_price_point_count"] += max(
                            0,
                            len(merged.get("price_history") or [])
                            - max(
                                len(existing.get("price_history") or []),
                                len(row.get("price_history") or []),
                            ),
                        )
                    selected[token] = merged
                else:
                    selected[token] = _normalize_lifecycle_row(row)

    stats["fragment_token_count"] = len(fragment_tokens)

    return selected, stats


def _metadata_for(row: Dict) -> Dict:
    return {
        "token": str(row.get("token_address") or "").lower(),
        "creator": row.get("creator", ""),
        "name": row.get("name", ""),
        "symbol": row.get("symbol", ""),
        "totalSupply": row.get("total_supply", row.get("totalSupply", 0)),
        "launchFee": row.get("launch_fee", row.get("launchFee", 0)),
        "launchTime": row.get("launch_time", row.get("launchTime", row.get("create_timestamp", 0))),
        "createTimestamp": int(row.get("create_timestamp", 0) or 0),
        "createBlock": row.get("create_block", row.get("createBlock", 0)),
    }


def build_latest_window(
    input_dir: Path,
    output_dir: Path,
    *,
    days: int = 7,
    as_of: object = None,
    force: bool = False,
    split_files: int = 1,
) -> Dict:
    if days <= 0:
        raise ValueError("days must be positive")
    if split_files <= 0:
        raise ValueError("split_files must be positive")
    as_of_timestamp = _parse_timestamp(as_of) if as_of is not None else int(datetime.now(timezone.utc).timestamp())
    if as_of_timestamp is None:
        raise ValueError(f"invalid as_of timestamp: {as_of!r}")
    cutoff_timestamp = as_of_timestamp - int(days) * 86400

    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"input lifecycle directory not found: {input_dir}")
    if output_dir.exists() and not force:
        raise FileExistsError(f"output directory already exists: {output_dir}; use --force to replace it")

    selected, stats = collect_latest_lifecycles(
        input_dir,
        cutoff_timestamp=cutoff_timestamp,
        as_of_timestamp=as_of_timestamp,
        output_dir=output_dir,
    )
    ordered = sorted(
        selected.values(),
        key=lambda row: (int(row.get("create_timestamp", 0) or 0), str(row.get("token_address", ""))),
    )

    output_parent = output_dir.parent
    output_parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_parent))
    try:
        stamp = datetime.fromtimestamp(as_of_timestamp, timezone.utc).strftime("%Y%m%d_%H%M%S")
        split_count = min(int(split_files), max(1, len(ordered)))
        chunk_size = max(1, (len(ordered) + split_count - 1) // split_count)
        lifecycle_paths = []
        for split_index in range(split_count):
            start = split_index * chunk_size
            chunk = ordered[start:start + chunk_size]
            if not chunk:
                continue
            split_stamp = datetime.fromtimestamp(as_of_timestamp, timezone.utc) + timedelta(seconds=split_index)
            split_name = split_stamp.strftime("%Y%m%d_%H%M%S")
            # Incremental naming makes DatasetBuilder consume every split file;
            # snapshot loading intentionally keeps only the newest snapshot.
            lifecycle_path = temp_dir / f"lifecycle_incremental_{split_name}.jsonl"
            with lifecycle_path.open("w", encoding="utf-8") as handle:
                for row in chunk:
                    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            lifecycle_paths.append(lifecycle_path)

        metadata = {
            "version": 2,
            "saved_at": datetime.now().isoformat(),
            "tokens": {
                row["token_address"]: _metadata_for(row)
                for row in ordered
            },
        }
        (temp_dir / "token_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "input_dir": str(input_dir),
            "output_dir": str(output_dir),
            "cutoff_timestamp": cutoff_timestamp,
            "as_of_timestamp": as_of_timestamp,
            "days": int(days),
            "output_token_count": len(ordered),
            "split_files": len(lifecycle_paths),
            "output_lifecycle_path": lifecycle_paths[0].name if len(lifecycle_paths) == 1 else None,
            "output_lifecycle_paths": [path.name for path in lifecycle_paths],
            **stats,
        }
        (temp_dir / "latest_window_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        if output_dir.exists():
            shutil.rmtree(output_dir)
        os.replace(temp_dir, output_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    summary["output_bytes"] = sum(path.stat().st_size for path in output_dir.iterdir() if path.is_file())
    (output_dir / "latest_window_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Build a latest-only BSC lifecycle window")
    parser.add_argument("--input-dir", default="data/training", help="Root containing lifecycle JSONL files")
    parser.add_argument("--output-dir", required=True, help="New latest-only lifecycle directory")
    parser.add_argument("--days", type=int, default=7, help="Creation-time window length")
    parser.add_argument("--as-of", default=None, help="UTC ISO timestamp or epoch; defaults to now")
    parser.add_argument("--split-files", type=int, default=1, help="Split output into disjoint chronological token files")
    parser.add_argument("--force", action="store_true", help="Replace an existing output directory")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    summary = build_latest_window(
        args.input_dir,
        args.output_dir,
        days=args.days,
        as_of=args.as_of,
        force=args.force,
        split_files=args.split_files,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return summary


if __name__ == "__main__":
    main()

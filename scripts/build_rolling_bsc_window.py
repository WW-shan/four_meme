#!/usr/bin/env python3
"""Build a chronology-safe rolling BSC window from two lifecycle snapshots.

The historical snapshot is kept as the training partition. Only tokens that do
not exist in that snapshot are copied from the latest snapshot, so overlapping
tokens cannot appear in both time partitions.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _token(row: dict) -> str:
    return str(row.get("token_address") or row.get("token") or "").strip().lower()


def _load_snapshot(directory: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    paths = sorted(directory.glob("*.jsonl"))
    if not paths:
        raise FileNotFoundError(f"no lifecycle JSONL files under {directory}")
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    continue
                token = _token(row)
                if not token:
                    continue
                row = dict(row)
                row["token_address"] = token
                current = rows.get(token)
                current_activity = len((current or {}).get("buys") or []) + len((current or {}).get("sells") or [])
                incoming_activity = len(row.get("buys") or []) + len(row.get("sells") or [])
                current_update = float((current or {}).get("last_update") or 0.0)
                incoming_update = float(row.get("last_update") or 0.0)
                if current is None or (incoming_activity, incoming_update) >= (current_activity, current_update):
                    rows[token] = row
    return rows


def _create_timestamp(row: dict) -> int:
    try:
        return int(float(row.get("create_timestamp") or row.get("created_at") or 0))
    except (TypeError, ValueError):
        return 0


def build_rolling_window(
    historical_dir: Path,
    latest_dir: Path,
    output_dir: Path,
    *,
    latest_split_files: int = 2,
    force: bool = False,
) -> dict:
    if latest_split_files < 1:
        raise ValueError("latest_split_files must be positive")
    if output_dir.exists() and not force:
        raise FileExistsError(f"output directory exists: {output_dir}; use --force")

    historical = _load_snapshot(historical_dir)
    latest = _load_snapshot(latest_dir)
    latest_only = [row for token, row in latest.items() if token not in historical]
    latest_only.sort(key=lambda row: (_create_timestamp(row), _token(row)))

    output_dir.mkdir(parents=True, exist_ok=True)
    if force:
        for path in output_dir.glob("*"):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    partitions: list[tuple[str, list[dict]]] = [("historical", sorted(historical.values(), key=lambda row: (_create_timestamp(row), _token(row))))]
    split_count = min(latest_split_files, max(1, len(latest_only)))
    chunk_size = max(1, (len(latest_only) + split_count - 1) // split_count)
    for index in range(split_count):
        chunk = latest_only[index * chunk_size:(index + 1) * chunk_size]
        if chunk:
            partitions.append((f"latest_only_{index + 1:02d}", chunk))

    output_paths = []
    for index, (name, rows) in enumerate(partitions, start=1):
        path = output_dir / f"lifecycle_incremental_{index:08d}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        output_paths.append(str(path))

    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "historical_dir": str(historical_dir),
        "latest_dir": str(latest_dir),
        "output_dir": str(output_dir),
        "historical_token_count": len(historical),
        "latest_token_count": len(latest),
        "overlap_token_count": len(set(historical).intersection(latest)),
        "latest_only_token_count": len(latest_only),
        "partition_names": [name for name, _rows in partitions],
        "lifecycle_paths": output_paths,
        "live_switch_evidence": False,
    }
    (output_dir / "rolling_window_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-dir", required=True)
    parser.add_argument("--latest-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--latest-split-files", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    summary = build_rolling_window(
        Path(args.historical_dir),
        Path(args.latest_dir),
        Path(args.output_dir),
        latest_split_files=args.latest_split_files,
        force=args.force,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


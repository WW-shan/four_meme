#!/usr/bin/env python3
"""Export replay-safe heat events from lifecycle JSONL snapshots.

This is a research/export command.  It does not call an external provider or
change the live collector.  Reconstructed lifecycle rows deliberately have no
local observation timestamp, so a later replay cannot accidentally assume zero
delivery latency.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline.heat_event_schema import HeatEventWriter, lifecycle_to_heat_events  # noqa: E402
from src.pipeline.runner_reserve_profile import load_lifecycles_from_paths  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", default="data/training", help="Directory containing lifecycle JSONL files")
    parser.add_argument("--lifecycle-file", action="append", default=None, help="Explicit lifecycle file; repeatable")
    parser.add_argument("--output", required=True, help="Append-only heat-event JSONL output")
    parser.add_argument("--limit-tokens", type=int, default=0, help="Optional token limit for a smoke export")
    parser.add_argument("--summary-output", default=None, help="Optional JSON summary path")
    return parser.parse_args(argv)


def _paths(args: argparse.Namespace) -> list[Path]:
    if args.lifecycle_file:
        return [Path(value) for value in args.lifecycle_file]
    return sorted(Path(args.lifecycle_dir).glob("lifecycle_*.jsonl"))


def _summary(events_count: int, tokens_count: int, paths: Iterable[Path], output: Path) -> dict:
    return {
        "schema_version": 1,
        "event_schema_version": 1,
        "tokens": int(tokens_count),
        "events": int(events_count),
        "lifecycle_paths": [str(path) for path in paths],
        "output": str(output),
        "observed_time_policy": "omitted_for_reconstructed_lifecycle_rows",
        "safe_for_live_switch": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.limit_tokens < 0:
        raise SystemExit("--limit-tokens must be non-negative")
    paths = _paths(args)
    if not paths:
        raise SystemExit("no lifecycle JSONL files found")

    lifecycles = load_lifecycles_from_paths(paths)
    if args.limit_tokens:
        lifecycles = lifecycles[: args.limit_tokens]

    output = Path(args.output)
    writer = HeatEventWriter(output)
    event_count = 0
    for lifecycle in lifecycles:
        event_count += writer.append_many(lifecycle_to_heat_events(lifecycle))

    summary = _summary(event_count, len(lifecycles), paths, output)
    if args.summary_output:
        summary_path = Path(args.summary_output)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

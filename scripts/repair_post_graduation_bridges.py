#!/usr/bin/env python3
"""Repair bridge rows after collector/schema fixes without refetching OHLCV."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline.cross_boundary import build_cross_boundary_lifecycle  # noqa: E402
from src.pipeline.runner_reserve_profile import load_lifecycles_from_paths  # noqa: E402


def _token(value):
    return str(value or "").strip().lower()


def _timestamp(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def repair(payload: dict, lifecycles: list[dict]) -> tuple[dict, dict]:
    base = {
        _token(row.get("token_address") or row.get("token")): row
        for row in lifecycles
        if _token(row.get("token_address") or row.get("token"))
    }
    as_of = _timestamp(payload.get("as_of_timestamp"))
    changed = 0
    skipped = 0
    rows = []
    for original in payload.get("rows") or []:
        row = dict(original)
        if row.get("status") != "ok":
            rows.append(row)
            continue
        token = _token(row.get("token"))
        lifecycle = base.get(token)
        graduation = _timestamp(row.get("graduation_time", (lifecycle or {}).get("graduate_time")))
        bars = []
        for value in row.get("bars") or []:
            if not isinstance(value, (list, tuple)) or len(value) < 6:
                continue
            timestamp = _timestamp(value[0])
            if timestamp is None or (graduation is not None and timestamp < graduation):
                continue
            if as_of is not None and timestamp > as_of:
                continue
            bars.append(list(value))
        row["bars"] = bars
        if lifecycle and bars:
            rebuilt = build_cross_boundary_lifecycle(
                lifecycle,
                {
                    "bars": bars,
                    "pool_address": row.get("pool_address"),
                    "quote_side": row.get("token_side", row.get("quote_side")),
                    "source_url": row.get("ohlcv_url"),
                },
                as_of_timestamp=as_of,
            )
            if rebuilt is not None:
                row["lifecycle"] = rebuilt
                row["cross_boundary"] = rebuilt.get("cross_boundary")
                row["graduation_time"] = _timestamp(rebuilt.get("graduate_time"))
                changed += 1
                rows.append(row)
                continue
        skipped += 1
        rows.append(row)
    repaired = dict(payload)
    repaired["rows"] = sorted(rows, key=lambda row: _token(row.get("token")))
    repaired["generated_at"] = datetime.now(timezone.utc).isoformat()
    repaired["bridge_repair"] = {
        "source_schema_version": payload.get("schema_version"),
        "curve_anchor_preserved": True,
        "pre_graduation_bars_removed": True,
    }
    return repaired, {
        "changed_ok_rows": changed,
        "unrepairable_ok_rows": skipped,
        "output_row_count": len(rows),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--lifecycle-dir", required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    lifecycles = load_lifecycles_from_paths(sorted(Path(args.lifecycle_dir).glob("lifecycle_*.jsonl")))
    repaired, stats = repair(payload, lifecycles)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(repaired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

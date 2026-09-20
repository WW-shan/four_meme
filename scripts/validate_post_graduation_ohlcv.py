#!/usr/bin/env python3
"""Validate timestamp and token/pool invariants in a GeckoTerminal bridge file."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline.runner_reserve_profile import load_lifecycles_from_paths  # noqa: E402


def _timestamp(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _token(value):
    return str(value or "").strip().lower()


def validate(payload: dict, lifecycles: list[dict]) -> dict:
    base = {
        _token(row.get("token_address") or row.get("token")): row
        for row in lifecycles
        if _token(row.get("token_address") or row.get("token"))
    }
    as_of = _timestamp(payload.get("as_of_timestamp"))
    rows = list(payload.get("rows") or [])
    violations = []
    checked = 0
    ok_rows = 0
    bar_count = 0
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != "ok":
            continue
        checked += 1
        token = _token(row.get("token"))
        original = base.get(token) or {}
        graduation = _timestamp(row.get("graduation_time", original.get("graduate_time")))
        bars = list(row.get("bars") or [])
        timestamps = [_timestamp(bar[0]) for bar in bars if isinstance(bar, (list, tuple)) and len(bar) >= 6]
        if not timestamps or any(value is None for value in timestamps):
            violations.append({"token": token, "code": "missing_or_malformed_bars"})
            continue
        bar_count += len(timestamps)
        if timestamps != sorted(set(timestamps)):
            violations.append({"token": token, "code": "bars_not_strictly_sorted_unique"})
        if graduation is not None and any(value < graduation for value in timestamps):
            violations.append({"token": token, "code": "bar_before_graduation"})
        if as_of is not None and any(value > as_of for value in timestamps):
            violations.append({"token": token, "code": "bar_after_as_of"})
        cross = row.get("cross_boundary") or {}
        first = _timestamp(cross.get("dex_first_timestamp"))
        last = _timestamp(cross.get("dex_last_timestamp"))
        if first is None or first != timestamps[0]:
            violations.append({"token": token, "code": "first_timestamp_mismatch"})
        if last is None or last != timestamps[-1]:
            violations.append({"token": token, "code": "last_timestamp_mismatch"})
        if cross.get("approximate_price_bridge") is not True:
            violations.append({"token": token, "code": "bridge_not_marked_approximate"})
        history = list((row.get("lifecycle") or {}).get("price_history") or [])
        if graduation is not None and not any(
            _timestamp(point.get("timestamp")) == graduation
            for point in history
            if isinstance(point, dict) and point.get("type") != "dex_close"
        ):
            violations.append({"token": token, "code": "missing_curve_graduation_anchor"})
        ok_rows += 1
    return {
        "schema_version": 1,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "source_output": payload.get("source_output"),
        "as_of_timestamp": as_of,
        "input_row_count": len(rows),
        "ok_row_count": ok_rows,
        "checked_ok_row_count": checked,
        "bar_count": bar_count,
        "violation_count": len(violations),
        "violation_codes": dict(sorted(Counter(item["code"] for item in violations).items())),
        "violations": violations[:200],
        "valid": not violations,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dex-output", required=True)
    parser.add_argument("--lifecycle-dir", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    payload = json.loads(Path(args.dex_output).read_text(encoding="utf-8"))
    paths = sorted(Path(args.lifecycle_dir).glob("lifecycle_*.jsonl"))
    lifecycles = load_lifecycles_from_paths(paths)
    report = validate(payload, lifecycles)
    output = Path(args.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "valid": report["valid"], "violations": report["violation_count"]}, ensure_ascii=False))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

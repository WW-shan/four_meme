#!/usr/bin/env python3
"""Audit frozen candidates and saved RPC metadata; never submit an order."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline.profitability_evidence_audit import audit_profitability_inputs  # noqa: E402
from src.pipeline.runner_reserve_profile import load_lifecycles_from_paths  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-report", type=Path, required=True)
    parser.add_argument("--lifecycle-dir", type=Path, required=True)
    parser.add_argument("--quote-snapshot", type=Path, required=True)
    parser.add_argument("--asset-snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    paths = sorted(args.lifecycle_dir.glob("lifecycle_*.jsonl"))
    if not paths:
        parser.error("no lifecycle files found")
    sources = [args.candidate_report, args.quote_snapshot, args.asset_snapshot]
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in sources]
    lifecycles = load_lifecycles_from_paths(paths)
    report = audit_profitability_inputs(payloads[0], lifecycles, payloads[1], payloads[2])
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["inputs"] = [
        {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sources + paths
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    csv_path = args.output.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(report["rows"][0]) if report["rows"] else ["token"])
        writer.writeheader()
        for row in report["rows"]:
            writer.writerow({**row, "audit_flags": ";".join(row["audit_flags"])})
    print(json.dumps({"output": str(args.output), "csv": str(csv_path), **report["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

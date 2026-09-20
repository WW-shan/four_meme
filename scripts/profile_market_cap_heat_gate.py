#!/usr/bin/env python3
"""Legacy price/activity diagnostic; not a validated profitability backtest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline.market_cap_heat_gate import (  # noqa: E402
    DEFAULT_THRESHOLDS_USD,
    MarketCapHeatConfig,
    profile_market_cap_heat_gate,
)
from src.pipeline.runner_reserve_profile import load_lifecycles_from_paths  # noqa: E402


def _floats(raw: str) -> tuple[float, ...]:
    values = tuple(sorted({float(part.strip()) for part in str(raw).split(",") if part.strip()}))
    if not values or any(value <= 0.0 for value in values):
        raise ValueError("thresholds must contain positive numbers")
    return values


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", default="data/training/latest_bsc_20260914_31d_final")
    parser.add_argument("--lifecycle-file", action="append", default=None)
    parser.add_argument("--thresholds-usd", default=",".join(str(value) for value in DEFAULT_THRESHOLDS_USD))
    parser.add_argument("--bnb-usd", type=float, default=734.17)
    parser.add_argument("--output", required=True, help="Explicit diagnostic output; do not replace old evidence")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    paths = [Path(value) for value in args.lifecycle_file] if args.lifecycle_file else sorted(
        Path(args.lifecycle_dir).glob("lifecycle_*.jsonl")
    )
    if not paths:
        raise SystemExit("no lifecycle files found")
    lifecycles = load_lifecycles_from_paths(paths)
    report = profile_market_cap_heat_gate(
        lifecycles,
        thresholds_usd=_floats(args.thresholds_usd),
        config=MarketCapHeatConfig(bnb_usd=float(args.bnb_usd)),
    )
    report.update(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "inputs": {
                "lifecycle_dir": str(args.lifecycle_dir),
                "lifecycle_files": [str(path) for path in paths],
                "lifecycle_count": len(lifecycles),
            },
        }
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "lifecycle_count": len(lifecycles)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

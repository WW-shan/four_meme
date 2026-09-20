#!/usr/bin/env python3
"""Write price-observation diagnostics; these do not validate trade execution."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline.market_cap_heat_failure import (  # noqa: E402
    FailureAttributionConfig,
    attribute_lifecycles,
)
from src.pipeline.market_cap_heat_gate import MarketCapHeatConfig  # noqa: E402
from src.pipeline.runner_reserve_profile import load_lifecycles_from_paths  # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", default="data/training/latest_bsc_20260914_31d_final")
    parser.add_argument("--lifecycle-file", action="append", default=None)
    parser.add_argument("--threshold-usd", type=float, default=50_000.0)
    parser.add_argument("--bnb-usd", type=float, default=734.17)
    parser.add_argument("--output", required=True, help="New diagnostic report path; preserve the earlier audit artifacts")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    paths = [Path(value) for value in args.lifecycle_file] if args.lifecycle_file else sorted(
        Path(args.lifecycle_dir).glob("lifecycle_*.jsonl")
    )
    if not paths:
        raise SystemExit("no lifecycle files found")
    lifecycles = load_lifecycles_from_paths(paths)
    config = FailureAttributionConfig(
        threshold_usd=float(args.threshold_usd),
        heat=MarketCapHeatConfig(
            bnb_usd=float(args.bnb_usd),
            horizons_seconds=(900, 3_600, 21_600, 86_400),
        ),
    )
    rows, summary = attribute_lifecycles(lifecycles, config=config)
    report = {
        "schema_version": 2,
        "evidence_status": "price_observation_only",
        "model_selection_eligible": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "safe_for_live_switch": False,
        "inputs": {
            "lifecycle_dir": str(args.lifecycle_dir),
            "lifecycle_files": [str(path) for path in paths],
            "lifecycle_count": len(lifecycles),
        },
        "config": {
            "threshold_usd": config.threshold_usd,
            "bnb_usd": config.heat.bnb_usd,
            "confirmation_seconds": config.heat.confirmation_seconds,
            "entry_delay_seconds": config.heat.execution_delay_seconds,
            "exit_grace_seconds": config.exit_grace_seconds,
            "fee_rate": config.heat.fee_rate,
            "slippage_rate": config.heat.slippage_rate,
            "horizons_seconds": list(config.heat.horizons_seconds),
        },
        "summary": summary,
        "rows": rows,
        "method_notes": [
            "Entry marks incorporate recorded prices up to the planned entry time; marks are not order-size-specific fills.",
            "Nearby events only measure price observation coverage, not whether an AMM order would execute.",
            "Snapshot returns use only prices at or before the horizon and expose snapshot age.",
            "Quote units, actual venue state, costs and fills remain unverified; do not use these proxies for model selection.",
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

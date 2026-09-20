#!/usr/bin/env python3
"""Profile a delayed executable two-stage runner reserve on lifecycle data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.dataset_builder import stable_lifecycle_order  # noqa: E402
from src.pipeline.runner_reserve_profile import (  # noqa: E402
    RunnerReplayConfig,
    load_lifecycles_from_paths,
    profile_lifecycles,
)


def _positive_ints(raw: str) -> list[int]:
    values = [int(part.strip()) for part in str(raw).split(",") if part.strip()]
    values = sorted({value for value in values if value > 0})
    if not values:
        raise argparse.ArgumentTypeError("must contain at least one positive integer")
    return values


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", default="data/training/latest_bsc_20260911")
    parser.add_argument("--lifecycle-file", action="append", default=None)
    parser.add_argument("--horizons", type=_positive_ints, default=[300, 1800, 7200, 21600, 86400])
    parser.add_argument("--entry-delay-seconds", type=int, default=3)
    parser.add_argument("--exit-delay-seconds", type=int, default=3)
    parser.add_argument("--fee-bps", type=float, default=100.0)
    parser.add_argument("--slippage-bps", type=float, default=200.0)
    parser.add_argument("--stop-loss-pct", type=float, default=-30.0)
    parser.add_argument("--activation-return-pct", type=float, default=100.0)
    parser.add_argument("--partial-exit-ratio", type=float, default=0.70)
    parser.add_argument("--reserve-drawdown-pct", type=float, default=30.0)
    parser.add_argument("--reserve-floor-return-pct", type=float, default=0.0)
    parser.add_argument("--min-entry-unique-buyers", type=int, default=3)
    parser.add_argument("--min-entry-buy-count", type=int, default=5)
    parser.add_argument("--max-entry-age-seconds", type=int, default=300)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.lifecycle_file:
        paths = [Path(path) for path in args.lifecycle_file]
    else:
        paths = stable_lifecycle_order(Path(args.lifecycle_dir).glob("lifecycle_*.jsonl"))
    config = RunnerReplayConfig(
        entry_delay_seconds=args.entry_delay_seconds,
        exit_delay_seconds=args.exit_delay_seconds,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        stop_loss_pct=args.stop_loss_pct,
        activation_return_pct=args.activation_return_pct,
        partial_exit_ratio=args.partial_exit_ratio,
        reserve_drawdown_pct=args.reserve_drawdown_pct,
        reserve_floor_return_pct=args.reserve_floor_return_pct,
        min_entry_unique_buyers=args.min_entry_unique_buyers,
        min_entry_buy_count=args.min_entry_buy_count,
        max_entry_age_seconds=args.max_entry_age_seconds,
    )
    lifecycles = load_lifecycles_from_paths(paths)
    report = profile_lifecycles(lifecycles, horizons=args.horizons, config=config)
    report["inputs"]["lifecycle_dir"] = str(args.lifecycle_dir)
    report["inputs"]["lifecycle_files"] = [str(path) for path in paths]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    for horizon, row in report["horizons"].items():
        print(
            "horizon=%ss samples=%d activated=%d activation_rate=%.3f baseline_median=%.3f%% runner_median=%.3f%%"
            % (
                horizon,
                row["sample_count"],
                row["activated_count"],
                row["activation_rate"],
                row["baseline_return_pct"]["median"],
                row["runner_return_pct"]["median"],
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


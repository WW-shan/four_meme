#!/usr/bin/env python3
"""Replay the configured legacy model selector against target barriers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline.runner_reserve_profile import load_lifecycles_from_paths  # noqa: E402
from src.pipeline.selector_barrier_replay import load_legacy_selector, replay_selector  # noqa: E402
from src.pipeline.target_barrier_profile import (  # noqa: E402
    BarrierReplayConfig,
    DEFAULT_HORIZONS,
)


def _ints(raw: str) -> list[int]:
    return [int(part.strip()) for part in str(raw).split(",") if part.strip()]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", default="data/models/20260519_v95_v84_selective_nearmiss_gate")
    parser.add_argument("--lifecycle-dir", default="data/training/bsc_month_latest_20260911_run7")
    parser.add_argument("--lifecycle-file", action="append", default=None)
    parser.add_argument("--horizons", default=",".join(str(value) for value in DEFAULT_HORIZONS))
    parser.add_argument("--target-return-pct", type=float, default=20.0)
    parser.add_argument("--stop-loss-pct", type=float, default=-30.0)
    parser.add_argument("--entry-delay-seconds", type=int, default=None)
    parser.add_argument("--exit-delay-seconds", type=int, default=None)
    parser.add_argument("--fee-bps", type=float, default=100.0)
    parser.add_argument("--slippage-bps", type=float, default=200.0)
    parser.add_argument("--fixed-stake-bnb", type=float, default=0.1)
    parser.add_argument("--min-entry-unique-buyers", type=int, default=3)
    parser.add_argument("--min-entry-buy-count", type=int, default=5)
    parser.add_argument("--max-entry-age-seconds", type=int, default=300)
    parser.add_argument("--entry-max-fill-wait-seconds", type=int, default=None)
    parser.add_argument("--entry-price-protection-pct", type=float, default=None)
    parser.add_argument("--incomplete-policy", choices=("last_observation", "loss"), default="loss")
    parser.add_argument("--max-open-positions", type=int, default=8)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    paths = [Path(value) for value in args.lifecycle_file] if args.lifecycle_file else sorted(
        Path(args.lifecycle_dir).glob("lifecycle_*.jsonl")
    )
    lifecycles = load_lifecycles_from_paths(paths)
    selector = load_legacy_selector(args.model_dir)
    config = BarrierReplayConfig(
        entry_delay_seconds=(
            selector["entry_delay_seconds"] if args.entry_delay_seconds is None else args.entry_delay_seconds
        ),
        exit_delay_seconds=(
            selector["exit_delay_seconds"] if args.exit_delay_seconds is None else args.exit_delay_seconds
        ),
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        fixed_stake_bnb=args.fixed_stake_bnb,
        min_entry_unique_buyers=args.min_entry_unique_buyers,
        min_entry_buy_count=args.min_entry_buy_count,
        max_entry_age_seconds=args.max_entry_age_seconds,
        incomplete_policy=args.incomplete_policy,
        entry_max_fill_wait_seconds=(
            selector.get("entry_max_fill_wait_seconds")
            if args.entry_max_fill_wait_seconds is None
            else args.entry_max_fill_wait_seconds
        ),
        entry_price_protection_pct=(
            selector.get("entry_price_protection_pct")
            if args.entry_price_protection_pct is None
            else args.entry_price_protection_pct
        ),
    )
    report = replay_selector(
        lifecycles,
        model_dir=args.model_dir,
        horizons=_ints(args.horizons),
        target_return_pct=args.target_return_pct,
        stop_loss_pct=args.stop_loss_pct,
        config=config,
        max_open_positions=args.max_open_positions,
    )
    report["inputs"]["lifecycle_dir"] = str(args.lifecycle_dir)
    report["inputs"]["lifecycle_files"] = [str(path) for path in paths]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    for horizon, row in report["horizons"].items():
        for name in ("activity_gate_all", "legacy_model_selector"):
            returns = row[name]["summary"]["conservative_all_candidates"]["returns"]
            print(
                "%s horizon=%ss candidates=%d selected=%d total_return=%.2f%% net=%.4f BNB"
                % (
                    name,
                    horizon,
                    row[name]["candidate_count"],
                    row[name]["capacity_selected_count"],
                    returns["total_return_pct"],
                    returns["net_profit_bnb"],
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

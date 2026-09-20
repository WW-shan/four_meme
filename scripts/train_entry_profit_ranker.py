#!/usr/bin/env python3
"""Train a fresh entry-time barrier classifier and profit ranker."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline.entry_profit_ranker import (  # noqa: E402
    DEFAULT_FEATURES,
    train_profit_ranker_experiment,
)
from src.pipeline.runner_reserve_profile import load_lifecycles_from_paths  # noqa: E402
from src.pipeline.target_barrier_profile import BarrierReplayConfig  # noqa: E402


def _feature_names(raw: str) -> list[str]:
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", default="data/training/bsc_month_latest_20260911_run7")
    parser.add_argument("--lifecycle-file", action="append", default=None)
    parser.add_argument("--horizon-seconds", type=int, default=21_600)
    parser.add_argument("--target-return-pct", type=float, default=20.0)
    parser.add_argument("--stop-loss-pct", type=float, default=-30.0)
    parser.add_argument(
        "--label-mode",
        choices=("barrier", "uncapped_horizon"),
        default="barrier",
        help="barrier clips returns at target; uncapped_horizon trains on the horizon path return",
    )
    parser.add_argument("--entry-delay-seconds", type=int, default=3)
    parser.add_argument("--exit-delay-seconds", type=int, default=3)
    parser.add_argument("--fee-bps", type=float, default=100.0)
    parser.add_argument("--slippage-bps", type=float, default=200.0)
    parser.add_argument("--fixed-stake-bnb", type=float, default=0.1)
    parser.add_argument("--initial-equity-bnb", type=float, default=1.0)
    parser.add_argument("--max-open-positions", type=int, default=8)
    parser.add_argument("--min-entry-unique-buyers", type=int, default=3)
    parser.add_argument("--min-entry-buy-count", type=int, default=5)
    parser.add_argument("--max-entry-age-seconds", type=int, default=300)
    parser.add_argument("--incomplete-policy", choices=("last_observation", "loss"), default="loss")
    parser.add_argument("--feature-names", default=",".join(DEFAULT_FEATURES))
    parser.add_argument("--iterations", type=int, default=400)
    parser.add_argument("--depth", type=int, default=6)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    paths = [Path(value) for value in args.lifecycle_file] if args.lifecycle_file else sorted(
        Path(args.lifecycle_dir).glob("lifecycle_*.jsonl")
    )
    lifecycles = load_lifecycles_from_paths(paths)
    config = BarrierReplayConfig(
        entry_delay_seconds=args.entry_delay_seconds,
        exit_delay_seconds=args.exit_delay_seconds,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        fixed_stake_bnb=args.fixed_stake_bnb,
        initial_equity_bnb=args.initial_equity_bnb,
        max_open_positions=args.max_open_positions,
        min_entry_unique_buyers=args.min_entry_unique_buyers,
        min_entry_buy_count=args.min_entry_buy_count,
        max_entry_age_seconds=args.max_entry_age_seconds,
        incomplete_policy=args.incomplete_policy,
    )
    model_params = {
        "iterations": args.iterations,
        "depth": args.depth,
        "learning_rate": args.learning_rate,
    }
    report = train_profit_ranker_experiment(
        lifecycles,
        horizon_seconds=args.horizon_seconds,
        target_return_pct=args.target_return_pct,
        stop_loss_pct=args.stop_loss_pct,
        label_mode=args.label_mode,
        config=config,
        feature_names=_feature_names(args.feature_names),
        model_params=model_params,
        output_dir=args.output_dir,
    )
    report["inputs"]["lifecycle_dir"] = str(args.lifecycle_dir)
    report["inputs"]["lifecycle_files"] = [str(path) for path in paths]
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {report_path}")
    for split_name in ("train", "validation", "final"):
        split = report["splits"].get(split_name, {})
        for model_name in ("classifier", "profit_ranker"):
            best = max(
                split.get(model_name, {}).get("ranked", []),
                key=lambda row: row.get("summary", {}).get("returns", {}).get("total_return_pct", -1e18),
                default=None,
            )
            if best:
                print(
                    "%s %s %s n=%d total_return=%.2f%% net=%.4f BNB"
                    % (
                        split_name,
                        model_name,
                        best["selection"],
                        best["candidate_count"],
                        best["summary"]["returns"]["total_return_pct"],
                        best["summary"]["returns"]["net_profit_bnb"],
                    )
                )
    for model_name, portfolio in report.get("portfolio_backtest", {}).items():
        validation = portfolio.get("splits", {}).get("validation", {})
        final = portfolio.get("splits", {}).get("final", {})
        print(
            "portfolio %s threshold=%.6g validation_net=%.4f BNB final_net=%.4f BNB trades=%d/%d accepted=%s"
            % (
                model_name,
                portfolio.get("selected_threshold", 1.0),
                validation.get("net_profit_bnb", 0.0),
                final.get("net_profit_bnb", 0.0),
                validation.get("trade_count", 0),
                final.get("trade_count", 0),
                portfolio.get("accepted_for_runtime", False),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

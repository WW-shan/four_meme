#!/usr/bin/env python3
"""Generate delayed executable-return profiles for several BSC holding windows."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline.horizon_profile import DEFAULT_HORIZONS, build_horizon_profile


def _int_list(raw: str) -> list[int]:
    values = []
    for part in str(raw).split(","):
        part = part.strip()
        if part:
            values.append(int(part))
    return values


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", default="data/training/latest_bsc_20260911")
    parser.add_argument("--lifecycle-file", action="append", default=None)
    parser.add_argument("--future-windows", default=",".join(str(value) for value in DEFAULT_HORIZONS))
    parser.add_argument("--sample-mode", default="trade_event", choices=("trade_event", "per_second"))
    parser.add_argument("--max-sample-age-seconds", type=int, default=300)
    parser.add_argument("--max-samples-per-token", type=int, default=120)
    parser.add_argument("--minimum-token-support", type=int, default=50)
    parser.add_argument("--min-entry-unique-buyers", type=int, default=3)
    parser.add_argument("--min-entry-buy-count", type=int, default=5)
    parser.add_argument("--label-fee-bps", type=float, default=100.0)
    parser.add_argument("--label-slippage-bps", type=float, default=200.0)
    parser.add_argument("--label-stop-loss-pct", type=float, default=-30.0)
    parser.add_argument("--label-target-return-pct", type=float, default=20.0)
    parser.add_argument("--label-entry-delay-seconds", type=int, default=3)
    parser.add_argument("--label-exit-delay-seconds", type=int, default=3)
    parser.add_argument("--label-delay-robust-entry-delays", default="0,3,5")
    parser.add_argument("--label-delay-robust-min-weight", type=float, default=1.0)
    parser.add_argument("--label-live-downside-penalty-weight", type=float, default=0.25)
    parser.add_argument("--label-fixed-stake-bnb", type=float, default=0.1)
    parser.add_argument("--label-entry-price-protection-pct", type=float, default=0.25)
    parser.add_argument("--no-flow-features", action="store_true")
    parser.add_argument("--output", default=None)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.max_sample_age_seconds <= 0:
        raise SystemExit("--max-sample-age-seconds must be positive")
    if args.max_samples_per_token <= 0:
        raise SystemExit("--max-samples-per-token must be positive")
    if args.minimum_token_support <= 0:
        raise SystemExit("--minimum-token-support must be positive")
    default_output = (
        "data/replay_reports/"
        f"horizon_profile_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output = Path(args.output or default_output)
    profile = build_horizon_profile(
        {
            "lifecycle_dir": args.lifecycle_dir,
            "lifecycle_paths": args.lifecycle_file,
            "future_windows": _int_list(args.future_windows),
            "sample_mode": args.sample_mode,
            "max_sample_age_seconds": args.max_sample_age_seconds,
            "max_samples_per_token": args.max_samples_per_token,
            "minimum_token_support": args.minimum_token_support,
            "min_entry_unique_buyers": args.min_entry_unique_buyers,
            "min_entry_buy_count": args.min_entry_buy_count,
            "label_fee_bps": args.label_fee_bps,
            "label_slippage_bps": args.label_slippage_bps,
            "label_stop_loss_pct": args.label_stop_loss_pct,
            "label_target_return_pct": args.label_target_return_pct,
            "label_entry_delay_seconds": args.label_entry_delay_seconds,
            "label_exit_delay_seconds": args.label_exit_delay_seconds,
            "label_delay_robust_entry_delay_seconds": _int_list(args.label_delay_robust_entry_delays),
            "label_delay_robust_min_weight": args.label_delay_robust_min_weight,
            "label_live_downside_penalty_weight": args.label_live_downside_penalty_weight,
            "label_fixed_stake_bnb": args.label_fixed_stake_bnb,
            "label_entry_price_protection_pct": args.label_entry_price_protection_pct,
            "include_flow_features": not args.no_flow_features,
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    for row in profile["horizons"]:
        returns = row["returns"]
        print(
            "horizon=%ss samples=%d tokens=%d robust_mean=%.3f robust_median=%.3f final_mean=%.3f candidate=%s"
            % (
                row["horizon_seconds"],
                row["support"]["sample_count"],
                row["support"]["token_count"],
                returns["live_delay_robust_return_pct"]["mean"],
                returns["live_delay_robust_return_pct"]["median"],
                returns["live_cost_adjusted_final_return_pct"]["mean"],
                row["decision"]["research_candidate"],
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

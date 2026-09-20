#!/usr/bin/env python3
"""Profile entry-time BSC target/stop barriers and candidate gates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline.runner_reserve_profile import load_lifecycles_from_paths  # noqa: E402
from src.pipeline.target_barrier_profile import (  # noqa: E402
    BarrierReplayConfig,
    DEFAULT_FEATURES,
    DEFAULT_HORIZONS,
    DEFAULT_STOPS,
    DEFAULT_TARGETS,
    default_entry_gate_grid,
    profile_target_barriers,
)


def _number_list(raw: str, *, positive: bool) -> list[float]:
    values = []
    for part in str(raw).split(","):
        if not part.strip():
            continue
        value = float(part.strip())
        if (positive and value > 0.0) or (not positive and value < 0.0):
            values.append(value)
    return values


def _int_list(raw: str) -> list[int]:
    return [int(value) for value in _number_list(raw, positive=True)]


def _load_json_list(path: str | None, default):
    if not path:
        return default
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("gates") or payload.get("candidates")
    if not isinstance(payload, list):
        raise SystemExit("JSON gate file must contain a list or a gates/candidates field")
    return payload


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", default="data/training/bsc_month_latest_20260911_run7")
    parser.add_argument("--lifecycle-file", action="append", default=None)
    parser.add_argument("--horizons", default=",".join(str(value) for value in DEFAULT_HORIZONS))
    parser.add_argument("--targets", default=",".join(str(value) for value in DEFAULT_TARGETS))
    parser.add_argument("--stops", default=",".join(str(value) for value in DEFAULT_STOPS))
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
    parser.add_argument(
        "--incomplete-policy",
        choices=("last_observation", "loss"),
        default="last_observation",
        help="How to score paths ending before the horizon; both complete and conservative metrics are reported",
    )
    parser.add_argument("--entry-gate-grid-json", default=None)
    parser.add_argument("--feature-names", default=",".join(DEFAULT_FEATURES))
    parser.add_argument("--feature-quantiles", default="0.50,0.70,0.80,0.90")
    parser.add_argument("--feature-target-return-pct", type=float, default=20.0)
    parser.add_argument("--feature-stop-loss-pct", type=float, default=-30.0)
    parser.add_argument("--min-feature-gate-samples", type=int, default=50)
    parser.add_argument("--min-entry-gate-validation-samples", type=int, default=50)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.lifecycle_file:
        paths = [Path(value) for value in args.lifecycle_file]
    else:
        paths = sorted(Path(args.lifecycle_dir).glob("lifecycle_*.jsonl"))
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
    gates = _load_json_list(args.entry_gate_grid_json, default_entry_gate_grid())
    report = profile_target_barriers(
        lifecycles,
        horizons=_int_list(args.horizons),
        targets=_number_list(args.targets, positive=True),
        stops=_number_list(args.stops, positive=False),
        config=config,
        feature_names=[value.strip() for value in args.feature_names.split(",") if value.strip()],
        feature_quantiles=_number_list(args.feature_quantiles, positive=True),
        feature_target_return_pct=args.feature_target_return_pct,
        feature_stop_loss_pct=args.feature_stop_loss_pct,
        min_feature_gate_samples=args.min_feature_gate_samples,
        entry_gate_grid=gates,
        min_entry_gate_validation_samples=args.min_entry_gate_validation_samples,
    )
    report["inputs"]["lifecycle_dir"] = str(args.lifecycle_dir)
    report["inputs"]["lifecycle_files"] = [str(path) for path in paths]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    for row in report["barrier_grid"]:
        if row["target_return_pct"] == args.feature_target_return_pct and row["stop_loss_pct"] == args.feature_stop_loss_pct:
            all_returns = row["conservative_all_candidates"]["returns"]
            print(
                "horizon=%ss target=%.0f stop=%.0f candidates=%d complete=%d total_return=%.2f%% net=%.4f BNB target_first=%.3f"
                % (
                    row["horizon_seconds"],
                    row["target_return_pct"],
                    row["stop_loss_pct"],
                    row["candidate_count"],
                    row["complete_count"],
                    all_returns["total_return_pct"],
                    all_returns["net_profit_bnb"],
                    row["conservative_all_candidates"]["rates"]["target_first_rate"],
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

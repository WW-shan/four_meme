#!/usr/bin/env python3
"""Train and replay the staged post-graduation DEX-confirmation strategy."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_tail_capture_training import (  # noqa: E402
    _load_dex_rows,
    _load_market_context,
    _merge_dex_lifecycles,
)
from src.pipeline.dex_confirmation_strategy import (  # noqa: E402
    DexConfirmationConfig,
    train_dex_confirmation_experiment,
)
from src.pipeline.runner_reserve_profile import load_lifecycles_from_paths  # noqa: E402
from src.pipeline.tail_capture_strategy import TAIL_FEATURES, TailReplayConfig  # noqa: E402


def _ints(raw: str) -> list[int]:
    return sorted({int(part.strip()) for part in str(raw).split(",") if part.strip()})


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", default="data/training/latest_bsc_20260914_31d_final")
    parser.add_argument("--dex-output", default="data/research/post_graduation_ohlcv_20260914_final.json")
    parser.add_argument("--market-metrics", default="docs/research/20260913-bsc-strategy-foundation/18-bsc-market-metrics-20260914.json")
    parser.add_argument("--horizons", default="21600,86400,259200")
    parser.add_argument("--output-dir", default="data/models/20260914_dex_confirmation_v1")
    parser.add_argument("--report", default="docs/research/20260913-bsc-strategy-foundation/31-dex-confirmation-20260914.json")
    parser.add_argument("--confirmation-bars", type=int, default=2)
    parser.add_argument("--max-confirmation-wait-hours", type=int, default=8)
    parser.add_argument("--min-market-volume-ratio", type=float, default=0.80)
    parser.add_argument("--min-price-retention-pct", type=float, default=-35.0)
    parser.add_argument("--min-volume-retention-ratio", type=float, default=0.20)
    parser.add_argument("--trailing-stop-pct", type=float, default=40.0)
    parser.add_argument("--max-candidates-per-token", type=int, default=3)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    paths = sorted(Path(args.lifecycle_dir).glob("lifecycle_*.jsonl"))
    lifecycles = load_lifecycles_from_paths(paths)
    dex_rows, dex_coverage = _load_dex_rows(Path(args.dex_output))
    lifecycles, merge_coverage = _merge_dex_lifecycles(lifecycles, dex_rows)
    market_context, market_coverage = _load_market_context(Path(args.market_metrics))
    replay = TailReplayConfig(trailing_stop_pct=args.trailing_stop_pct)
    confirmation = DexConfirmationConfig(
        confirmation_bars=args.confirmation_bars,
        max_confirmation_wait_hours=args.max_confirmation_wait_hours,
        min_market_volume_ratio=args.min_market_volume_ratio,
        min_price_retention_pct=args.min_price_retention_pct,
        min_volume_retention_ratio=args.min_volume_retention_ratio,
        replay=replay,
    )
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_type": "bsc_sequential_dex_confirmation",
        "inputs": {
            "lifecycle_dir": str(args.lifecycle_dir),
            "lifecycle_files": [str(path) for path in paths],
            "dex_output": str(args.dex_output),
            "market_metrics": str(args.market_metrics),
            "horizons": _ints(args.horizons),
            "feature_names": list(TAIL_FEATURES),
            "max_candidates_per_token": int(args.max_candidates_per_token),
            "confirmation_config": {
                "confirmation_bars": confirmation.confirmation_bars,
                "max_confirmation_wait_hours": confirmation.max_confirmation_wait_hours,
                "min_market_volume_ratio": confirmation.min_market_volume_ratio,
                "min_price_retention_pct": confirmation.min_price_retention_pct,
                "min_volume_retention_ratio": confirmation.min_volume_retention_ratio,
                "trailing_stop_pct": replay.trailing_stop_pct,
            },
        },
        "data_coverage": {**dex_coverage, **merge_coverage, **market_coverage},
        "horizons": {},
        "safe_for_live_switch": False,
    }
    for horizon in _ints(args.horizons):
        output_dir = Path(args.output_dir) / f"horizon_{horizon}s"
        result = train_dex_confirmation_experiment(
            lifecycles,
            horizon_seconds=horizon,
            confirmation_config=confirmation,
            feature_names=TAIL_FEATURES,
            market_context=market_context,
            max_candidates_per_token=args.max_candidates_per_token,
            output_dir=output_dir,
        )
        report["horizons"][str(horizon)] = result
        print(
            "horizon=%ss status=%s candidates=%d confirmed=%d final_net=%s harsh_final=%s accepted=%s"
            % (
                horizon,
                result.get("status"),
                result.get("inputs", {}).get("candidate_count", 0),
                result.get("path_coverage", {}).get("confirmed_entry_count", 0),
                result.get("splits", {}).get("final", {}).get("portfolio", {}).get("net_profit_bnb", "n/a"),
                result.get("stress_replay", {}).get("harsh", {}).get("final", {}).get("net_profit_bnb", "n/a"),
                result.get("research_acceptance", {}).get("passes", False),
            )
        )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

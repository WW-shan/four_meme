#!/usr/bin/env python3
"""Profile a small predeclared grid of causal DEX confirmation rules."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_tail_capture_training import _load_dex_rows, _load_market_context, _merge_dex_lifecycles  # noqa: E402
from src.pipeline.dex_confirmation_strategy import DexConfirmationConfig, simulate_confirmed_entry  # noqa: E402
from src.pipeline.runner_reserve_profile import load_lifecycles_from_paths  # noqa: E402
from src.pipeline.tail_capture_strategy import (  # noqa: E402
    TAIL_FEATURES,
    TailReplayConfig,
    _portfolio_backtest,
    _token_time_splits,
    build_tail_candidates,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", default="data/training/latest_bsc_20260914_31d_final")
    parser.add_argument("--dex-output", default="data/research/post_graduation_ohlcv_20260914_final.json")
    parser.add_argument("--market-metrics", default="docs/research/20260913-bsc-strategy-foundation/18-bsc-market-metrics-20260914.json")
    parser.add_argument("--horizon-seconds", type=int, default=21_600)
    parser.add_argument("--output", default="docs/research/20260913-bsc-strategy-foundation/32-dex-confirmation-grid-20260914.json")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    paths = sorted(Path(args.lifecycle_dir).glob("lifecycle_*.jsonl"))
    lifecycles = load_lifecycles_from_paths(paths)
    dex_rows, dex_coverage = _load_dex_rows(Path(args.dex_output))
    lifecycles, merge_coverage = _merge_dex_lifecycles(lifecycles, dex_rows)
    market_context, market_coverage = _load_market_context(Path(args.market_metrics))
    replay = TailReplayConfig(trailing_stop_pct=40.0)
    candidates = build_tail_candidates(
        lifecycles,
        replay,
        feature_names=TAIL_FEATURES,
        max_candidates_per_token=3,
        market_context=market_context,
    )
    splits = _token_time_splits(candidates, args.horizon_seconds)
    scores = [0.0] * len(candidates)
    grid = []
    for bars in (1, 2, 3):
        for wait_hours in (8, 12):
            for market_ratio in (0.50, 0.80):
                for volume_ratio in (0.05, 0.20):
                    config = DexConfirmationConfig(
                        confirmation_bars=bars,
                        max_confirmation_wait_hours=wait_hours,
                        min_market_volume_ratio=market_ratio,
                        min_price_retention_pct=-50.0,
                        min_volume_retention_ratio=volume_ratio,
                        replay=replay,
                    )
                    outcomes = [
                        dict(simulate_confirmed_entry(candidate, horizon_seconds=args.horizon_seconds, config=config))
                        for candidate in candidates
                    ]
                    for outcome in outcomes:
                        if not outcome.get("entry_available"):
                            outcome["status"] = "missing_entry"
                    validation = _portfolio_backtest(candidates, outcomes, scores, splits["validation"], replay, -1e30)
                    final = _portfolio_backtest(candidates, outcomes, scores, splits["final"], replay, -1e30)
                    validation_complete = [
                        index for index in splits["validation"]
                        if outcomes[index].get("confirmation_passed") and outcomes[index].get("complete") and outcomes[index].get("status") == "ok"
                    ]
                    final_complete = [
                        index for index in splits["final"]
                        if outcomes[index].get("confirmation_passed") and outcomes[index].get("complete") and outcomes[index].get("status") == "ok"
                    ]
                    grid.append({
                        "confirmation_bars": bars,
                        "max_confirmation_wait_hours": wait_hours,
                        "min_market_volume_ratio": market_ratio,
                        "min_price_retention_pct": -50.0,
                        "min_volume_retention_ratio": volume_ratio,
                        "confirmed_count": int(sum(bool(row.get("confirmation_passed")) for row in outcomes)),
                        "validation": validation,
                        "final": final,
                        "validation_complete_count": len(validation_complete),
                        "final_complete_count": len(final_complete),
                        "validation_complete_replay": _portfolio_backtest(candidates, outcomes, scores, validation_complete, replay, -1e30),
                        "final_complete_replay": _portfolio_backtest(candidates, outcomes, scores, final_complete, replay, -1e30),
                    })
    eligible = [row for row in grid if row["validation"]["trade_count"] >= 10 and row["validation_complete_count"] >= 5]
    selected = max(eligible or grid, key=lambda row: (row["validation"]["net_profit_bnb"], row["validation_complete_replay"]["net_profit_bnb"], -row["confirmed_count"]))
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_type": "bsc_dex_confirmation_rule_grid",
        "inputs": {"lifecycle_dir": str(args.lifecycle_dir), "dex_output": str(args.dex_output), "horizon_seconds": args.horizon_seconds, "candidate_count": len(candidates)},
        "data_coverage": {**dex_coverage, **merge_coverage, **market_coverage},
        "grid_count": len(grid),
        "selected_by_validation": selected,
        "grid": sorted(grid, key=lambda row: row["validation"]["net_profit_bnb"], reverse=True),
        "safe_for_live_switch": False,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "grid_count": len(grid), "selected": selected}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

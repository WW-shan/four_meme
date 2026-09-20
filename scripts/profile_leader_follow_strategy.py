#!/usr/bin/env python3
"""Profile a rule-based BSC leader/follower event strategy."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_tail_capture_training import _load_dex_rows, _merge_dex_lifecycles  # noqa: E402
from src.pipeline.leader_follow_strategy import (  # noqa: E402
    LeaderFollowConfig,
    build_leader_follow_candidates,
    simulate_leader_follow,
)
from src.pipeline.runner_reserve_profile import load_lifecycles_from_paths  # noqa: E402
from src.pipeline.tail_capture_strategy import _portfolio_backtest, _token_time_splits  # noqa: E402


def _ints(raw: str) -> list[int]:
    return sorted({int(part.strip()) for part in str(raw).split(",") if part.strip()})


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", default="data/training/latest_bsc_20260914_31d_final")
    parser.add_argument("--dex-output", default="data/research/post_graduation_ohlcv_20260914_final.json")
    parser.add_argument("--horizons", default="3600,21600,86400")
    parser.add_argument("--delays", default="3,10,30,60")
    parser.add_argument("--output", default="docs/research/20260913-bsc-strategy-foundation/34-leader-follow-profile-20260914.json")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    paths = sorted(Path(args.lifecycle_dir).glob("lifecycle_*.jsonl"))
    lifecycles = load_lifecycles_from_paths(paths)
    dex_rows, dex_coverage = _load_dex_rows(Path(args.dex_output))
    lifecycles, merge_coverage = _merge_dex_lifecycles(lifecycles, dex_rows)
    horizons = _ints(args.horizons)
    delays = _ints(args.delays)
    rows = []
    for delay in delays:
        config = LeaderFollowConfig(follower_delay_seconds=delay)
        candidates = build_leader_follow_candidates(lifecycles, config)
        splits = _token_time_splits(candidates, max(horizons, default=3600)) if candidates else {"train": [], "validation": [], "final": []}
        for horizon in horizons:
            outcomes = [simulate_leader_follow(candidate, horizon_seconds=horizon, config=config) for candidate in candidates]
            scores = [0.0] * len(candidates)
            split_rows = {}
            for name, indices in splits.items():
                split_rows[name] = {
                    "candidate_count": len(indices),
                    "leader_token_count": len({candidates[index]["token"] for index in indices}),
                    "portfolio": _portfolio_backtest(candidates, outcomes, scores, indices, config.replay, -1e30),
                    "complete_only": _portfolio_backtest(
                        candidates,
                        outcomes,
                        scores,
                        [index for index in indices if outcomes[index].get("complete")],
                        config.replay,
                        -1e30,
                    ),
                }
            rows.append({
                "follower_delay_seconds": delay,
                "horizon_seconds": horizon,
                "candidate_count": len(candidates),
                "unique_token_count": len({row["token"] for row in candidates}),
                "split_replay": split_rows,
                "leader_prior_token_distribution": {
                    "max": max((max((int(item.get("prior_token_count", 0)) for item in row.get("leaders") or []), default=0) for row in candidates), default=0),
                    "mean": (
                        sum(max((int(item.get("prior_token_count", 0)) for item in row.get("leaders") or []), default=0) for row in candidates) / len(candidates)
                        if candidates else 0.0
                    ),
                },
            })
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_type": "bsc_rule_based_leader_follow_profile",
        "inputs": {"lifecycle_dir": str(args.lifecycle_dir), "dex_output": str(args.dex_output), "horizons": horizons, "delays": delays},
        "data_coverage": {**dex_coverage, **merge_coverage},
        "rows": rows,
        "safe_for_live_switch": False,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "rows": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

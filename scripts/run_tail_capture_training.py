#!/usr/bin/env python3
"""Train and replay the tail-aware, multi-horizon BSC entry strategy."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline.runner_reserve_profile import load_lifecycles_from_paths  # noqa: E402
from src.pipeline.cross_boundary import build_cross_boundary_lifecycle  # noqa: E402
from src.pipeline.tail_capture_strategy import (  # noqa: E402
    TAIL_FEATURES,
    TailReplayConfig,
    train_tail_capture_experiment,
)


def _parse_ints(value: str) -> list[int]:
    return sorted({int(part.strip()) for part in str(value).split(",") if part.strip()})


def _load_dex_rows(path: Path) -> tuple[dict[str, dict], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = {}
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        token = str(row.get("token") or "").strip().lower()
        if token:
            rows[token] = row
    statuses = Counter(str(row.get("status") or "unknown") for row in rows.values())
    bars = sum(len(row.get("bars") or []) for row in rows.values() if row.get("status") == "ok")
    return rows, {
        "source_output": str(path),
        "as_of_timestamp": payload.get("as_of_timestamp"),
        "as_of_utc": payload.get("as_of_utc"),
        "graduated_input_count": int(payload.get("graduated_input_count", 0) or 0),
        "status_counts": dict(sorted(statuses.items())),
        "ok_token_count": int(statuses.get("ok", 0)),
        "ok_bar_count": int(bars),
        "proxy_enabled": bool(payload.get("proxy_enabled")),
    }


def _merge_dex_lifecycles(base: list[dict], dex_rows: dict[str, dict]) -> tuple[list[dict], dict]:
    merged = []
    enriched = 0
    graduated = 0
    for lifecycle in base:
        token = str(lifecycle.get("token_address") or lifecycle.get("token") or "").strip().lower()
        if lifecycle.get("graduated"):
            graduated += 1
        dex = dex_rows.get(token)
        if dex and dex.get("status") == "ok" and isinstance(dex.get("lifecycle"), dict):
            # Rebuild the bridge from the raw bars on every training run.  It
            # makes the dataset self-healing when a collector bug is fixed
            # after a resumable OHLCV file was written.
            rebuilt = build_cross_boundary_lifecycle(lifecycle, dex)
            lifecycle = dict(rebuilt or dex["lifecycle"])
            lifecycle["token_address"] = token
            enriched += 1
        merged.append(lifecycle)
    return merged, {
        "base_token_count": len(base),
        "graduated_token_count": graduated,
        "dex_enriched_token_count": enriched,
        "dex_enrichment_rate_among_graduated": float(enriched / graduated) if graduated else 0.0,
    }


def _load_market_context(path: Path | None) -> tuple[dict[str, dict], dict]:
    """Load daily BSC DEX volume as a causal market-regime covariate."""
    if path is None or not path.exists():
        return {}, {"market_context_path": str(path) if path else None, "market_context_days": 0}
    payload = json.loads(path.read_text(encoding="utf-8"))
    chart = list(payload.get("daily_chart_tail") or [])
    volumes = [float(row.get("volume_usd", 0.0) or 0.0) for row in chart]
    context = {}
    for index, row in enumerate(chart):
        # A decision made during this UTC date cannot know that date's final
        # aggregate.  Shift the feature by one day and compare that prior day
        # with the preceding seven-day baseline.
        volume = volumes[index - 1] if index > 0 else 0.0
        prior = volumes[max(0, index - 8):index - 1]
        average = sum(prior) / len(prior) if prior else volume
        context[str(row.get("date_utc"))] = {
            "volume_usd": volume,
            "vs_7d_avg": float(volume / average) if average > 0 else 1.0,
        }
    return context, {
        "market_context_path": str(path),
        "market_context_days": len(context),
        "market_context_source_url": payload.get("source_url"),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", default="data/training/latest_bsc_20260914_31d")
    parser.add_argument("--dex-output", required=True)
    parser.add_argument(
        "--market-metrics",
        default="docs/research/20260913-bsc-strategy-foundation/18-bsc-market-metrics-20260914.json",
        help="Fetched daily BSC DEX volume JSON used as a regime feature",
    )
    parser.add_argument("--horizons", default="3600,21600,86400,259200")
    parser.add_argument("--output-dir", default="data/models/20260914_tail_capture_v1")
    parser.add_argument("--report", default="data/research/tail_capture_training_20260914.json")
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
    parser.add_argument("--max-candidates-per-token", type=int, default=24)
    parser.add_argument("--positive-return-pct", type=float, default=100.0)
    parser.add_argument("--rank-target", choices=("utility", "mfe"), default="utility")
    parser.add_argument("--stop-loss-pct", type=float, default=None)
    parser.add_argument("--trailing-stop-pct", type=float, default=None)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    lifecycle_paths = sorted(Path(args.lifecycle_dir).glob("lifecycle_*.jsonl"))
    lifecycles = load_lifecycles_from_paths(lifecycle_paths)
    dex_rows, dex_coverage = _load_dex_rows(Path(args.dex_output))
    lifecycles, merge_coverage = _merge_dex_lifecycles(lifecycles, dex_rows)
    market_context, market_coverage = _load_market_context(Path(args.market_metrics) if args.market_metrics else None)
    config = TailReplayConfig(
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
        stop_loss_pct=args.stop_loss_pct,
        trailing_stop_pct=args.trailing_stop_pct,
    )
    horizons = _parse_ints(args.horizons)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_type": "bsc_tail_capture_multi_horizon",
        "inputs": {
            "lifecycle_dir": str(args.lifecycle_dir),
            "lifecycle_files": [str(path) for path in lifecycle_paths],
            "dex_output": str(args.dex_output),
            "horizons": horizons,
            "config": {key: getattr(config, key) for key in config.__dataclass_fields__},
            "feature_names": list(TAIL_FEATURES),
            "max_candidates_per_token": int(args.max_candidates_per_token),
        },
        "data_coverage": {**dex_coverage, **merge_coverage, **market_coverage},
        "horizons": {},
        "safe_for_live_switch": False,
    }
    for horizon in horizons:
        horizon_dir = Path(args.output_dir) / f"horizon_{int(horizon)}s"
        result = train_tail_capture_experiment(
            lifecycles,
            horizon_seconds=horizon,
            config=config,
            feature_names=TAIL_FEATURES,
            positive_return_pct=args.positive_return_pct,
            output_dir=horizon_dir,
            market_context=market_context,
            max_candidates_per_token=args.max_candidates_per_token,
            rank_target=args.rank_target,
        )
        report["horizons"][str(horizon)] = result
        print(
            "horizon=%ss status=%s candidates=%d complete=%d final_net=%s stress_final=%s"
            % (
                horizon,
                result.get("status"),
                result.get("inputs", {}).get("candidate_count", 0),
                result.get("path_coverage", {}).get("complete_count", 0),
                result.get("splits", {}).get("final", {}).get("portfolio", {}).get("net_profit_bnb", "n/a"),
                result.get("stress_replay", {}).get("harsh", {}).get("final", {}).get("net_profit_bnb", "n/a"),
            )
        )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

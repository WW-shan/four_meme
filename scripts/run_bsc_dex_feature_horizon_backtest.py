#!/usr/bin/env python3
"""Backtest DEX entry features and holding horizons without look-ahead.

The input OHLCV file is a point-in-time export from a DEX data provider.  The
first complete hourly bar after graduation supplies entry features; execution
uses the following bar open.  Thresholds are fitted on the chronological train
slice and evaluated on a later test slice.  This is a research diagnostic, not
a live model selector.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_LIFECYCLE_DIR = "data/training/bsc_month_latest_20260911_run7"
DEFAULT_OHLCV = ".ccg/tasks/bsc-retention-strategy-validation-20260913/research/graduated_dex_ohlcv.json"
DEFAULT_OUTPUT = "data/replay_reports/bsc_dex_feature_horizon_backtest.json"
DEFAULT_HORIZONS_HOURS = (1, 3, 6, 12, 24, 48, 72)
FEATURE_MODES = (
    "none",
    "momentum_positive",
    "volume_high",
    "range_high",
    "momentum_volume",
    "momentum_range",
    "volume_range",
    "all_three",
)
EXIT_MODES = ("fixed", "hard_stop_30", "trailing_30")
ENTRY_FEE_BPS = 100.0
ENTRY_SLIPPAGE_BPS = 200.0
EXIT_FEE_BPS = 100.0
EXIT_SLIPPAGE_BPS = 200.0


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _timestamp(value: Any) -> float | None:
    parsed = _finite(value)
    if parsed is not None:
        return parsed
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed_dt = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
    return parsed_dt.timestamp()


def _token(value: Any) -> str:
    return str(value or "").strip().lower()


def _load_lifecycles(lifecycle_dir: str | Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(lifecycle_dir).glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                token = _token(row.get("token_address") or row.get("token"))
                if not token:
                    continue
                old = rows.get(token)
                if old is None or len(row.get("price_history") or []) >= len(old.get("price_history") or []):
                    rows[token] = dict(row)
    return rows


def _bar_rows(raw: Iterable[Any]) -> list[tuple[float, float, float, float, float, float]]:
    bars = []
    for row in raw or []:
        if not isinstance(row, Sequence) or len(row) < 6:
            continue
        values = [_finite(value) for value in row[:6]]
        if any(value is None for value in values):
            continue
        timestamp, opening, high, low, close, volume = values
        if timestamp is None or opening is None or high is None or low is None or close is None or volume is None:
            continue
        if timestamp <= 0.0 or opening <= 0.0 or high <= 0.0 or low <= 0.0 or close <= 0.0 or volume < 0.0:
            continue
        bars.append((timestamp, opening, high, low, close, volume))
    return sorted(set(bars), key=lambda row: row[0])


def _load_ohlcv(path: str | Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, Mapping) else None
    result: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        token = _token(row.get("token") or (row.get("pair") or {}).get("baseToken", {}).get("address"))
        if not token:
            continue
        bars = _bar_rows(row.get("ohlcv"))
        if bars:
            result[token] = {
                "token": token,
                "symbol": row.get("symbol") or (row.get("pair") or {}).get("baseToken", {}).get("symbol"),
                "pair_address": row.get("pair_address") or (row.get("pair") or {}).get("pairAddress"),
                "pair": row.get("pair") or {},
                "bars": bars,
            }
    return result


def build_episodes(
    lifecycles: Mapping[str, Mapping[str, Any]],
    ohlcv_rows: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    episodes = []
    for token, row in ohlcv_rows.items():
        lifecycle = lifecycles.get(token)
        if not lifecycle or not lifecycle.get("graduated"):
            continue
        graduation = _timestamp(lifecycle.get("graduate_time"))
        bars = list(row.get("bars") or [])
        if graduation is None or len(bars) < 3:
            continue
        after = [index for index, bar in enumerate(bars) if bar[0] >= graduation]
        if not after:
            continue
        decision_index = after[0]
        entry_index = decision_index + 1
        if entry_index >= len(bars):
            continue
        decision_bar = bars[decision_index]
        entry_bar = bars[entry_index]
        momentum = decision_bar[4] / decision_bar[1] - 1.0
        bar_range = (decision_bar[2] - decision_bar[3]) / decision_bar[1]
        episodes.append({
            "token": token,
            "symbol": row.get("symbol") or lifecycle.get("symbol"),
            "graduation_time": graduation,
            "decision_time": decision_bar[0],
            "entry_time": entry_bar[0],
            "entry_index": entry_index,
            "bars": bars,
            "momentum_1h": momentum,
            "range_1h": bar_range,
            "volume_1h": decision_bar[5],
        })
    return sorted(episodes, key=lambda row: (row["graduation_time"], row["token"]))


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = round((len(ordered) - 1) * float(probability))
    return ordered[max(0, min(len(ordered) - 1, index))]


def _feature_thresholds(episodes: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    return {
        "momentum_median": _quantile([float(row["momentum_1h"]) for row in episodes], 0.5),
        "range_median": _quantile([float(row["range_1h"]) for row in episodes], 0.5),
        "volume_median": _quantile([float(row["volume_1h"]) for row in episodes], 0.5),
    }


def _feature_accepts(mode: str, episode: Mapping[str, Any], thresholds: Mapping[str, float]) -> bool:
    momentum = float(episode["momentum_1h"])
    bar_range = float(episode["range_1h"])
    volume = float(episode["volume_1h"])
    positive_momentum = momentum > 0.0
    high_momentum = momentum >= float(thresholds.get("momentum_median", 0.0))
    high_range = bar_range >= float(thresholds.get("range_median", 0.0))
    high_volume = volume >= float(thresholds.get("volume_median", 0.0))
    if mode == "none":
        return True
    if mode == "momentum_positive":
        return positive_momentum
    if mode == "volume_high":
        return high_volume
    if mode == "range_high":
        return high_range
    if mode == "momentum_volume":
        return positive_momentum and high_volume
    if mode == "momentum_range":
        return positive_momentum and high_range
    if mode == "volume_range":
        return high_volume and high_range
    if mode == "all_three":
        return positive_momentum and high_volume and high_range
    raise ValueError(f"unsupported feature mode: {mode}")


def _endpoint_index(bars: Sequence[tuple[float, float, float, float, float, float]], entry_index: int, horizon_seconds: float) -> int | None:
    target = bars[entry_index][0] + float(horizon_seconds)
    for index in range(entry_index + 1, len(bars)):
        if bars[index][0] >= target:
            return index
    return None


def simulate_episode(
    episode: Mapping[str, Any],
    *,
    horizon_seconds: int,
    exit_mode: str,
    entry_fee_bps: float = ENTRY_FEE_BPS,
    entry_slippage_bps: float = ENTRY_SLIPPAGE_BPS,
    exit_fee_bps: float = EXIT_FEE_BPS,
    exit_slippage_bps: float = EXIT_SLIPPAGE_BPS,
) -> dict[str, Any]:
    bars = episode["bars"]
    entry_index = int(episode["entry_index"])
    endpoint_index = _endpoint_index(bars, entry_index, horizon_seconds)
    if endpoint_index is None:
        return {"status": "incomplete_horizon", "horizon_seconds": int(horizon_seconds)}
    entry_price = float(bars[entry_index][1])
    peak = entry_price
    exit_index = endpoint_index
    exit_reason = "horizon"
    if exit_mode in {"hard_stop_30", "trailing_30"}:
        for index in range(entry_index, endpoint_index):
            close = float(bars[index][4])
            peak = max(peak, close)
            threshold = entry_price * 0.70 if exit_mode == "hard_stop_30" else peak * 0.70
            if close <= threshold:
                candidate = index + 1
                if candidate <= endpoint_index:
                    exit_index = candidate
                    exit_reason = "hard_stop" if exit_mode == "hard_stop_30" else "trailing_stop"
                break
    exit_price = float(bars[exit_index][1])
    gross_multiple = exit_price / entry_price
    entry_cost = 1.0 + (float(entry_fee_bps) + float(entry_slippage_bps)) / 10_000.0
    exit_factor = max(0.0, 1.0 - (float(exit_fee_bps) + float(exit_slippage_bps)) / 10_000.0)
    net_multiple = gross_multiple * exit_factor / entry_cost
    return {
        "status": "ok",
        "horizon_seconds": int(horizon_seconds),
        "entry_time": float(bars[entry_index][0]),
        "exit_time": float(bars[exit_index][0]),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_return_pct": (gross_multiple - 1.0) * 100.0,
        "net_return_pct": (net_multiple - 1.0) * 100.0,
        "exit_reason": exit_reason,
    }


def _distribution(values: Iterable[float]) -> dict[str, float | int]:
    cleaned = [float(value) for value in values if math.isfinite(float(value))]
    if not cleaned:
        return {"count": 0, "mean": 0.0, "median": 0.0, "p10": 0.0, "positive_rate": 0.0}
    return {
        "count": len(cleaned),
        "mean": statistics.mean(cleaned),
        "median": statistics.median(cleaned),
        "p10": _quantile(cleaned, 0.10),
        "positive_rate": sum(value > 0.0 for value in cleaned) / len(cleaned),
    }


def summarize_outcomes(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    usable = [row for row in outcomes if row.get("status") == "ok"]
    return {
        "sample_count": len(usable),
        "incomplete_count": len(outcomes) - len(usable),
        "net_return_pct": _distribution(float(row["net_return_pct"]) for row in usable),
        "gross_return_pct": _distribution(float(row["gross_return_pct"]) for row in usable),
        "exit_reason_counts": dict(sorted(
            {reason: sum(1 for row in usable if row.get("exit_reason") == reason) for reason in {str(row.get("exit_reason")) for row in usable}}.items()
        )),
    }


def _split_episodes(episodes: Sequence[Mapping[str, Any]], train_fraction: float) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    if not episodes:
        return [], []
    cut = max(1, min(len(episodes) - 1, int(math.floor(len(episodes) * train_fraction)))) if len(episodes) > 1 else 1
    return list(episodes[:cut]), list(episodes[cut:])


def run_grid(
    episodes: Sequence[Mapping[str, Any]],
    *,
    horizons_hours: Sequence[int],
    train_fraction: float = 0.70,
    min_train_samples: int = 10,
    fee_bps: float = ENTRY_FEE_BPS,
    slippage_bps: float = ENTRY_SLIPPAGE_BPS,
) -> dict[str, Any]:
    train, test = _split_episodes(episodes, train_fraction)
    reports: dict[str, Any] = {}
    selected: dict[str, Any] = {}
    for horizon_hours in horizons_hours:
        horizon_seconds = int(horizon_hours) * 3600
        reports[str(horizon_hours)] = {}
        for exit_mode in EXIT_MODES:
            reports[str(horizon_hours)][exit_mode] = {}
            candidates = []
            for feature_mode in FEATURE_MODES:
                thresholds = _feature_thresholds(train)
                train_selected = [row for row in train if _feature_accepts(feature_mode, row, thresholds)]
                test_selected = [row for row in test if _feature_accepts(feature_mode, row, thresholds)]
                train_outcomes = [
                    simulate_episode(
                        row,
                        horizon_seconds=horizon_seconds,
                        exit_mode=exit_mode,
                        entry_fee_bps=fee_bps,
                        entry_slippage_bps=slippage_bps,
                        exit_fee_bps=fee_bps,
                        exit_slippage_bps=slippage_bps,
                    )
                    for row in train_selected
                ]
                test_outcomes = [
                    simulate_episode(
                        row,
                        horizon_seconds=horizon_seconds,
                        exit_mode=exit_mode,
                        entry_fee_bps=fee_bps,
                        entry_slippage_bps=slippage_bps,
                        exit_fee_bps=fee_bps,
                        exit_slippage_bps=slippage_bps,
                    )
                    for row in test_selected
                ]
                train_summary = summarize_outcomes(train_outcomes)
                test_summary = summarize_outcomes(test_outcomes)
                candidate = {
                    "feature_mode": feature_mode,
                    "thresholds": thresholds,
                    "train_selected_count": len(train_selected),
                    "test_selected_count": len(test_selected),
                    "train": train_summary,
                    "test": test_summary,
                }
                candidates.append(candidate)
            eligible_candidates = [
                row
                for row in candidates
                if int(row["train"]["net_return_pct"]["count"]) >= int(min_train_samples)
            ]
            selected_candidate = max(
                eligible_candidates or candidates,
                key=lambda row: (
                    float(row["train"]["net_return_pct"]["mean"]),
                    float(row["train"]["net_return_pct"]["median"]),
                    int(row["train"]["net_return_pct"]["count"]),
                ),
            )
            reports[str(horizon_hours)][exit_mode] = {
                "candidates": candidates,
                "selected_by_train": selected_candidate,
                "selection_eligible_count": len(eligible_candidates),
                "minimum_train_samples": int(min_train_samples),
            }
            selected[f"{horizon_hours}h/{exit_mode}"] = {
                "feature_mode": selected_candidate["feature_mode"],
                "train_net_mean_pct": selected_candidate["train"]["net_return_pct"]["mean"],
                "test_net_mean_pct": selected_candidate["test"]["net_return_pct"]["mean"],
                "test_net_median_pct": selected_candidate["test"]["net_return_pct"]["median"],
                "test_sample_count": selected_candidate["test"]["net_return_pct"]["count"],
            }
    return {
        "split": {
            "train_fraction": float(train_fraction),
            "train_count": len(train),
            "test_count": len(test),
            "train_last_graduation": train[-1]["graduation_time"] if train else None,
            "test_first_graduation": test[0]["graduation_time"] if test else None,
        },
        "horizons": reports,
        "selected_by_train": selected,
    }


def _hours(raw: str) -> list[int]:
    values = sorted({int(part.strip()) for part in str(raw).split(",") if part.strip()})
    if not values or any(value <= 0 for value in values):
        raise argparse.ArgumentTypeError("horizons must contain positive integers")
    return values


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", default=DEFAULT_LIFECYCLE_DIR)
    parser.add_argument("--ohlcv", default=DEFAULT_OHLCV)
    parser.add_argument("--horizons-hours", type=_hours, default=list(DEFAULT_HORIZONS_HOURS))
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--min-train-samples", type=int, default=10)
    parser.add_argument("--fee-bps", type=float, default=ENTRY_FEE_BPS)
    parser.add_argument("--slippage-bps", type=float, default=ENTRY_SLIPPAGE_BPS)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def build_report(args) -> dict[str, Any]:
    lifecycles = _load_lifecycles(args.lifecycle_dir)
    ohlcv_rows = _load_ohlcv(args.ohlcv)
    episodes = build_episodes(lifecycles, ohlcv_rows)
    grid = run_grid(
        episodes,
        horizons_hours=args.horizons_hours,
        train_fraction=float(args.train_fraction),
        min_train_samples=int(args.min_train_samples),
        fee_bps=float(args.fee_bps),
        slippage_bps=float(args.slippage_bps),
    )
    return {
        "schema_version": 1,
        "profile_type": "bsc_dex_feature_horizon_walk_forward_backtest",
        "inputs": {
            "lifecycle_dir": str(args.lifecycle_dir),
            "ohlcv": str(args.ohlcv),
            "lifecycle_token_count": len(lifecycles),
            "ohlcv_token_count": len(ohlcv_rows),
            "episode_count": len(episodes),
            "horizons_hours": list(args.horizons_hours),
            "min_train_samples": int(args.min_train_samples),
            "fee_bps_each_side": float(args.fee_bps),
            "slippage_bps_each_side": float(args.slippage_bps),
            "costs_bps_each_side": {
                "fee": float(args.fee_bps),
                "slippage": float(args.slippage_bps),
            },
        },
        "coverage": {
            "graduated_lifecycle_count": sum(1 for row in lifecycles.values() if row.get("graduated")),
            "matched_ohlcv_count": sum(1 for token in ohlcv_rows if token in lifecycles),
            "episode_count": len(episodes),
            "survivorship_and_provider_bias_warning": True,
        },
        "grid": grid,
        "decision": "research_only_do_not_promote_without_raw_chain_and_future_validation",
    }


def main(argv=None) -> int:
    args = parse_args(argv)
    if not 0.5 <= float(args.train_fraction) < 1.0:
        raise SystemExit("--train-fraction must be >= 0.5 and < 1.0")
    if int(args.min_train_samples) <= 0:
        raise SystemExit("--min-train-samples must be positive")
    if float(args.fee_bps) < 0.0 or float(args.slippage_bps) < 0.0:
        raise SystemExit("--fee-bps and --slippage-bps must be non-negative")
    report = build_report(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    for key, row in report["grid"]["selected_by_train"].items():
        print(
            "%s feature=%s train_mean=%.3f%% test_mean=%.3f%% test_median=%.3f%% test_n=%d"
            % (
                key,
                row["feature_mode"],
                row["train_net_mean_pct"],
                row["test_net_mean_pct"],
                row["test_net_median_pct"],
                row["test_sample_count"],
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

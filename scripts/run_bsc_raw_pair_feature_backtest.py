#!/usr/bin/env python3
"""Backtest raw BSC pair logs with DEX flow and liquidity features.

This tool consumes ``Swap`` and ``Sync`` logs fetched from a BSC RPC.  It
keeps block timestamp and block/log ordering in the derived hourly bars.  The
current public RPC snapshot may not expose a full month of archive logs, so
coverage is reported explicitly instead of being silently filled.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_bsc_dex_feature_horizon_backtest import (  # noqa: E402
    _feature_thresholds,
    _split_episodes,
    simulate_episode,
    summarize_outcomes)


DEFAULT_LIFECYCLE_DIR = "data/training/bsc_month_latest_20260911_run7"
DEFAULT_PAIR_TOKENS = ".ccg/tasks/bsc-retention-strategy-validation-20260913/research/pair_tokens.json"
DEFAULT_RAW_LOGS = ".ccg/tasks/bsc-retention-strategy-validation-20260913/research/bsc_pair_logs_recent.json"
DEFAULT_OUTPUT = "data/replay_reports/bsc_raw_pair_feature_backtest.json"
RAW_FEATURE_MODES = (
    "none",
    "flow_positive",
    "volume_liquidity_high",
    "liquidity_high",
    "new_traders_high",
    "flow_volume",
    "all_health",
)
RAW_QUOTE_ADDRESSES = {
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": "WBNB",
    "0x55d398326f99059ff775485246999027b3197955": "USDT",
    "0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d": "USD1",
}
SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
SYNC_TOPIC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


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


def _int_hex(value: Any) -> int:
    if isinstance(value, int):
        return value
    text = str(value or "0")
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return 0


def _topic_address(value: Any) -> str:
    text = str(value or "").lower()
    return "0x" + text[-40:] if len(text) >= 40 else ""


def _words(data: Any, count: int) -> list[int] | None:
    text = str(data or "")
    if text.startswith("0x"):
        text = text[2:]
    if len(text) < count * 64:
        return None
    try:
        return [int(text[index:index + 64], 16) for index in range(0, count * 64, 64)]
    except ValueError:
        return None


def _load_lifecycles(path: str | Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for lifecycle_path in sorted(Path(path).glob("*.jsonl")):
        with lifecycle_path.open("r", encoding="utf-8") as handle:
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


def _load_pair_tokens(path: str | Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        _token(token): dict(row)
        for token, row in (payload or {}).items()
        if isinstance(row, Mapping) and _token(token)
    }


def _load_raw_logs(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    events = [row for row in payload.get("events", []) if isinstance(row, Mapping)]
    events.sort(key=lambda row: (_int_hex(row.get("blockNumber")), _int_hex(row.get("logIndex"))))
    return {"payload": payload, "events": events}


def _pair_bars(
    events: Sequence[Mapping[str, Any]],
    *,
    pair_meta: Mapping[str, Any],
    token_meta: Mapping[str, Any],
) -> tuple[list[tuple[float, float, float, float, float, float]], dict[str, Any]]:
    token = _token(token_meta.get("base_token"))
    token0 = _token(token_meta.get("token0"))
    token1 = _token(token_meta.get("token1"))
    base_is_token0 = token0 == token
    quote_address = token1 if base_is_token0 else token0
    quote_symbol = RAW_QUOTE_ADDRESSES.get(quote_address, quote_address)
    reserves0 = reserves1 = None
    buckets: dict[int, dict[str, Any]] = {}
    first_seen_trader: dict[str, int] = {}
    for row in events:
        timestamp = _int_hex(row.get("blockTimestamp"))
        if timestamp <= 0:
            continue
        bucket_time = (timestamp // 3600) * 3600
        bucket = buckets.setdefault(
            bucket_time,
            {
                "timestamp": bucket_time,
                "open": None,
                "high": None,
                "low": None,
                "close": None,
                "volume": 0.0,
                "buy_volume": 0.0,
                "sell_volume": 0.0,
                "event_count": 0,
                "traders": set(),
                "new_traders": 0,
                "reserves0": reserves0,
                "reserves1": reserves1,
            },
        )
        topic0 = str((row.get("topics") or [""])[0]).lower()
        if topic0 == SYNC_TOPIC:
            decoded = _words(row.get("data"), 2)
            if decoded is None:
                continue
            reserves0, reserves1 = decoded
            bucket["reserves0"] = reserves0
            bucket["reserves1"] = reserves1
            continue
        if topic0 != SWAP_TOPIC:
            continue
        decoded = _words(row.get("data"), 4)
        topics = row.get("topics") or []
        if decoded is None or len(topics) < 3:
            continue
        amount0_in, amount1_in, amount0_out, amount1_out = decoded
        if base_is_token0:
            base_in, quote_in, base_out, quote_out = amount0_in, amount1_in, amount0_out, amount1_out
        else:
            base_in, quote_in, base_out, quote_out = amount1_in, amount0_in, amount1_out, amount0_out
        base_delta = base_out - base_in
        quote_delta = quote_in - quote_out
        if base_delta == 0 or quote_delta == 0 or base_delta * quote_delta <= 0:
            continue
        price = abs(quote_delta / base_delta)
        quote_volume = abs(quote_delta) / 1e18
        if not math.isfinite(price) or price <= 0.0 or not math.isfinite(quote_volume):
            continue
        bucket["open"] = price if bucket["open"] is None else bucket["open"]
        bucket["high"] = price if bucket["high"] is None else max(bucket["high"], price)
        bucket["low"] = price if bucket["low"] is None else min(bucket["low"], price)
        bucket["close"] = price
        bucket["volume"] += quote_volume
        if base_delta > 0:
            bucket["buy_volume"] += quote_volume
        else:
            bucket["sell_volume"] += quote_volume
        bucket["event_count"] += 1
        trader = _topic_address(topics[2])
        if trader:
            bucket["traders"].add(trader)
            if trader not in first_seen_trader:
                first_seen_trader[trader] = timestamp
                bucket["new_traders"] += 1
    bars = []
    for timestamp in sorted(buckets):
        bucket = buckets[timestamp]
        if bucket["open"] is None or bucket["close"] is None:
            continue
        liquidity_quote = None
        if bucket["reserves0"] is not None and bucket["reserves1"] is not None:
            quote_reserve = bucket["reserves0"] if not base_is_token0 else bucket["reserves1"]
            liquidity_quote = 2.0 * float(quote_reserve) / 1e18
        bucket_volume = float(bucket["volume"])
        sell_pressure = (
            float(bucket["sell_volume"]) / bucket_volume
            if bucket_volume > 0.0
            else 0.0
        )
        bucket["liquidity_quote"] = liquidity_quote
        bucket["sell_pressure"] = sell_pressure
        bucket["new_traders"] = int(bucket["new_traders"])
        # The generic simulator consumes OHLCV-shaped tuples; the richer
        # fields remain attached to the episode for feature selection.
        bars.append((
            float(timestamp),
            float(bucket["open"]),
            float(bucket["high"]),
            float(bucket["low"]),
            float(bucket["close"]),
            bucket_volume,
        ))
    return bars, {
        "quote_address": quote_address,
        "quote_symbol": quote_symbol,
        "buckets": buckets,
        "pair_address": pair_meta.get("pair_address"),
    }


def build_raw_episodes(
    lifecycles: Mapping[str, Mapping[str, Any]],
    pair_tokens: Mapping[str, Mapping[str, Any]],
    raw_payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events_by_pair: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in raw_payload.get("events", []) or []:
        address = str(row.get("address") or "").lower()
        if address:
            events_by_pair[address].append(row)
    pair_meta_by_address = {
        str(address).lower(): value
        for address, value in (raw_payload.get("payload", {}).get("pairs", {}) or {}).items()
        if isinstance(value, Mapping)
    }
    episodes: list[dict[str, Any]] = []
    skipped = Counter()
    observed_timestamps = []
    for token, token_meta in pair_tokens.items():
        pair_address = str(token_meta.get("pair_address") or "").lower()
        pair_events = events_by_pair.get(pair_address, [])
        if not pair_events:
            skipped["no_raw_pair_events"] += 1
            continue
        lifecycle = lifecycles.get(token)
        if not lifecycle or not lifecycle.get("graduated"):
            skipped["not_graduated_or_missing_lifecycle"] += 1
            continue
        graduation = _timestamp(lifecycle.get("graduate_time"))
        if graduation is None:
            skipped["missing_graduation_time"] += 1
            continue
        observed_timestamps.extend(
            _int_hex(row.get("blockTimestamp"))
            for row in pair_events
            if _int_hex(row.get("blockTimestamp")) > 0
        )
        bars, rich = _pair_bars(
            pair_events,
            pair_meta=pair_meta_by_address.get(pair_address, {"pair_address": pair_address}),
            token_meta=token_meta,
        )
        if len(bars) < 3:
            skipped["fewer_than_three_hourly_bars"] += 1
            continue
        first_observed = bars[0][0]
        if graduation < first_observed - 3600.0:
            skipped["graduation_before_raw_log_window"] += 1
            continue
        after = [index for index, bar in enumerate(bars) if bar[0] >= graduation]
        if not after or after[0] + 1 >= len(bars):
            skipped["no_entry_bar_after_graduation"] += 1
            continue
        decision_index = after[0]
        entry_index = decision_index + 1
        decision_bucket = rich["buckets"].get(int(bars[decision_index][0])) or {}
        liquidity_quote = decision_bucket.get("liquidity_quote")
        episodes.append({
            "token": token,
            "symbol": lifecycle.get("symbol"),
            "graduation_time": graduation,
            "decision_time": bars[decision_index][0],
            "entry_time": bars[entry_index][0],
            "entry_index": entry_index,
            "bars": bars,
            "momentum_1h": bars[decision_index][4] / bars[decision_index][1] - 1.0,
            "range_1h": (bars[decision_index][2] - bars[decision_index][3]) / bars[decision_index][1],
            "volume_1h": bars[decision_index][5],
            "sell_pressure_1h": float(decision_bucket.get("sell_pressure") or 0.0),
            "new_traders_1h": int(decision_bucket.get("new_traders") or 0),
            "liquidity_quote": liquidity_quote,
            "quote_symbol": rich["quote_symbol"],
            "pair_address": pair_address,
        })
    coverage = {
        "raw_pair_count": len(events_by_pair),
        "episode_count": len(episodes),
        "skipped_counts": dict(sorted(skipped.items())),
        "raw_event_min_timestamp": min(observed_timestamps) if observed_timestamps else None,
        "raw_event_max_timestamp": max(observed_timestamps) if observed_timestamps else None,
        "raw_event_min_time": (
            datetime.fromtimestamp(min(observed_timestamps), timezone.utc).isoformat()
            if observed_timestamps else None
        ),
        "raw_event_max_time": (
            datetime.fromtimestamp(max(observed_timestamps), timezone.utc).isoformat()
            if observed_timestamps else None
        ),
    }
    return sorted(episodes, key=lambda row: (row["graduation_time"], row["token"])), coverage


def _raw_feature_thresholds(episodes: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    values = {
        "momentum_median": [float(row["momentum_1h"]) for row in episodes],
        "volume_median": [float(row["volume_1h"]) for row in episodes],
        "liquidity_median": [
            float(row["liquidity_quote"])
            for row in episodes
            if row.get("liquidity_quote") is not None and float(row["liquidity_quote"]) >= 0.0
        ],
        "new_traders_median": [float(row["new_traders_1h"]) for row in episodes],
    }
    thresholds = _feature_thresholds(episodes)
    thresholds["liquidity_median"] = statistics.median(values["liquidity_median"]) if values["liquidity_median"] else 0.0
    thresholds["new_traders_median"] = statistics.median(values["new_traders_median"]) if values["new_traders_median"] else 0.0
    return thresholds


def _raw_feature_accepts(mode: str, episode: Mapping[str, Any], thresholds: Mapping[str, float]) -> bool:
    sell_pressure = float(episode.get("sell_pressure_1h") or 0.0)
    volume = float(episode.get("volume_1h") or 0.0)
    liquidity = episode.get("liquidity_quote")
    liquidity = float(liquidity) if liquidity is not None else 0.0
    new_traders = float(episode.get("new_traders_1h") or 0.0)
    flow_positive = sell_pressure <= 0.55
    volume_high = volume >= float(thresholds.get("volume_median", 0.0))
    liquidity_high = liquidity >= float(thresholds.get("liquidity_median", 0.0))
    new_traders_high = new_traders >= float(thresholds.get("new_traders_median", 0.0))
    if mode == "none":
        return True
    if mode == "flow_positive":
        return flow_positive
    if mode == "volume_liquidity_high":
        return volume_high and liquidity_high
    if mode == "liquidity_high":
        return liquidity_high
    if mode == "new_traders_high":
        return new_traders_high
    if mode == "flow_volume":
        return flow_positive and volume_high
    if mode == "all_health":
        return flow_positive and volume_high and liquidity_high and new_traders_high
    raise ValueError(f"unsupported raw feature mode: {mode}")


def run_raw_grid(
    episodes: Sequence[Mapping[str, Any]],
    *,
    horizons_hours: Sequence[int],
    train_fraction: float = 0.70,
    min_train_samples: int = 5,
) -> dict[str, Any]:
    train, test = _split_episodes(episodes, train_fraction)
    reports: dict[str, Any] = {}
    selected: dict[str, Any] = {}
    thresholds = _raw_feature_thresholds(train)
    for horizon_hours in horizons_hours:
        horizon_seconds = int(horizon_hours) * 3600
        reports[str(horizon_hours)] = {}
        for exit_mode in ("fixed", "hard_stop_30", "trailing_30"):
            candidates = []
            for feature_mode in RAW_FEATURE_MODES:
                train_selected = [row for row in train if _raw_feature_accepts(feature_mode, row, thresholds)]
                test_selected = [row for row in test if _raw_feature_accepts(feature_mode, row, thresholds)]
                train_outcomes = [simulate_episode(row, horizon_seconds=horizon_seconds, exit_mode=exit_mode) for row in train_selected]
                test_outcomes = [simulate_episode(row, horizon_seconds=horizon_seconds, exit_mode=exit_mode) for row in test_selected]
                candidates.append({
                    "feature_mode": feature_mode,
                    "thresholds": thresholds,
                    "train_selected_count": len(train_selected),
                    "test_selected_count": len(test_selected),
                    "train": summarize_outcomes(train_outcomes),
                    "test": summarize_outcomes(test_outcomes),
                })
            eligible = [row for row in candidates if row["train"]["net_return_pct"]["count"] >= min_train_samples]
            selected_candidate = max(
                eligible or candidates,
                key=lambda row: (
                    float(row["train"]["net_return_pct"]["mean"]),
                    float(row["train"]["net_return_pct"]["median"]),
                ),
            )
            reports[str(horizon_hours)][exit_mode] = {
                "candidates": candidates,
                "selected_by_train": selected_candidate,
                "selection_eligible_count": len(eligible),
                "minimum_train_samples": min_train_samples,
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
            "train_fraction": train_fraction,
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
    parser.add_argument("--pair-tokens", default=DEFAULT_PAIR_TOKENS)
    parser.add_argument("--raw-logs", default=DEFAULT_RAW_LOGS)
    parser.add_argument("--horizons-hours", type=_hours, default=[1, 3, 6, 12, 24, 48, 72])
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--min-train-samples", type=int, default=5)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def build_report(args) -> dict[str, Any]:
    lifecycles = _load_lifecycles(args.lifecycle_dir)
    pair_tokens = _load_pair_tokens(args.pair_tokens)
    raw = _load_raw_logs(args.raw_logs)
    episodes, coverage = build_raw_episodes(lifecycles, pair_tokens, raw)
    grid = run_raw_grid(
        episodes,
        horizons_hours=args.horizons_hours,
        train_fraction=float(args.train_fraction),
        min_train_samples=int(args.min_train_samples),
    )
    raw_payload = raw.get("payload") or {}
    return {
        "schema_version": 1,
        "profile_type": "bsc_raw_pair_feature_horizon_walk_forward_backtest",
        "inputs": {
            "lifecycle_dir": str(args.lifecycle_dir),
            "pair_tokens": str(args.pair_tokens),
            "raw_logs": str(args.raw_logs),
            "lifecycle_token_count": len(lifecycles),
            "graduated_lifecycle_count": sum(1 for row in lifecycles.values() if row.get("graduated")),
            "pair_token_count": len(pair_tokens),
            "raw_event_count": len(raw.get("events") or []),
            "rpc": raw_payload.get("rpc"),
            "start_block": raw_payload.get("start_block"),
            "end_block": raw_payload.get("end_block"),
            "rpc_errors": raw_payload.get("errors") or [],
            "horizons_hours": list(args.horizons_hours),
            "min_train_samples": int(args.min_train_samples),
            "costs_bps_each_side": {"fee": 100.0, "slippage": 200.0},
        },
        "coverage": coverage,
        "grid": grid,
        "decision": "research_only_partial_rpc_archive_coverage",
    }


def main(argv=None) -> int:
    args = parse_args(argv)
    if not 0.5 <= float(args.train_fraction) < 1.0:
        raise SystemExit("--train-fraction must be >= 0.5 and < 1.0")
    if int(args.min_train_samples) <= 0:
        raise SystemExit("--min-train-samples must be positive")
    report = build_report(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    print(
        "episodes=%d raw_events=%d skipped=%s"
        % (
            report["coverage"]["episode_count"],
            report["inputs"]["raw_event_count"],
            report["coverage"]["skipped_counts"],
        )
    )
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

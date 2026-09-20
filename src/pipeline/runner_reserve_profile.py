"""Read-only replay of a two-stage runner-reserve exit policy.

This module deliberately uses raw lifecycle price paths only. It is a strategy
profile, not a trainer and not evidence for switching live trading. Entries are
anchored to the first point-in-time activity gate and every outcome requires a
complete future horizon.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.pipeline import reentry_probe


@dataclass(frozen=True)
class RunnerReplayConfig:
    entry_delay_seconds: int = 3
    exit_delay_seconds: int = 3
    fee_bps: float = 100.0
    slippage_bps: float = 200.0
    stop_loss_pct: float = -30.0
    activation_return_pct: float = 100.0
    partial_exit_ratio: float = 0.70
    reserve_drawdown_pct: float = 30.0
    reserve_floor_return_pct: float = 0.0
    min_entry_unique_buyers: int = 3
    min_entry_buy_count: int = 5
    max_entry_age_seconds: int = 300

    def __post_init__(self) -> None:
        if self.entry_delay_seconds < 0 or self.exit_delay_seconds < 0:
            raise ValueError("execution delays must be non-negative")
        if self.fee_bps < 0.0 or self.slippage_bps < 0.0:
            raise ValueError("execution costs must be non-negative")
        if self.stop_loss_pct >= 0.0:
            raise ValueError("stop_loss_pct must be negative")
        if self.activation_return_pct <= 0.0:
            raise ValueError("activation_return_pct must be positive")
        if not 0.0 < self.partial_exit_ratio < 1.0:
            raise ValueError("partial_exit_ratio must be between 0 and 1")
        if not 0.0 < self.reserve_drawdown_pct < 100.0:
            raise ValueError("reserve_drawdown_pct must be between 0 and 100")
        if self.min_entry_unique_buyers < 1 or self.min_entry_buy_count < 1:
            raise ValueError("entry activity gates must be positive")


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _timestamp(value: Any) -> float | None:
    if isinstance(value, datetime):
        return value.timestamp()
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


def _event_time(row: Mapping[str, Any]) -> float | None:
    return _timestamp(row.get("timestamp", row.get("time")))


def _price_path(lifecycle: Mapping[str, Any]) -> list[reentry_probe.PricePoint]:
    path = []
    seen = set()
    for row in lifecycle.get("price_history") or []:
        if not isinstance(row, Mapping):
            continue
        timestamp = _event_time(row)
        price = _finite(row.get("price"))
        if timestamp is None or price is None or price <= 0.0:
            continue
        key = (timestamp, price)
        if key in seen:
            continue
        seen.add(key)
        path.append(reentry_probe.PricePoint(datetime.fromtimestamp(timestamp, tz=timezone.utc), price, str(row.get("type") or "")))
    return sorted(path, key=lambda point: point.time)


def _price_at_or_before(path: Sequence[reentry_probe.PricePoint], timestamp: float) -> float | None:
    prices = [float(point.price) for point in path if _timestamp(point.time) is not None and _timestamp(point.time) <= timestamp]
    return prices[-1] if prices else None


def _point_at_or_after(path: Sequence[reentry_probe.PricePoint], timestamp: float) -> reentry_probe.PricePoint | None:
    for point in path:
        point_time = _timestamp(point.time)
        if point_time is not None and point_time >= timestamp and float(point.price) > 0.0:
            return point
    return None


def _lifecycle_event_identity(event: Mapping[str, Any]) -> str:
    """Deduplicate repeated snapshot events while retaining provenance."""
    payload = {
        "timestamp": _timestamp(event.get("timestamp", event.get("time"))),
        "account": str(event.get("account") or "").lower(),
        "token_amount": event.get("token_amount"),
        "bnb_amount": event.get("bnb_amount", event.get("ether_amount")),
        "price": event.get("price"),
        "type": str(event.get("type") or ""),
        "block_number": event.get("block_number", event.get("blockNumber")),
        "log_index": event.get("log_index", event.get("logIndex")),
        "transaction_hash": str(
            event.get("transaction_hash", event.get("transactionHash")) or ""
        ).lower(),
    }
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))


def _merge_lifecycle_events(existing: Any, incoming: Any) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for event in list(existing or []) + list(incoming or []):
        if not isinstance(event, Mapping):
            continue
        identity = _lifecycle_event_identity(event)
        if identity and identity not in merged:
            merged[identity] = dict(event)
    return sorted(
        merged.values(),
        key=lambda event: (
            _timestamp(event.get("timestamp", event.get("time"))) or 0.0,
            int(event.get("block_number", event.get("blockNumber", -1)) or -1),
            int(event.get("log_index", event.get("logIndex", -1)) or -1),
            str(event.get("transaction_hash", event.get("transactionHash")) or ""),
        ),
    )


def _merge_lifecycle_rows(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    """Merge cumulative lifecycle snapshots and reactivation fragments."""
    old_count = len(existing.get("buys") or []) + len(existing.get("sells") or [])
    new_count = len(incoming.get("buys") or []) + len(incoming.get("sells") or [])
    merged = dict(incoming if new_count >= old_count else existing)
    merged["buys"] = _merge_lifecycle_events(existing.get("buys"), incoming.get("buys"))
    merged["sells"] = _merge_lifecycle_events(existing.get("sells"), incoming.get("sells"))
    merged["price_history"] = _merge_lifecycle_events(
        existing.get("price_history"), incoming.get("price_history")
    )
    timestamps = [
        _timestamp(event.get("timestamp", event.get("time")))
        for event in (merged["buys"] + merged["sells"] + merged["price_history"])
    ]
    timestamps = [value for value in timestamps if value is not None]
    create_values = [
        _timestamp(existing.get("create_timestamp", existing.get("created_at"))),
        _timestamp(incoming.get("create_timestamp", incoming.get("created_at"))),
    ]
    create_values = [value for value in create_values if value is not None]
    if create_values:
        merged["create_timestamp"] = int(min(create_values))
        timestamps.extend(create_values)
    previous_updates = [_timestamp(existing.get("last_update")), _timestamp(incoming.get("last_update"))]
    timestamps.extend(value for value in previous_updates if value is not None)
    if timestamps:
        merged["last_update"] = int(max(timestamps))
    merged["graduated"] = bool(existing.get("graduated") or incoming.get("graduated"))
    graduate_times = [_timestamp(existing.get("graduate_time")), _timestamp(incoming.get("graduate_time"))]
    graduate_times = [value for value in graduate_times if value is not None]
    if graduate_times:
        merged["graduate_time"] = int(max(graduate_times))
    return merged


def _effective_buy_price(raw_price: float, config: RunnerReplayConfig) -> float:
    return raw_price * (1.0 + (config.fee_bps + config.slippage_bps) / 10_000.0)


def _effective_sell_multiple(raw_price: float, entry_price: float, config: RunnerReplayConfig) -> float:
    cost_factor = max(0.0, 1.0 - (config.fee_bps + config.slippage_bps) / 10_000.0)
    return raw_price * cost_factor / entry_price


def _entry_candidate(lifecycle: Mapping[str, Any], config: RunnerReplayConfig) -> dict[str, Any] | None:
    create_time = _timestamp(lifecycle.get("create_timestamp", lifecycle.get("created_at")))
    if create_time is None:
        return None
    buys = [row for row in lifecycle.get("buys") or [] if isinstance(row, Mapping)]
    buys = sorted((row for row in buys if _event_time(row) is not None), key=lambda row: _event_time(row) or 0.0)
    buyers: set[str] = set()
    sample_time = None
    for buy_count, row in enumerate(buys, start=1):
        timestamp = _event_time(row)
        if timestamp is None or timestamp - create_time > config.max_entry_age_seconds:
            break
        account = str(row.get("account") or "").strip().lower()
        if account:
            buyers.add(account)
        if buy_count >= config.min_entry_buy_count and len(buyers) >= config.min_entry_unique_buyers:
            sample_time = timestamp
            break
    if sample_time is None:
        return None
    path = _price_path(lifecycle)
    anchor_price = _price_at_or_before(path, sample_time)
    if anchor_price is None or anchor_price <= 0.0:
        return None
    return {
        "token": str(lifecycle.get("token_address") or lifecycle.get("token") or "").lower(),
        "symbol": lifecycle.get("symbol"),
        "sample_time": sample_time,
        "anchor_price": anchor_price,
        "path": path,
    }


def simulate_runner_path(
    path: Sequence[reentry_probe.PricePoint],
    *,
    sample_time: float,
    anchor_price: float,
    horizon_seconds: int,
    config: RunnerReplayConfig | None = None,
) -> dict[str, Any]:
    """Simulate baseline full exit and two-stage reserve exit for one path."""
    config = config or RunnerReplayConfig()
    entry_point = _point_at_or_after(path, sample_time + config.entry_delay_seconds)
    if entry_point is None:
        return {"status": "missing_entry", "horizon_seconds": int(horizon_seconds)}
    entry_time = _timestamp(entry_point.time)
    if entry_time is None:
        return {"status": "missing_entry", "horizon_seconds": int(horizon_seconds)}
    horizon_end = sample_time + float(horizon_seconds)
    last_time = max((_timestamp(point.time) or 0.0 for point in path), default=0.0)
    if last_time < horizon_end:
        return {"status": "incomplete_horizon", "horizon_seconds": int(horizon_seconds)}

    entry_price = _effective_buy_price(float(entry_point.price), config)
    stop_multiple = 1.0 + config.stop_loss_pct / 100.0
    activation_multiple = 1.0 + config.activation_return_pct / 100.0
    floor_multiple = 1.0 + config.reserve_floor_return_pct / 100.0
    drawdown_factor = 1.0 - config.reserve_drawdown_pct / 100.0
    points = [point for point in path if entry_time <= (_timestamp(point.time) or 0.0) <= horizon_end]

    baseline_exit = None
    runner_exit = None
    activated = False
    activation_time = None
    peak_multiple = 1.0
    realized_fraction = 0.0
    realized_return = 0.0
    baseline_reason = "horizon"
    runner_reason = "horizon"

    for point in points:
        point_time = _timestamp(point.time)
        if point_time is None:
            continue
        multiple = _effective_sell_multiple(float(point.price), entry_price, config)
        if baseline_exit is None and multiple <= stop_multiple:
            exit_point = _point_at_or_after(path, point_time + config.exit_delay_seconds)
            if exit_point is not None and (_timestamp(exit_point.time) or 0.0) <= horizon_end:
                baseline_exit = exit_point
                baseline_reason = "stop_loss"

        if not activated:
            if multiple >= activation_multiple:
                exit_point = _point_at_or_after(path, point_time + config.exit_delay_seconds)
                if exit_point is None or (_timestamp(exit_point.time) or 0.0) > horizon_end:
                    continue
                activated = True
                activation_time = _timestamp(exit_point.time)
                activation_multiple_realized = _effective_sell_multiple(float(exit_point.price), entry_price, config)
                realized_fraction = config.partial_exit_ratio
                realized_return = realized_fraction * (activation_multiple_realized - 1.0)
                peak_multiple = max(activation_multiple_realized, activation_multiple)
                continue

        if activated:
            peak_multiple = max(peak_multiple, multiple)
            if multiple <= floor_multiple or multiple <= peak_multiple * drawdown_factor:
                exit_point = _point_at_or_after(path, point_time + config.exit_delay_seconds)
                if exit_point is not None and (_timestamp(exit_point.time) or 0.0) <= horizon_end:
                    runner_exit = exit_point
                    runner_reason = "reserve_stop"
                    break

    final_point = _point_at_or_after(path, horizon_end)
    if final_point is None:
        final_point = points[-1] if points else None
    if final_point is None:
        return {"status": "missing_exit", "horizon_seconds": int(horizon_seconds)}
    if baseline_exit is None:
        baseline_exit = final_point
    if runner_exit is None:
        runner_exit = final_point

    baseline_multiple = _effective_sell_multiple(float(baseline_exit.price), entry_price, config)
    baseline_return = baseline_multiple - 1.0
    if activated:
        reserve_multiple = _effective_sell_multiple(float(runner_exit.price), entry_price, config)
        runner_return = realized_return + (1.0 - realized_fraction) * (reserve_multiple - 1.0)
    else:
        runner_return = baseline_return
        runner_reason = baseline_reason

    return {
        "status": "ok",
        "horizon_seconds": int(horizon_seconds),
        "entry_time": entry_time,
        "entry_price": entry_price,
        "baseline_return_pct": baseline_return * 100.0,
        "runner_return_pct": runner_return * 100.0,
        "baseline_reason": baseline_reason,
        "runner_reason": runner_reason,
        "activated": bool(activated),
        "activation_time": activation_time,
        "activation_multiple": (activation_multiple if activated else None),
        "runner_peak_multiple": peak_multiple,
        "baseline_exit_time": _timestamp(baseline_exit.time),
        "runner_exit_time": _timestamp(runner_exit.time),
    }


def _distribution(values: Iterable[float]) -> dict[str, float | int]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        return {"count": 0, "mean": 0.0, "median": 0.0, "p10": 0.0, "p90": 0.0}
    ordered = sorted(clean)
    return {
        "count": len(clean),
        "mean": statistics.mean(clean),
        "median": statistics.median(clean),
        "p10": ordered[round((len(ordered) - 1) * 0.10)],
        "p90": ordered[round((len(ordered) - 1) * 0.90)],
    }


def summarize_runner_outcomes(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    usable = [row for row in outcomes if row.get("status") == "ok"]
    activated = [row for row in usable if row.get("activated")]
    return {
        "sample_count": len(usable),
        "activated_count": len(activated),
        "activation_rate": len(activated) / len(usable) if usable else 0.0,
        "baseline_return_pct": _distribution(float(row["baseline_return_pct"]) for row in usable),
        "runner_return_pct": _distribution(float(row["runner_return_pct"]) for row in usable),
        "baseline_reason_counts": dict(sorted(Counter(str(row.get("baseline_reason")) for row in usable).items())),
        "runner_reason_counts": dict(sorted(Counter(str(row.get("runner_reason")) for row in usable).items())),
        "activated_runner_return_pct": _distribution(float(row["runner_return_pct"]) for row in activated),
    }


def profile_lifecycles(
    lifecycles: Iterable[Mapping[str, Any]],
    *,
    horizons: Sequence[int] = (300, 1_800, 7_200, 21_600, 86_400),
    config: RunnerReplayConfig | None = None,
) -> dict[str, Any]:
    config = config or RunnerReplayConfig()
    candidates = [candidate for lifecycle in lifecycles if (candidate := _entry_candidate(lifecycle, config))]
    horizon_reports = {}
    for horizon in sorted({int(value) for value in horizons if int(value) > 0}):
        outcomes = [
            simulate_runner_path(
                candidate["path"],
                sample_time=float(candidate["sample_time"]),
                anchor_price=float(candidate["anchor_price"]),
                horizon_seconds=horizon,
                config=config,
            )
            for candidate in candidates
        ]
        horizon_reports[str(horizon)] = summarize_runner_outcomes(outcomes)
        horizon_reports[str(horizon)]["incomplete_count"] = sum(row.get("status") == "incomplete_horizon" for row in outcomes)
        horizon_reports[str(horizon)]["missing_count"] = sum(row.get("status") != "ok" for row in outcomes)
    return {
        "schema_version": 1,
        "profile_type": "bsc_runner_reserve_executable_profile",
        "inputs": {
            "candidate_count": len(candidates),
            "horizons": sorted({int(value) for value in horizons if int(value) > 0}),
            "config": {
                key: getattr(config, key)
                for key in config.__dataclass_fields__
            },
        },
        "horizons": horizon_reports,
        "live_switch_recommendation": "none",
        "decision": "research_only_until_independent_final_and_stress_support",
    }


def load_lifecycles_from_paths(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in paths:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                lifecycle = json.loads(line)
                token = str(lifecycle.get("token_address") or lifecycle.get("token") or "").strip().lower()
                if not token:
                    continue
                lifecycle = dict(lifecycle)
                lifecycle["token_address"] = token
                existing = rows.get(token)
                if existing is None:
                    rows[token] = lifecycle
                else:
                    rows[token] = _merge_lifecycle_rows(existing, lifecycle)
    return list(rows.values())

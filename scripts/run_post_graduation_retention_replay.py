#!/usr/bin/env python3
"""Profile conditional post-graduation retention on lifecycle snapshots.

The current collector does not produce DEX snapshots, so a graduated token
without ``post_graduation_snapshots`` is deliberately counted as missing-feed
evidence.  This report is descriptive and cannot establish profitability.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.dataset_builder import stable_lifecycle_order  # noqa: E402
from src.trader.post_graduation_retention import (  # noqa: E402
    PostGraduationRetentionPolicy,
    RetentionHealthConfig,
)


DEFAULT_LIFECYCLE_DIR = "data/training/bsc_month_latest_20260911_run7"
DEFAULT_OUTPUT = "data/replay_reports/post_graduation_retention_replay.json"


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
    from datetime import datetime, timezone

    try:
        parsed_dt = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
    return parsed_dt.timestamp()


def _token(value: Any) -> str:
    return str(value or "").strip().lower()


def _load_lifecycles(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Merge duplicate lifecycle rows without dropping post-graduation snapshots."""

    rows: dict[str, dict[str, Any]] = {}
    snapshot_keys = ("post_graduation_snapshots", "dex_snapshots", "retention_snapshots")
    for path in paths:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, Mapping):
                    continue
                token = _token(row.get("token_address") or row.get("token"))
                if not token:
                    continue
                current = rows.get(token)
                if current is None:
                    current = dict(row)
                    rows[token] = current
                elif len(row.get("price_history") or []) > len(current.get("price_history") or []):
                    previous = current
                    replacement = dict(row)
                    if _as_bool(previous.get("graduated")):
                        replacement["graduated"] = True
                    if previous.get("graduate_time") and not replacement.get("graduate_time"):
                        replacement["graduate_time"] = previous.get("graduate_time")
                    for key in snapshot_keys:
                        previous_snapshots = list(previous.get(key) or [])
                        replacement_snapshots = list(replacement.get(key) or [])
                        combined = list(previous_snapshots)
                        previous_markers = {
                            json.dumps(item, sort_keys=True, default=str)
                            for item in previous_snapshots
                        }
                        for item in replacement_snapshots:
                            marker = json.dumps(item, sort_keys=True, default=str)
                            if marker not in previous_markers:
                                combined.append(item)
                                previous_markers.add(marker)
                        replacement[key] = combined
                    previous_nested = previous.get("post_graduation_market")
                    replacement_nested = replacement.get("post_graduation_market")
                    if isinstance(previous_nested, Mapping) or isinstance(replacement_nested, Mapping):
                        merged_nested = {}
                        if isinstance(previous_nested, Mapping):
                            merged_nested.update(previous_nested)
                        if isinstance(replacement_nested, Mapping):
                            merged_nested.update(replacement_nested)
                        replacement["post_graduation_market"] = merged_nested
                    rows[token] = replacement
                    current = replacement
                if _as_bool(row.get("graduated")):
                    current["graduated"] = True
                if row.get("graduate_time") and not current.get("graduate_time"):
                    current["graduate_time"] = row.get("graduate_time")
                for key in snapshot_keys:
                    incoming = row.get(key)
                    if not isinstance(incoming, list):
                        continue
                    merged = list(current.get(key) or [])
                    seen = {json.dumps(item, sort_keys=True, default=str) for item in merged}
                    for item in incoming:
                        marker = json.dumps(item, sort_keys=True, default=str)
                        if marker not in seen:
                            merged.append(item)
                            seen.add(marker)
                    current[key] = merged
                nested = row.get("post_graduation_market")
                if isinstance(nested, Mapping):
                    existing_nested = current.get("post_graduation_market")
                    merged_nested = dict(existing_nested) if isinstance(existing_nested, Mapping) else {}
                    merged_nested.update(nested)
                    current["post_graduation_market"] = merged_nested
    return list(rows.values())


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "graduated"}


def _snapshot_rows(lifecycle: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("post_graduation_snapshots", "dex_snapshots", "retention_snapshots"):
        value = lifecycle.get(key)
        if isinstance(value, list):
            rows.extend(dict(row) for row in value if isinstance(row, Mapping))
    nested = lifecycle.get("post_graduation_market")
    if isinstance(nested, Mapping):
        rows.append(dict(nested))
    if any(
        key in lifecycle
        for key in (
            "dex_liquidity_usd",
            "liquidity_usd",
            "pair_liquidity_usd",
            "volume_liquidity_ratio_5m",
            "dex_volume_5m_usd",
        )
    ):
        rows.append(dict(lifecycle))

    deduped: dict[tuple[float | None, str], dict[str, Any]] = {}
    for row in rows:
        timestamp = next(
            (
                _timestamp(row.get(key))
                for key in ("observed_at", "timestamp", "updated_at")
                if row.get(key) is not None
            ),
            None,
        )
        identity = (timestamp, json.dumps(row, sort_keys=True, default=str))
        deduped[identity] = row
    return sorted(
        deduped.values(),
        key=lambda row: next(
            (
                _timestamp(row.get(key))
                for key in ("observed_at", "timestamp", "updated_at")
                if row.get(key) is not None
            ),
        ) or 0.0,
    )


def _current_price(lifecycle: Mapping[str, Any], snapshot: Mapping[str, Any]) -> float:
    for source in (snapshot, lifecycle):
        for key in ("current_price", "price_current", "price", "mid_price"):
            value = _finite(source.get(key))
            if value is not None and value > 0.0:
                return value
    return 0.0


def _graduation_time(lifecycle: Mapping[str, Any], snapshots: Iterable[Mapping[str, Any]]) -> float | None:
    for key in ("graduate_time", "graduated_at"):
        value = _timestamp(lifecycle.get(key))
        if value is not None:
            return value
    for row in snapshots:
        value = next(
            (
                _timestamp(row.get(key))
                for key in ("observed_at", "timestamp", "updated_at")
                if row.get(key) is not None
            ),
            None,
        )
        if value is not None:
            return value
    return None


def _position_for_lifecycle(lifecycle: Mapping[str, Any], graduation_time: float | None) -> dict[str, Any]:
    create_time = _timestamp(lifecycle.get("create_timestamp", lifecycle.get("created_at")))
    peak_price = _finite(lifecycle.get("price_max"), 0.0) or 0.0
    return {
        "runner_state": "active",
        "runner_graduated": True,
        "runner_graduated_at": graduation_time,
        "entry_time": create_time,
        "runner_peak_price": peak_price,
        "peak_price": peak_price,
    }


def _evaluate_lifecycle(
    lifecycle: Mapping[str, Any],
    policy: PostGraduationRetentionPolicy,
) -> dict[str, Any]:
    token = _token(lifecycle.get("token_address") or lifecycle.get("token"))
    snapshots = _snapshot_rows(lifecycle)
    graduated = _as_bool(lifecycle.get("graduated"))
    if not graduated:
        return {
            "token": token,
            "symbol": lifecycle.get("symbol"),
            "status": "not_graduated",
            "snapshot_count": len(snapshots),
            "decision_counts": {},
            "reason_counts": {},
        }

    graduation_time = _graduation_time(lifecycle, snapshots)
    position = _position_for_lifecycle(lifecycle, graduation_time)
    if not snapshots:
        now = max(
            value
            for value in (
                graduation_time,
                _timestamp(lifecycle.get("last_update")),
                _timestamp(lifecycle.get("create_timestamp")),
            )
            if value is not None
        ) if any(
            value is not None
            for value in (
                graduation_time,
                _timestamp(lifecycle.get("last_update")),
                _timestamp(lifecycle.get("create_timestamp")),
            )
        ) else 0.0
        decision = policy.evaluate(
            position,
            market={"graduated": True},
            current_price=_current_price(lifecycle, {}),
            now=now,
        )
        return {
            "token": token,
            "symbol": lifecycle.get("symbol"),
            "status": "graduated_without_dex_snapshot",
            "snapshot_count": 0,
            "decision_counts": {decision.action: 1},
            "reason_counts": {decision.reason: 1},
            "first_decision": {
                "action": decision.action,
                "reason": decision.reason,
                "missing_fields": list(decision.missing_fields),
            },
        }

    decisions = Counter()
    reasons = Counter()
    first_decision: dict[str, Any] | None = None
    for row in snapshots:
        market = dict(row)
        market.setdefault("graduated", True)
        observed = next(
            (
                _timestamp(market.get(key))
                for key in ("observed_at", "timestamp", "updated_at")
                if market.get(key) is not None
            ),
            None,
        )
        if observed is None:
            fallback_now = max(
                value
                for value in (
                    graduation_time,
                    _timestamp(lifecycle.get("last_update")),
                    _timestamp(lifecycle.get("create_timestamp")),
                )
                if value is not None
            ) if any(
                value is not None
                for value in (
                    graduation_time,
                    _timestamp(lifecycle.get("last_update")),
                    _timestamp(lifecycle.get("create_timestamp")),
                )
            ) else 0.0
            decision = policy.evaluate(
                position,
                market=dict(market),
                current_price=_current_price(lifecycle, market),
                now=fallback_now,
            )
            decisions[decision.action] += 1
            reasons[decision.reason] += 1
            if first_decision is None:
                first_decision = {
                    "action": decision.action,
                    "reason": decision.reason,
                    "health_score": decision.health_score,
                    "checks": dict(decision.checks),
                    "missing_fields": list(decision.missing_fields),
                    "provenance": dict(decision.provenance),
                }
            break
        decision = policy.evaluate(
            position,
            market=market,
            current_price=_current_price(lifecycle, market),
            now=observed,
        )
        decisions[decision.action] += 1
        reasons[decision.reason] += 1
        if first_decision is None:
            first_decision = {
                "action": decision.action,
                "reason": decision.reason,
                "health_score": decision.health_score,
                "checks": dict(decision.checks),
                "missing_fields": list(decision.missing_fields),
                "provenance": dict(decision.provenance),
            }
        if decision.action == "close":
            break
    return {
        "token": token,
        "symbol": lifecycle.get("symbol"),
        "status": "evaluated",
        "snapshot_count": len(snapshots),
        "decision_counts": dict(decisions),
        "reason_counts": dict(reasons),
        "first_decision": first_decision,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", default=DEFAULT_LIFECYCLE_DIR)
    parser.add_argument("--lifecycle-file", action="append", default=None)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--max-hold-seconds", type=float, default=4 * 86_400.0)
    parser.add_argument("--max-data-age-seconds", type=float, default=120.0)
    parser.add_argument("--min-liquidity-usd", type=float, default=10_000.0)
    parser.add_argument("--min-volume-liquidity-ratio-5m", type=float, default=0.20)
    parser.add_argument("--min-new-traders-1h", type=float, default=2.0)
    parser.add_argument("--max-sell-pressure-5m", type=float, default=0.60)
    parser.add_argument("--max-drawdown", type=float, default=0.35)
    parser.add_argument("--max-examples", type=int, default=20)
    return parser.parse_args(argv)


def build_report(args) -> dict[str, Any]:
    paths = [Path(path) for path in args.lifecycle_file] if args.lifecycle_file else stable_lifecycle_order(
        Path(args.lifecycle_dir).glob("lifecycle_*.jsonl")
    )
    policy = PostGraduationRetentionPolicy(
        RetentionHealthConfig(
            enabled=True,
            max_hold_seconds=float(args.max_hold_seconds),
            max_data_age_seconds=float(args.max_data_age_seconds),
            min_liquidity_usd=float(args.min_liquidity_usd),
            min_volume_liquidity_ratio_5m=float(args.min_volume_liquidity_ratio_5m),
            min_new_traders_1h=float(args.min_new_traders_1h),
            max_sell_pressure_5m=float(args.max_sell_pressure_5m),
            max_drawdown_from_peak=float(args.max_drawdown),
        )
    )
    lifecycles = _load_lifecycles(paths)
    token_reports = [_evaluate_lifecycle(lifecycle, policy) for lifecycle in lifecycles]
    status_counts = Counter(row["status"] for row in token_reports)
    decision_counts = Counter()
    reason_counts = Counter()
    for row in token_reports:
        decision_counts.update(row.get("decision_counts") or {})
        reason_counts.update(row.get("reason_counts") or {})
    token_reports.sort(key=lambda row: row.get("token") or "")
    examples = [
        row for row in token_reports
        if row.get("status") != "not_graduated"
    ][: max(0, int(args.max_examples))]
    return {
        "schema_version": 1,
        "profile_type": "bsc_post_graduation_retention_health_replay",
        "inputs": {
            "lifecycle_dir": str(args.lifecycle_dir),
            "lifecycle_files": [str(path) for path in paths],
            "token_count": len(lifecycles),
            "config": {
                "max_hold_seconds": float(args.max_hold_seconds),
                "max_data_age_seconds": float(args.max_data_age_seconds),
                "min_liquidity_usd": float(args.min_liquidity_usd),
                "min_volume_liquidity_ratio_5m": float(args.min_volume_liquidity_ratio_5m),
                "min_new_traders_1h": float(args.min_new_traders_1h),
                "max_sell_pressure_5m": float(args.max_sell_pressure_5m),
                "max_drawdown": float(args.max_drawdown),
            },
        },
        "summary": {
            "token_count": len(token_reports),
            "graduated_count": status_counts.get("graduated_without_dex_snapshot", 0)
            + status_counts.get("evaluated", 0),
            "post_graduation_snapshot_token_count": status_counts.get("evaluated", 0),
            "graduated_without_dex_snapshot_count": status_counts.get("graduated_without_dex_snapshot", 0),
            "status_counts": dict(sorted(status_counts.items())),
            "decision_counts": dict(sorted(decision_counts.items())),
            "reason_counts": dict(sorted(reason_counts.items())),
        },
        "examples": examples,
        "decision": "research_only_missing_post_graduation_dex_coverage",
    }


def main(argv=None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    summary = report["summary"]
    print(
        "tokens=%d graduated=%d with_dex_snapshots=%d missing_dex_snapshots=%d decisions=%s"
        % (
            summary["token_count"],
            summary["graduated_count"],
            summary["post_graduation_snapshot_token_count"],
            summary["graduated_without_dex_snapshot_count"],
            summary["decision_counts"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

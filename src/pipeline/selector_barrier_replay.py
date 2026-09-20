"""Replay a concrete legacy model selector against entry-time barrier outcomes.

The target-barrier profile intentionally uses an activity-gated candidate pool
as a denominator.  This module adds the missing comparison: load a historical
hybrid model, apply its probability/entry-value/quality/near-rescue rules at
every decision-time event, and then replay only the accepted signals.
"""

from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

try:
    from catboost import CatBoostClassifier, CatBoostRegressor
except Exception:  # pragma: no cover - runtime command reports a clear error
    CatBoostClassifier = None
    CatBoostRegressor = None

from src.data.feature_extractor import extract_features
from src.pipeline.runner_reserve_profile import (
    _price_path,
    _timestamp)
from src.pipeline.target_barrier_profile import (
    BarrierReplayConfig,
    DEFAULT_HORIZONS,
    build_entry_candidates,
    simulate_barrier,
    summarize_outcomes,
)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if math.isfinite(parsed) else float(default)


def _evaluation_value(manifest: Mapping[str, Any], key: str, default: Any = None) -> Any:
    evaluation = manifest.get("evaluation") if isinstance(manifest, Mapping) else None
    if not isinstance(evaluation, Mapping):
        evaluation = {}
    if key in evaluation:
        return evaluation[key]
    runtime = evaluation.get("runtime_replay")
    if isinstance(runtime, Mapping) and key in runtime:
        return runtime[key]
    return default


def load_legacy_selector(model_dir: str | Path) -> dict[str, Any]:
    """Load model artifacts and selector parameters from a hybrid manifest."""

    if CatBoostClassifier is None or CatBoostRegressor is None:
        raise ModuleNotFoundError("catboost is required for legacy selector replay")
    model_dir = Path(model_dir)
    manifest = json.loads((model_dir / "hybrid_manifest.json").read_text(encoding="utf-8"))
    buy_model = CatBoostClassifier()
    buy_model.load_model(str(model_dir / "buy_model.cbm"))
    entry_path = model_dir / "entry_value_model.cbm"
    entry_model = None
    if entry_path.exists():
        entry_model = CatBoostRegressor()
        entry_model.load_model(str(entry_path))
    schema = json.loads((model_dir / "feature_schema.json").read_text(encoding="utf-8"))
    feature_names = list(schema.get("feature_names") or [])
    threshold_path = model_dir / "buy_threshold.json"
    threshold_payload = json.loads(threshold_path.read_text(encoding="utf-8")) if threshold_path.exists() else {}
    threshold = _finite(threshold_payload.get("threshold"), _finite(_evaluation_value(manifest, "buy_threshold", 0.5)))
    return {
        "model_dir": str(model_dir),
        "manifest": manifest,
        "buy_model": buy_model,
        "entry_value_model": entry_model,
        "feature_names": feature_names,
        "threshold": threshold,
        "min_entry_unique_buyers": int(_finite(_evaluation_value(manifest, "min_entry_unique_buyers", 3), 3)),
        "min_entry_buy_count": int(_finite(_evaluation_value(manifest, "min_entry_buy_count", 5), 5)),
        "max_entry_age_seconds": int(_finite(_evaluation_value(manifest, "max_entry_age_seconds", 300), 300)),
        "min_entry_volume_30s": _evaluation_value(manifest, "min_entry_volume_30s", None),
        "min_entry_price_volatility": _evaluation_value(manifest, "min_entry_price_volatility", None),
        "min_entry_score": _evaluation_value(manifest, "min_entry_score", None),
        "entry_ranking_mode": str(_evaluation_value(manifest, "entry_ranking_mode", "chronological") or "chronological"),
        "entry_delay_seconds": int(_finite(_evaluation_value(manifest, "entry_delay_seconds", 3), 3)),
        "exit_delay_seconds": int(_finite(_evaluation_value(manifest, "exit_delay_seconds", 3), 3)),
        "entry_max_fill_wait_seconds": _evaluation_value(manifest, "entry_max_fill_wait_seconds", None),
        "entry_price_protection_pct": _evaluation_value(manifest, "entry_price_protection_pct", None),
        "use_pred_return_filter": bool(_evaluation_value(manifest, "use_pred_return_filter", True)),
        "near": {
            "min_prob": _evaluation_value(manifest, "buy_near_threshold_min_prob", None),
            "min_score": _evaluation_value(manifest, "buy_near_min_pred_return", None),
            "min_volume": _evaluation_value(manifest, "buy_near_min_entry_volume_30s", None),
            "min_volatility": _evaluation_value(manifest, "buy_near_min_entry_price_volatility", None),
            "min_age": _evaluation_value(manifest, "buy_near_min_age_seconds", None),
        },
    }


def _passes_floor(value: Any, floor: Any) -> bool:
    if floor is None:
        return True
    parsed = _finite(value, math.nan)
    return math.isfinite(parsed) and parsed >= _finite(floor)


def _signal_acceptance(features: Mapping[str, Any], probability: float, score: float | None, selector: Mapping[str, Any], age: float) -> str | None:
    """Return ``primary``/``near`` for a legacy accepted signal, else None."""

    volume = features.get("volume_30s")
    volatility = features.get("price_volatility")
    threshold = _finite(selector.get("threshold"), 1.0)
    base_quality = (
        _passes_floor(volume, selector.get("min_entry_volume_30s"))
        and _passes_floor(volatility, selector.get("min_entry_price_volatility"))
    )
    if probability >= threshold:
        if selector.get("use_pred_return_filter") and not _passes_floor(score, selector.get("min_entry_score")):
            return None
        return "primary" if base_quality else None

    near = selector.get("near") or {}
    near_prob = near.get("min_prob")
    if near_prob is None or probability < _finite(near_prob) or probability >= threshold:
        return None
    if not _passes_floor(score, near.get("min_score")):
        return None
    if not _passes_floor(volume, near.get("min_volume")):
        return None
    if not _passes_floor(volatility, near.get("min_volatility")):
        return None
    if near.get("min_age") is not None and age < _finite(near.get("min_age")):
        return None
    return "near"


def legacy_selector_candidates(
    lifecycles: Sequence[Mapping[str, Any]],
    selector: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply legacy model rules to every decision-time event and keep first hit."""

    feature_names = list(selector.get("feature_names") or [])
    if not feature_names:
        raise ValueError("legacy model feature schema is empty")
    rows: list[dict[str, Any]] = []
    for lifecycle in lifecycles:
        create_time = _finite(lifecycle.get("create_timestamp"))
        if create_time <= 0.0:
            continue
        buys = sorted(
            [row for row in lifecycle.get("buys") or [] if isinstance(row, Mapping) and _timestamp(row.get("timestamp")) is not None],
            key=lambda row: _finite(row.get("timestamp")),
        )
        sells = sorted(
            [row for row in lifecycle.get("sells") or [] if isinstance(row, Mapping) and _timestamp(row.get("timestamp")) is not None],
            key=lambda row: _finite(row.get("timestamp")),
        )
        times = sorted({int(_finite(row.get("timestamp"))) for row in buys + sells if 0 < _finite(row.get("timestamp")) - create_time <= selector["max_entry_age_seconds"]})
        buyer_count = 0
        for sample_time in times:
            past_buys = [row for row in buys if _finite(row.get("timestamp")) <= sample_time]
            past_sells = [row for row in sells if _finite(row.get("timestamp")) <= sample_time]
            buyer_count = len({str(row.get("account") or "").lower() for row in past_buys if str(row.get("account") or "").strip()})
            if len(past_buys) < selector["min_entry_buy_count"] or buyer_count < selector["min_entry_unique_buyers"]:
                continue
            features = extract_features(
                dict(lifecycle),
                past_buys,
                past_sells,
                sample_time,
                include_flow_features=True,
            )
            rows.append(
                {
                    "lifecycle": lifecycle,
                    "sample_time": int(sample_time),
                    "age_seconds": float(sample_time - create_time),
                    "features": features,
                }
            )

    if not rows:
        return [], {"decision_row_count": 0, "accepted_signal_row_count": 0, "primary_signal_count": 0, "near_signal_count": 0}
    X = pd.DataFrame(
        [
            {_name: _finite((row.get("features") or {}).get(_name)) for _name in feature_names}
            for row in rows
        ],
        columns=feature_names,
        dtype=float,
    )
    probabilities = selector["buy_model"].predict_proba(X)[:, 1]
    scores = (
        selector["entry_value_model"].predict(X).reshape(-1)
        if selector.get("entry_value_model") is not None
        else np.full(len(rows), np.nan, dtype=float)
    )
    accepted: list[dict[str, Any]] = []
    first_by_token: dict[str, dict[str, Any]] = {}
    branch_counts = Counter()
    for row, probability, score in zip(rows, probabilities, scores):
        lifecycle = row["lifecycle"]
        token = str(lifecycle.get("token_address") or lifecycle.get("token") or "").strip().lower()
        if not token:
            continue
        branch = _signal_acceptance(
            row["features"],
            float(probability),
            None if not math.isfinite(float(score)) else float(score),
            selector,
            row["age_seconds"],
        )
        if branch is None:
            continue
        branch_counts[branch] += 1
        existing = first_by_token.get(token)
        if existing is not None and int(existing["sample_time"]) <= int(row["sample_time"]):
            continue
        candidate = {
            "token": token,
            "symbol": lifecycle.get("symbol"),
            "sample_time": int(row["sample_time"]),
            "create_timestamp": int(_finite(lifecycle.get("create_timestamp"))),
            "path": _price_path(lifecycle),
            "features": row["features"],
            "signal_price": _finite(row["features"].get("current_price")),
            "graduated": bool(lifecycle.get("graduated")),
            "legacy_probability": float(probability),
            "legacy_entry_score": None if not math.isfinite(float(score)) else float(score),
            "legacy_branch": branch,
        }
        first_by_token[token] = candidate
    accepted = sorted(first_by_token.values(), key=lambda row: (int(row["sample_time"]), str(row["token"])))
    return accepted, {
        "decision_row_count": len(rows),
        "accepted_signal_row_count": int(sum(branch_counts.values())),
        "accepted_token_count": len(accepted),
        "primary_signal_count": int(branch_counts.get("primary", 0)),
        "near_signal_count": int(branch_counts.get("near", 0)),
        "accepted_token_branch_counts": dict(Counter(str(row.get("legacy_branch")) for row in accepted)),
    }


def _capacity_subset(
    candidates: Sequence[Mapping[str, Any]],
    *,
    horizon_seconds: int,
    target_return_pct: float,
    stop_loss_pct: float,
    config: BarrierReplayConfig,
    max_open_positions: int | None,
) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]], dict[str, int]]:
    outcomes = []
    selected = []
    active_exit_times: list[float] = []
    skipped = Counter()
    for candidate in candidates:
        outcome = simulate_barrier(
            candidate,
            horizon_seconds=horizon_seconds,
            target_return_pct=target_return_pct,
            stop_loss_pct=stop_loss_pct,
            config=config,
        )
        if outcome.get("status") in {"missing_entry", "entry_timeout", "entry_protection_skip"}:
            skipped[str(outcome.get("status"))] += 1
            continue
        entry_time = _finite(outcome.get("entry_time"), _finite(candidate.get("sample_time")))
        active_exit_times = [value for value in active_exit_times if value > entry_time]
        if max_open_positions is not None and len(active_exit_times) >= int(max_open_positions):
            continue
        selected.append(candidate)
        outcomes.append(outcome)
        exit_time = _finite(outcome.get("exit_time"), entry_time + horizon_seconds)
        active_exit_times.append(max(entry_time, exit_time))
    return selected, outcomes, {
        "attempted_count": len(candidates),
        "selected_count": len(selected),
        "skipped_count": sum(skipped.values()),
        **{f"{key}_count": int(value) for key, value in skipped.items()},
    }


def replay_selector(
    lifecycles: Sequence[Mapping[str, Any]],
    *,
    model_dir: str | Path,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    target_return_pct: float = 20.0,
    stop_loss_pct: float = -30.0,
    config: BarrierReplayConfig | None = None,
    max_open_positions: int | None = 8,
) -> dict[str, Any]:
    config = config or BarrierReplayConfig(
        min_entry_unique_buyers=3,
        min_entry_buy_count=5,
        max_entry_age_seconds=300,
    )
    selector = load_legacy_selector(model_dir)
    if config is None:
        config = BarrierReplayConfig(
            entry_delay_seconds=selector["entry_delay_seconds"],
            exit_delay_seconds=selector["exit_delay_seconds"],
            entry_max_fill_wait_seconds=(
                None
                if selector.get("entry_max_fill_wait_seconds") is None
                else int(_finite(selector.get("entry_max_fill_wait_seconds")))
            ),
            entry_price_protection_pct=(
                None
                if selector.get("entry_price_protection_pct") is None
                else _finite(selector.get("entry_price_protection_pct"))
            ),
            min_entry_unique_buyers=selector["min_entry_unique_buyers"],
            min_entry_buy_count=selector["min_entry_buy_count"],
            max_entry_age_seconds=selector["max_entry_age_seconds"],
        )
    activity_candidates = build_entry_candidates(lifecycles, config, include_features=True)
    legacy_candidates, selector_stats = legacy_selector_candidates(lifecycles, selector)
    horizon_reports: dict[str, Any] = {}
    for horizon in sorted({int(value) for value in horizons if int(value) > 0}):
        activity_selected, activity_outcomes, activity_stats = _capacity_subset(
            activity_candidates,
            horizon_seconds=horizon,
            target_return_pct=target_return_pct,
            stop_loss_pct=stop_loss_pct,
            config=config,
            max_open_positions=max_open_positions,
        )
        legacy_selected, legacy_outcomes, legacy_stats = _capacity_subset(
            legacy_candidates,
            horizon_seconds=horizon,
            target_return_pct=target_return_pct,
            stop_loss_pct=stop_loss_pct,
            config=config,
            max_open_positions=max_open_positions,
        )
        horizon_reports[str(horizon)] = {
            "activity_gate_all": {
                "candidate_count": len(activity_candidates),
                "capacity_selected_count": len(activity_selected),
                "execution_stats": activity_stats,
                "summary": summarize_outcomes(activity_outcomes, fixed_stake_bnb=config.fixed_stake_bnb),
            },
            "legacy_model_selector": {
                "candidate_count": len(legacy_candidates),
                "capacity_selected_count": len(legacy_selected),
                "execution_stats": legacy_stats,
                "summary": summarize_outcomes(legacy_outcomes, fixed_stake_bnb=config.fixed_stake_bnb),
            },
        }
    return {
        "schema_version": 1,
        "profile_type": "bsc_legacy_selector_target_barrier_replay",
        "model_dir": str(model_dir),
        "selector": {
            key: value
            for key, value in selector.items()
            if key not in {"manifest", "buy_model", "entry_value_model"}
        },
        "inputs": {
            "horizons": [int(value) for value in horizons],
            "target_return_pct": float(target_return_pct),
            "stop_loss_pct": float(stop_loss_pct),
            "config": {key: getattr(config, key) for key in config.__dataclass_fields__},
            "max_open_positions": max_open_positions,
        },
        "scope": {
            "lifecycle_count": len(lifecycles),
            "activity_gate_candidate_count": len(activity_candidates),
            "legacy_selector_candidate_count": len(legacy_candidates),
            "activity_gate_is_model_selector": False,
            "legacy_selector_is_model_selector": True,
            "uses_graduation_as_filter": False,
            "uses_decision_time_features_only": True,
            "legacy_selector_stats": selector_stats,
        },
        "horizons": horizon_reports,
        "decision": "selector_scope_audit_only_no_runtime_switch",
    }


__all__ = [
    "legacy_selector_candidates",
    "load_legacy_selector",
    "replay_selector",
]

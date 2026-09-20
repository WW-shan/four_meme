from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from typing import Any, Iterable

from src.pipeline import reentry_probe


DECISION_TIME_FIELDS = (
    "volume_30s",
    "price_volatility",
    "token_age_seconds",
    "feature_count",
    "features_hash",
    "entry_ranking_mode",
    "near_threshold_rescue_used",
    "use_pred_return_filter",
    "min_pred_return",
    "min_entry_volume_30s",
    "min_entry_price_volatility",
    "buy_near_threshold_min_prob",
    "buy_near_min_pred_return",
    "buy_near_min_entry_volume_30s",
    "buy_near_min_entry_price_volatility",
    "buy_near_min_age_seconds",
)

DECISION_TIME_ALIASES = {
    "entry_volume_30s": "volume_30s",
    "entry_price_volatility": "price_volatility",
    "age_seconds": "token_age_seconds",
}

FLOW_WINDOWS_SECONDS = (10, 30, 60)


def _first_present(*values: float | None) -> float | None:
    present = [float(value) for value in values if value is not None]
    return min(present) if present else None


def _decision_time_fields(signal: dict[str, Any]) -> dict[str, Any]:
    copied = {key: signal[key] for key in DECISION_TIME_FIELDS if key in signal}
    for alias, canonical in DECISION_TIME_ALIASES.items():
        if alias in signal:
            copied[alias] = signal[alias]
        if canonical in signal:
            copied[canonical] = signal[canonical]
        if canonical in copied:
            copied.setdefault(alias, copied[canonical])
        if alias in copied:
            copied.setdefault(canonical, copied[alias])
    return copied


def _event_time(row: dict[str, Any]) -> dt.datetime | None:
    value = row.get("timestamp", row.get("time"))
    if value is None:
        return None
    try:
        return reentry_probe.parse_time(value)
    except (TypeError, ValueError):
        return None


def _event_bnb_amount(row: dict[str, Any]) -> float:
    return reentry_probe.safe_float(row.get("bnb_amount"))


def _events_before_anchor(
    rows: Iterable[dict[str, Any]],
    *,
    anchor_time: dt.datetime,
    window_seconds: float,
) -> list[dict[str, Any]]:
    anchor_time = reentry_probe.parse_time(anchor_time)
    selected: list[dict[str, Any]] = []
    for row in rows:
        event_time = _event_time(row)
        if event_time is None:
            continue
        seconds_before = (anchor_time - event_time).total_seconds()
        if 0.0 <= seconds_before <= float(window_seconds):
            selected.append(row)
    return selected


def _accounts(rows: Iterable[dict[str, Any]]) -> set[str]:
    return {
        str(row.get("account") or "").strip().lower()
        for row in rows
        if str(row.get("account") or "").strip()
    }


def _window_flow_metrics(*, buys: list[dict[str, Any]], sells: list[dict[str, Any]], window_seconds: int) -> dict[str, Any]:
    buy_volume = sum(_event_bnb_amount(row) for row in buys)
    sell_volume = sum(_event_bnb_amount(row) for row in sells)
    total_volume = buy_volume + sell_volume
    prefix = "flow_"
    suffix = f"_{int(window_seconds)}s"
    metrics = {
        f"{prefix}buy_volume{suffix}": float(buy_volume),
        f"{prefix}sell_volume{suffix}": float(sell_volume),
        f"{prefix}total_volume{suffix}": float(total_volume),
        f"{prefix}event_count{suffix}": int(len(buys) + len(sells)),
        f"{prefix}sell_pressure{suffix}": None,
        f"{prefix}buy_sell_ratio{suffix}": None,
        f"{prefix}signed_imbalance{suffix}": None,
    }
    if total_volume > 0.0:
        metrics[f"{prefix}sell_pressure{suffix}"] = sell_volume / total_volume
        metrics[f"{prefix}signed_imbalance{suffix}"] = (buy_volume - sell_volume) / total_volume
        if sell_volume > 0.0:
            metrics[f"{prefix}buy_sell_ratio{suffix}"] = buy_volume / sell_volume
    return metrics


def _signal_time_flow_fields(lifecycle: dict[str, Any] | None, anchor_time: dt.datetime) -> dict[str, Any]:
    if not lifecycle:
        return {"flow_metrics_available": False}

    raw_buys = [row for row in lifecycle.get("buys") or [] if isinstance(row, dict)]
    raw_sells = [row for row in lifecycle.get("sells") or [] if isinstance(row, dict)]
    fields: dict[str, Any] = {"flow_metrics_available": True}
    window_buys: dict[int, list[dict[str, Any]]] = {}
    window_sells: dict[int, list[dict[str, Any]]] = {}
    for window_seconds in FLOW_WINDOWS_SECONDS:
        buys = _events_before_anchor(raw_buys, anchor_time=anchor_time, window_seconds=window_seconds)
        sells = _events_before_anchor(raw_sells, anchor_time=anchor_time, window_seconds=window_seconds)
        window_buys[int(window_seconds)] = buys
        window_sells[int(window_seconds)] = sells
        fields.update(_window_flow_metrics(buys=buys, sells=sells, window_seconds=int(window_seconds)))

    buyers_60 = _accounts(window_buys[60])
    sellers_60 = _accounts(window_sells[60])
    buyers_30 = _accounts(window_buys[30])
    buyers_10 = _accounts(window_buys[10])
    prev_50_buys = [
        row
        for row in window_buys[60]
        if row not in window_buys[10]
    ]
    prev_50_buyers = _accounts(prev_50_buys)

    fields["flow_buy_sell_overlap_ratio_60s"] = (
        len(buyers_60 & sellers_60) / len(buyers_60) if buyers_60 else 0.0
    )
    fields["flow_recent_seller_reentry_ratio_30s"] = (
        len(buyers_30 & sellers_60) / len(buyers_30) if buyers_30 else 0.0
    )
    if buyers_10 or prev_50_buyers:
        fields["flow_buyer_set_churn_10s_vs_prev50s"] = 1.0 - (
            len(buyers_10 & prev_50_buyers) / max(len(buyers_10 | prev_50_buyers), 1)
        )
    else:
        fields["flow_buyer_set_churn_10s_vs_prev50s"] = 0.0
    return fields


def _before_stop(hit_time: float | None, stop_time: float | None) -> bool:
    return hit_time is not None and (stop_time is None or float(hit_time) < float(stop_time))


def _json_default(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


def to_json_text(report: dict[str, Any]) -> str:
    return json.dumps(report, default=_json_default, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def score_signal_time_to_barrier(
    signal: dict[str, Any],
    path: Iterable[reentry_probe.PricePoint],
    *,
    lifecycle: dict[str, Any] | None = None,
    horizon_seconds: float = 600,
    quick_profit_seconds: float = 120,
) -> dict[str, Any]:
    anchor_time = reentry_probe.parse_time(signal.get("time"))
    path = list(path)
    anchor_price = reentry_probe._anchor_price_at_or_before(path, anchor_time)
    base = {
        "token": reentry_probe.normalize_token(signal.get("token") or signal.get("token_address")),
        "symbol": signal.get("symbol"),
        "reason": signal.get("reason"),
        "prob": signal.get("prob"),
        "pred_return": signal.get("pred_return"),
        "signal_time": anchor_time,
        "candidate_type": "rejected_signal_time_to_barrier",
        **_decision_time_fields(signal),
        **_signal_time_flow_fields(lifecycle, anchor_time),
    }
    if not path or anchor_price is None or anchor_price <= 0.0:
        return {
            **base,
            "barrier_class": "missing_path",
            "recommended_policy": "skip",
            "quick_take_profit_candidate": False,
            "slow_runner_candidate": False,
            "missing_path": True,
        }

    metrics = reentry_probe.path_metrics(
        path,
        anchor_time=anchor_time,
        anchor_price=anchor_price,
        horizon_seconds=horizon_seconds,
    )
    plus_25 = metrics.get("time_to_plus_25_seconds")
    plus_60 = metrics.get("time_to_plus_60_seconds")
    minus_18 = metrics.get("time_to_minus_18_seconds")
    minus_25 = metrics.get("time_to_minus_25_seconds")
    stop_time = _first_present(minus_18, minus_25)

    if metrics.get("first_barrier") in {"-18", "-25"}:
        barrier_class = "stop_first"
    elif (
        plus_25 is not None
        and float(plus_25) <= float(quick_profit_seconds)
        and _before_stop(plus_25, stop_time)
        and stop_time is not None
        and float(stop_time) > float(plus_25)
    ):
        barrier_class = "fast_profit_then_collapse"
    elif plus_25 is not None and float(plus_25) <= float(quick_profit_seconds) and _before_stop(plus_25, stop_time):
        barrier_class = "fast_profit"
    elif (
        (plus_25 is not None and float(plus_25) > float(quick_profit_seconds) and _before_stop(plus_25, stop_time))
        or _before_stop(plus_60, stop_time)
    ):
        barrier_class = "slow_runner"
    else:
        barrier_class = "flat_timeout"

    recommended_policy = "skip"
    if barrier_class in {"fast_profit_then_collapse", "fast_profit"}:
        recommended_policy = "quick_take_profit"
    elif barrier_class == "slow_runner":
        recommended_policy = "conditional_slow_hold"

    return {
        **base,
        "barrier_class": barrier_class,
        "recommended_policy": recommended_policy,
        "quick_take_profit_candidate": recommended_policy == "quick_take_profit",
        "slow_runner_candidate": recommended_policy == "conditional_slow_hold",
        "missing_path": False,
        **metrics,
    }


def _signal_rank_key(signal: dict[str, Any]) -> tuple[float, float, dt.datetime]:
    return (
        reentry_probe.safe_float(signal.get("pred_return")),
        reentry_probe.safe_float(signal.get("prob")),
        reentry_probe.parse_time(signal.get("time")),
    )


def build_probe_report(
    *,
    signal_rows: Iterable[dict[str, Any]],
    lifecycles: dict[str, dict[str, Any]],
    generated_at: dt.datetime | None = None,
    horizon_seconds: float = 600,
    quick_profit_seconds: float = 120,
    since: Any = None,
    until: Any = None,
    max_candidate_sample: int = 100,
) -> dict[str, Any]:
    candidate_sample_limit = int(max_candidate_sample)
    if candidate_sample_limit < 0:
        raise ValueError("max_candidate_sample must be non-negative")
    parsed_signals = list(reentry_probe.iter_signal_decisions(signal_rows))
    since_time = reentry_probe.parse_time(since) if since is not None else None
    until_time = reentry_probe.parse_time(until) if until is not None else None
    if since_time is not None:
        parsed_signals = [signal for signal in parsed_signals if reentry_probe.parse_time(signal.get("time")) >= since_time]
    if until_time is not None:
        parsed_signals = [signal for signal in parsed_signals if reentry_probe.parse_time(signal.get("time")) <= until_time]
    signal_by_token: dict[str, dict[str, Any]] = {}
    for signal in parsed_signals:
        token = reentry_probe.normalize_token(signal.get("token"))
        if not token:
            continue
        current = signal_by_token.get(token)
        if current is None or _signal_rank_key(signal) > _signal_rank_key(current):
            signal_by_token[token] = signal

    normalized_lifecycles = {
        reentry_probe.normalize_token(token): lifecycle
        for token, lifecycle in (lifecycles or {}).items()
        if reentry_probe.normalize_token(token)
    }
    candidates = [
        score_signal_time_to_barrier(
            signal,
            reentry_probe.price_path_for_token(normalized_lifecycles, signal.get("token")),
            lifecycle=normalized_lifecycles.get(reentry_probe.normalize_token(signal.get("token"))),
            horizon_seconds=horizon_seconds,
            quick_profit_seconds=quick_profit_seconds,
        )
        for signal in signal_by_token.values()
    ]
    class_counts = Counter(candidate["barrier_class"] for candidate in candidates)
    policy_counts = Counter(candidate["recommended_policy"] for candidate in candidates)
    if candidate_sample_limit == 0:
        candidate_sample = candidates
    else:
        candidate_sample = candidates[:candidate_sample_limit]
    unemitted_candidate_count = max(0, len(candidates) - len(candidate_sample))

    return {
        "generated_at": generated_at
        or dt.datetime.now(dt.timezone.utc).astimezone(reentry_probe.ANALYSIS_TZ).replace(tzinfo=None),
        "probe_contract": {
            "read_only": True,
            "live_switch_evidence": False,
            "requires_replay_before_live_change": True,
        },
        "parameters": {
            "horizon_seconds": horizon_seconds,
            "quick_profit_seconds": quick_profit_seconds,
            "since": since_time,
            "until": until_time,
            "max_candidate_sample": candidate_sample_limit,
        },
        "candidate_counts": {
            "signal_decisions": len(parsed_signals),
            "per_token_candidates": len(candidates),
            "dropped_duplicate_signal_decisions": max(0, len(parsed_signals) - len(candidates)),
            "emitted_candidate_count": len(candidate_sample),
            "sample_limited": unemitted_candidate_count > 0,
            "unemitted_candidate_count": unemitted_candidate_count,
        },
        "class_counts": dict(sorted(class_counts.items())),
        "policy_counts": dict(sorted(policy_counts.items())),
        "candidate_sample": candidate_sample,
    }

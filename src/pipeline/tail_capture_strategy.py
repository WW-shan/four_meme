"""Tail-aware entry ranking and fixed-horizon portfolio replay.

This module is intentionally separate from the legacy barrier trainer.  It
answers a different question: after an observable early entry, which signal
has the best *capital-weighted* future path, and how does that ranking behave
when positions are held for several hours or days?  Training uses complete
paths only; replay reports incomplete paths separately instead of silently
turning missing post-graduation data into a target failure.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from bisect import bisect_left
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import json

import numpy as np
import pandas as pd

from src.data.feature_extractor import extract_features

try:
    from catboost import CatBoostClassifier, CatBoostRanker, Pool
except Exception:  # pragma: no cover - environments without optional ML deps
    CatBoostClassifier = None
    CatBoostRanker = None
    Pool = None


TAIL_FEATURES = (
    "time_since_launch",
    "entry_age_seconds",
    "candidate_sequence",
    "bsc_dex_volume_24h_usd_log",
    "bsc_dex_volume_vs_7d_avg",
    "market_context_available",
    "creator_prior_launches_1h",
    "creator_prior_launches_24h",
    "creator_prior_graduations_7d",
    "symbol_prior_launches_1h",
    "symbol_prior_launches_24h",
    "global_launches_1h",
    "global_graduations_24h",
    "volume_10s",
    "volume_30s",
    "volume_1min",
    "volume_2min",
    "volume_5min",
    "buy_pressure",
    "price_change_pct",
    "price_momentum",
    "price_volatility",
    "volume_acceleration",
    "buy_volume_slope_10_30",
    "buy_volume_slope_30_60",
    "buy_count_slope_10_30",
    "unique_buyers_slope_10_30",
    "recent_new_buyer_ratio_10s",
    "recent_repeat_buyer_ratio_30s",
    "buyer_set_churn_10s_vs_prev50s",
    "holder_concentration_top5",
    "max_holder_ratio",
    "creator_buy_share",
    "creator_sell_share",
    "round_trip_buy_volume_ratio",
    "volume_price_divergence",
    "sell_pressure_30s",
    "sell_pressure_change_10_60",
    "signed_imbalance_30s",
    "signed_imbalance_60s",
)


@dataclass(frozen=True)
class TailReplayConfig:
    entry_delay_seconds: int = 3
    exit_delay_seconds: int = 3
    fee_bps: float = 100.0
    slippage_bps: float = 200.0
    min_entry_unique_buyers: int = 3
    min_entry_buy_count: int = 5
    max_entry_age_seconds: int = 300
    fixed_stake_bnb: float = 0.1
    initial_equity_bnb: float = 1.0
    max_open_positions: int | None = 8
    ranking_group_seconds: int = 3_600
    incomplete_policy: str = "last_observation"
    stop_loss_pct: float | None = None
    trailing_stop_pct: float | None = None

    def __post_init__(self) -> None:
        if self.entry_delay_seconds < 0 or self.exit_delay_seconds < 0:
            raise ValueError("execution delays must be non-negative")
        if self.fee_bps < 0 or self.slippage_bps < 0:
            raise ValueError("costs must be non-negative")
        if self.min_entry_unique_buyers < 1 or self.min_entry_buy_count < 1:
            raise ValueError("entry activity gates must be positive")
        if self.max_entry_age_seconds <= 0:
            raise ValueError("max_entry_age_seconds must be positive")
        if self.fixed_stake_bnb <= 0 or self.initial_equity_bnb <= 0:
            raise ValueError("capital values must be positive")
        if self.max_open_positions is not None and self.max_open_positions <= 0:
            raise ValueError("max_open_positions must be positive")
        if self.ranking_group_seconds <= 0:
            raise ValueError("ranking_group_seconds must be positive")
        if self.incomplete_policy not in {"last_observation", "loss"}:
            raise ValueError("incomplete_policy must be last_observation or loss")
        if self.stop_loss_pct is not None and self.stop_loss_pct >= 0:
            raise ValueError("stop_loss_pct must be negative")
        if self.trailing_stop_pct is not None and not 0 < self.trailing_stop_pct < 100:
            raise ValueError("trailing_stop_pct must be between 0 and 100")


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _timestamp(value: Any) -> float | None:
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = None
    if number is not None and math.isfinite(number):
        return number
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _token(value: Any) -> str:
    return str(value or "").strip().lower()


def _price_path(lifecycle: Mapping[str, Any]) -> list[tuple[float, float, str]]:
    points = []
    seen = set()
    for row in lifecycle.get("price_history") or []:
        if not isinstance(row, Mapping):
            continue
        timestamp = _timestamp(row.get("timestamp"))
        price = _finite(row.get("price"))
        if timestamp is None or price <= 0:
            continue
        identity = (float(timestamp), float(price), str(row.get("type") or ""))
        if identity in seen:
            continue
        seen.add(identity)
        points.append(identity)
    return sorted(points, key=lambda row: (row[0], row[2], row[1]))


def _dex_bars(lifecycle: Mapping[str, Any]) -> list[tuple[float, float, float]]:
    """Return post-graduation close/volume observations with their provenance."""
    bars = []
    for row in lifecycle.get("price_history") or []:
        if not isinstance(row, Mapping) or str(row.get("type") or "") != "dex_close":
            continue
        timestamp = _timestamp(row.get("timestamp"))
        price = _finite(row.get("price"))
        volume = _finite(row.get("dex_volume"), 0.0)
        if timestamp is None or price <= 0.0:
            continue
        bars.append((float(timestamp), float(price), max(0.0, volume)))
    return sorted(set(bars), key=lambda row: row[0])


def _point_at_or_after(
    path: Sequence[tuple[float, float, str]],
    timestamp: float,
    *,
    entry: bool = False,
):
    """Return a deterministic same-second quote.

    Multiple swaps share a chain second in the persisted data.  Use the
    highest price for an entry and the lowest price for an exit so replay does
    not manufacture an unrealistically favorable fill.
    """
    eligible = [point for point in path if point[0] >= float(timestamp) and point[1] > 0]
    if eligible:
        first_timestamp = eligible[0][0]
        same_second = [point for point in eligible if point[0] == first_timestamp]
        return (max if entry else min)(same_second, key=lambda point: point[1])
    return None


def _point_at_or_before(path: Sequence[tuple[float, float, str]], timestamp: float):
    points = [point for point in path if point[0] <= float(timestamp) and point[1] > 0]
    return points[-1] if points else None


def _effective_buy_price(raw_price: float, config: TailReplayConfig) -> float:
    return raw_price * (1.0 + (config.fee_bps + config.slippage_bps) / 10_000.0)


def _effective_sell_multiple(raw_price: float, entry_price: float, config: TailReplayConfig) -> float:
    factor = max(0.0, 1.0 - (config.fee_bps + config.slippage_bps) / 10_000.0)
    return raw_price * factor / max(entry_price, 1e-18)


def _evenly_limit(values: Sequence[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or len(values) <= limit:
        return list(values)
    if limit == 1:
        return [values[0]]
    positions = sorted({round(i * (len(values) - 1) / (limit - 1)) for i in range(limit)})
    return [values[int(position)] for position in positions]


def build_tail_candidates(
    lifecycles: Iterable[Mapping[str, Any]],
    config: TailReplayConfig | None = None,
    *,
    feature_names: Sequence[str] = TAIL_FEATURES,
    max_candidates_per_token: int = 24,
    market_context: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Create causal candidates at every qualifying early buy event.

    The old trainer kept only the first qualifying event for a token.  Keeping
    the event stream lets the ranker learn whether a slightly later, cleaner
    flow signal is preferable while replay still prevents duplicate positions.
    """
    config = config or TailReplayConfig()
    lifecycles = list(lifecycles)
    candidates: list[dict[str, Any]] = []
    # Build causal history indexes once.  Only timestamps strictly before the
    # decision are queried; a token's eventual graduation is never used before
    # its recorded graduation second.
    creator_launch_times: dict[str, list[float]] = defaultdict(list)
    creator_graduation_times: dict[str, list[float]] = defaultdict(list)
    symbol_launch_times: dict[str, list[float]] = defaultdict(list)
    global_launch_times: list[float] = []
    global_graduation_times: list[float] = []
    for lifecycle in lifecycles:
        created = _timestamp(lifecycle.get("create_timestamp", lifecycle.get("created_at")))
        if created is None:
            continue
        creator = _token(lifecycle.get("creator"))
        symbol = _token(lifecycle.get("symbol"))
        global_launch_times.append(created)
        if creator:
            creator_launch_times[creator].append(created)
        if symbol:
            symbol_launch_times[symbol].append(created)
        graduated_at = _timestamp(lifecycle.get("graduate_time"))
        if graduated_at is not None and graduated_at < float("inf"):
            global_graduation_times.append(graduated_at)
            if creator:
                creator_graduation_times[creator].append(graduated_at)
    for values in [global_launch_times, global_graduation_times, *creator_launch_times.values(), *creator_graduation_times.values(), *symbol_launch_times.values()]:
        values.sort()
    for lifecycle in lifecycles:
        create_time = _timestamp(lifecycle.get("create_timestamp", lifecycle.get("created_at")))
        token = _token(lifecycle.get("token_address") or lifecycle.get("token"))
        if create_time is None or not token:
            continue
        buys = [row for row in lifecycle.get("buys") or [] if isinstance(row, Mapping)]
        sells = [row for row in lifecycle.get("sells") or [] if isinstance(row, Mapping)]
        buys.sort(key=lambda row: (_timestamp(row.get("timestamp")) or float("inf"), str(row.get("transaction_hash") or "")))
        sells.sort(key=lambda row: (_timestamp(row.get("timestamp")) or float("inf"), str(row.get("transaction_hash") or "")))
        path = _price_path(lifecycle)
        dex_bars = _dex_bars(lifecycle)
        if not path:
            continue
        buyers: set[str] = set()
        token_candidates: list[dict[str, Any]] = []
        seen_times: set[int] = set()
        for sequence, buy in enumerate(buys):
            timestamp = _timestamp(buy.get("timestamp"))
            if timestamp is None or timestamp - create_time > config.max_entry_age_seconds:
                break
            account = _token(buy.get("account"))
            if account:
                buyers.add(account)
            if sequence + 1 < config.min_entry_buy_count or len(buyers) < config.min_entry_unique_buyers:
                continue
            sample_time = int(timestamp)
            if sample_time in seen_times:
                continue
            seen_times.add(sample_time)
            past_buys = [row for row in buys if (_timestamp(row.get("timestamp")) or float("inf")) <= sample_time]
            past_sells = [row for row in sells if (_timestamp(row.get("timestamp")) or float("inf")) <= sample_time]
            anchor = _point_at_or_before(path, sample_time)
            if anchor is None:
                continue
            features = extract_features(
                dict(lifecycle),
                past_buys,
                past_sells,
                sample_time,
                include_flow_features=True,
            )
            # These are decision-time metadata, not post-entry labels.
            features["entry_age_seconds"] = float(timestamp - create_time)
            features["candidate_sequence"] = float(sequence + 1)
            date_key = datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
            market = (market_context or {}).get(date_key) or {}
            market_volume = _finite(market.get("volume_usd"))
            features["bsc_dex_volume_24h_usd_log"] = math.log1p(max(0.0, market_volume)) if market_volume > 0 else 0.0
            features["bsc_dex_volume_vs_7d_avg"] = _finite(market.get("vs_7d_avg"))
            features["market_context_available"] = 1.0 if market_volume > 0 else 0.0
            creator = _token(lifecycle.get("creator"))
            symbol = _token(lifecycle.get("symbol"))

            def _prior_count(values: Sequence[float], window: int) -> int:
                if not values:
                    return 0
                left = bisect_left(values, float(timestamp - window))
                right = bisect_left(values, float(timestamp))
                return max(0, right - left)

            # The current token is included in launch indexes because its
            # creation precedes later entry events; remove that one row.
            creator_launch_1h = max(0, _prior_count(creator_launch_times.get(creator, []), 3_600) - 1)
            creator_launch_24h = max(0, _prior_count(creator_launch_times.get(creator, []), 86_400) - 1)
            creator_graduation_7d = _prior_count(creator_graduation_times.get(creator, []), 604_800)
            symbol_launch_1h = max(0, _prior_count(symbol_launch_times.get(symbol, []), 3_600) - 1)
            symbol_launch_24h = max(0, _prior_count(symbol_launch_times.get(symbol, []), 86_400) - 1)
            features["creator_prior_launches_1h"] = float(creator_launch_1h)
            features["creator_prior_launches_24h"] = float(creator_launch_24h)
            features["creator_prior_graduations_7d"] = float(creator_graduation_7d)
            features["symbol_prior_launches_1h"] = float(symbol_launch_1h)
            features["symbol_prior_launches_24h"] = float(symbol_launch_24h)
            features["global_launches_1h"] = float(max(0, _prior_count(global_launch_times, 3_600) - 1))
            features["global_graduations_24h"] = float(_prior_count(global_graduation_times, 86_400))
            token_candidates.append(
                {
                    "token": token,
                    "symbol": lifecycle.get("symbol"),
                    "sample_time": sample_time,
                    "create_timestamp": int(create_time),
                    "signal_price": float(anchor[1]),
                    "features": {name: _finite(features.get(name)) for name in feature_names},
                    "path": path,
                    "dex_bars": dex_bars,
                    "graduated": bool(lifecycle.get("graduated")),
                    "graduate_time": lifecycle.get("graduate_time"),
                    "cross_boundary": dict(lifecycle.get("cross_boundary") or {}),
                }
            )
            if int(max_candidates_per_token) == 1:
                break
        candidates.extend(_evenly_limit(token_candidates, int(max_candidates_per_token)))
    return sorted(candidates, key=lambda row: (int(row["sample_time"]), str(row["token"])))


def _return_for_exit(raw_exit_price: float, entry_price: float, config: TailReplayConfig) -> float:
    return (_effective_sell_multiple(raw_exit_price, entry_price, config) - 1.0) * 100.0


def _tail_utility(return_pct: float) -> float:
    """Smooth heavy tails without clipping a genuine multi-x winner."""
    wealth = max(0.0, 1.0 + float(return_pct) / 100.0)
    # Log wealth is the portfolio objective: a -100% loss is strongly
    # negative, while a rare multi-x winner remains valuable without letting
    # one outlier dominate the tree fit.
    return float(math.log(max(1e-6, wealth)))


def simulate_tail_path(
    candidate: Mapping[str, Any],
    *,
    horizon_seconds: int,
    config: TailReplayConfig | None = None,
) -> dict[str, Any]:
    """Replay a fixed-horizon hold with optional catastrophic/trailing exits."""
    config = config or TailReplayConfig()
    if horizon_seconds <= 0:
        raise ValueError("horizon_seconds must be positive")
    path = list(candidate.get("path") or [])
    sample_time = _finite(candidate.get("sample_time"))
    entry_point = _point_at_or_after(path, sample_time + config.entry_delay_seconds, entry=True)
    if entry_point is None:
        return {
            "status": "missing_entry",
            "complete": False,
            "horizon_seconds": int(horizon_seconds),
            "return_pct": -100.0,
            "conservative_return_pct": -100.0,
        }
    entry_time, entry_raw_price, _ = entry_point
    entry_price = _effective_buy_price(entry_raw_price, config)
    horizon_end = float(entry_time + horizon_seconds)
    last_time = max((point[0] for point in path), default=0.0)
    complete = last_time >= horizon_end
    future = [point for point in path if entry_time < point[0] <= horizon_end]
    peak_multiple = 1.0
    min_multiple = 1.0
    max_multiple = 1.0
    trigger = None
    exit_reason = "horizon"
    for point in future:
        multiple = _effective_sell_multiple(point[1], entry_price, config)
        peak_multiple = max(peak_multiple, multiple)
        max_multiple = max(max_multiple, multiple)
        min_multiple = min(min_multiple, multiple)
        if config.stop_loss_pct is not None and multiple <= 1.0 + config.stop_loss_pct / 100.0:
            trigger = point[0]
            exit_reason = "stop_loss"
            break
        if (
            config.trailing_stop_pct is not None
            and peak_multiple > 1.0
            and multiple <= peak_multiple * (1.0 - config.trailing_stop_pct / 100.0)
        ):
            trigger = point[0]
            exit_reason = "trailing_stop"
            break
    if trigger is not None:
        exit_point = _point_at_or_after(path, trigger + config.exit_delay_seconds)
        if exit_point is None or exit_point[0] > horizon_end:
            exit_point = None
    else:
        exit_point = _point_at_or_after(path, horizon_end)
        if exit_point is not None and exit_point[0] > horizon_end + max(config.exit_delay_seconds, 3600):
            exit_point = None
        if exit_point is None:
            eligible = [point for point in path if entry_time < point[0] <= horizon_end]
            exit_point = eligible[-1] if eligible else None
    if exit_point is None:
        return {
            "status": "missing_exit",
            "complete": bool(complete),
            "horizon_seconds": int(horizon_seconds),
            "entry_time": float(entry_time),
            "entry_price": float(entry_price),
            "return_pct": -100.0,
            "conservative_return_pct": -100.0,
            "exit_reason": exit_reason,
            "mfe_pct": float((max_multiple - 1.0) * 100.0),
            "mae_pct": float((min_multiple - 1.0) * 100.0),
        }
    realized = _return_for_exit(exit_point[1], entry_price, config)
    if complete or config.incomplete_policy == "last_observation":
        conservative = realized
        status = "ok" if complete else "incomplete_last_observation"
    else:
        conservative = -100.0
        status = "censored_loss"
    return {
        "status": status,
        "complete": bool(complete),
        "horizon_seconds": int(horizon_seconds),
        "entry_time": float(entry_time),
        "entry_price": float(entry_price),
        "exit_time": float(exit_point[0]),
        "exit_price": float(exit_point[1]),
        "return_pct": float(realized),
        "conservative_return_pct": float(conservative),
        "utility": _tail_utility(conservative),
        "exit_reason": exit_reason,
        "mfe_pct": float((max_multiple - 1.0) * 100.0),
        "mae_pct": float((min_multiple - 1.0) * 100.0),
        "time_to_exit_seconds": float(exit_point[0] - entry_time),
        "tail_10x": bool(max_multiple >= 10.0),
    }


def _token_time_splits(candidates: Sequence[Mapping[str, Any]], purge_seconds: int) -> dict[str, list[int]]:
    tokens: dict[str, list[int]] = defaultdict(list)
    first_time: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        token = _token(candidate.get("token"))
        timestamp = int(_finite(candidate.get("sample_time")))
        tokens[token].append(index)
        first_time[token] = min(timestamp, first_time.get(token, timestamp))
    ordered_tokens = sorted(tokens, key=lambda token: (first_time[token], token))
    train_end = max(1, int(len(ordered_tokens) * 0.60)) if ordered_tokens else 0
    validation_end = min(len(ordered_tokens), max(train_end + 1, int(len(ordered_tokens) * 0.80)))
    buckets = {
        "train": [index for token in ordered_tokens[:train_end] for index in tokens[token]],
        "validation": [index for token in ordered_tokens[train_end:validation_end] for index in tokens[token]],
        "final": [index for token in ordered_tokens[validation_end:] for index in tokens[token]],
    }
    validation_start = min(
        (int(_finite(candidates[index].get("sample_time"))) for index in buckets["validation"]),
        default=None,
    )
    final_start = min(
        (int(_finite(candidates[index].get("sample_time"))) for index in buckets["final"]),
        default=None,
    )
    if purge_seconds > 0 and validation_start is not None:
        buckets["train"] = [
            index for index in buckets["train"]
            if int(_finite(candidates[index].get("sample_time"))) + purge_seconds < validation_start
        ]
    if purge_seconds > 0 and final_start is not None:
        buckets["validation"] = [
            index for index in buckets["validation"]
            if int(_finite(candidates[index].get("sample_time"))) + purge_seconds < final_start
        ]
    for key in buckets:
        buckets[key].sort(key=lambda index: (int(_finite(candidates[index].get("sample_time"))), _token(candidates[index].get("token"))))
    return buckets


def _feature_frame(candidates: Sequence[Mapping[str, Any]], indices: Sequence[int], feature_names: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                name: _finite((candidates[index].get("features") or {}).get(name))
                for name in feature_names
            }
            for index in indices
        ],
        columns=list(feature_names),
        dtype=float,
    )


def _group_ids(
    candidates: Sequence[Mapping[str, Any]],
    indices: Sequence[int],
    *,
    group_seconds: int = 3_600,
) -> list[int]:
    groups: dict[str, int] = {}
    result = []
    for index in indices:
        timestamp = int(_finite(candidates[index].get("sample_time")))
        bucket = str(timestamp // max(1, int(group_seconds)))
        groups.setdefault(bucket, len(groups) + 1)
        result.append(groups[bucket])
    return result


def _ordered_group_indices(
    candidates: Sequence[Mapping[str, Any]],
    indices: Sequence[int],
    *,
    group_seconds: int = 3_600,
) -> list[int]:
    return sorted(
        indices,
        key=lambda index: (
            int(_finite(candidates[index].get("sample_time"))) // max(1, int(group_seconds)),
            int(_finite(candidates[index].get("sample_time"))),
            _token(candidates[index].get("token")),
        ),
    )


def _fit_ranker(
    candidates,
    feature_names,
    train_indices,
    validation_indices,
    labels,
    params,
    *,
    ranking_group_seconds: int = 3_600,
):
    if CatBoostRanker is None or Pool is None:
        raise ModuleNotFoundError("catboost is required for tail capture training")
    train_indices = _ordered_group_indices(candidates, train_indices, group_seconds=ranking_group_seconds)
    validation_indices = _ordered_group_indices(candidates, validation_indices, group_seconds=ranking_group_seconds)
    model_params = {
        "iterations": 300,
        "learning_rate": 0.04,
        "depth": 4,
        "l2_leaf_reg": 20.0,
        "random_strength": 1.0,
        "od_type": "Iter",
        "od_wait": 35,
    }
    model_params.update(dict(params or {}))
    model = CatBoostRanker(
        loss_function="YetiRank",
        eval_metric="NDCG:top=25",
        random_seed=42,
        verbose=False,
        allow_writing_files=False,
        **model_params,
    )
    train_pool = Pool(
        _feature_frame(candidates, train_indices, feature_names),
        label=[labels[index] for index in train_indices],
        group_id=_group_ids(candidates, train_indices, group_seconds=ranking_group_seconds),
    )
    eval_pool = None
    if validation_indices and len(set(_group_ids(candidates, validation_indices, group_seconds=ranking_group_seconds))) >= 1:
        eval_pool = Pool(
            _feature_frame(candidates, validation_indices, feature_names),
            label=[labels[index] for index in validation_indices],
            group_id=_group_ids(candidates, validation_indices, group_seconds=ranking_group_seconds),
        )
    model.fit(train_pool, eval_set=eval_pool, use_best_model=bool(eval_pool))
    return model


def _fit_classifier(candidates, feature_names, train_indices, validation_indices, labels):
    if CatBoostClassifier is None:
        return None
    train_indices = list(train_indices)
    if len(set(labels[index] for index in train_indices)) < 2:
        return None
    model = CatBoostClassifier(
        loss_function="Logloss",
        eval_metric="AUC",
        random_seed=42,
        verbose=False,
        allow_writing_files=False,
        auto_class_weights="Balanced",
        iterations=300,
        learning_rate=0.04,
        depth=4,
        l2_leaf_reg=20.0,
        random_strength=1.0,
        od_type="Iter",
        od_wait=35,
    )
    x_train = _feature_frame(candidates, train_indices, feature_names)
    y_train = [labels[index] for index in train_indices]
    eval_set = None
    if validation_indices and len(set(labels[index] for index in validation_indices)) >= 2:
        eval_set = (
            _feature_frame(candidates, validation_indices, feature_names),
            [labels[index] for index in validation_indices],
        )
    model.fit(x_train, y_train, eval_set=eval_set, use_best_model=bool(eval_set))
    return model


def _score_thresholds(scores: Sequence[float]) -> list[float]:
    finite = sorted(float(value) for value in scores if math.isfinite(float(value)))
    if not finite:
        return [float("inf")]
    return sorted({finite[-1], *(float(np.quantile(finite, q)) for q in (0.80, 0.90, 0.95, 0.98))})


def _portfolio_backtest(candidates, outcomes, scores, indices, config, threshold):
    cash = float(config.initial_equity_bnb)
    open_positions: list[tuple[float, float, float]] = []
    entered_tokens: set[str] = set()
    equity_points = [cash]
    signal_count = capacity_skips = cash_skips = duplicate_skips = entry_failures = 0

    def close_until(timestamp: float) -> None:
        nonlocal cash, open_positions
        remaining = []
        for exit_time, stake, return_pct in open_positions:
            if exit_time <= timestamp:
                cash += stake * max(0.0, 1.0 + return_pct / 100.0)
                equity_points.append(cash)
            else:
                remaining.append((exit_time, stake, return_pct))
        open_positions = remaining

    for index in sorted(indices, key=lambda item: (int(_finite(candidates[item].get("sample_time"))), _token(candidates[item].get("token")))):
        if _finite(scores[index], -math.inf) < float(threshold):
            continue
        signal_count += 1
        outcome = outcomes[index]
        if outcome.get("status") == "missing_entry":
            entry_failures += 1
            continue
        entry_time = _finite(outcome.get("entry_time"), _finite(candidates[index].get("sample_time")))
        close_until(entry_time)
        token = _token(candidates[index].get("token"))
        if token in entered_tokens:
            duplicate_skips += 1
            continue
        if config.max_open_positions is not None and len(open_positions) >= int(config.max_open_positions):
            capacity_skips += 1
            continue
        if cash + 1e-12 < config.fixed_stake_bnb:
            cash_skips += 1
            continue
        cash -= config.fixed_stake_bnb
        entered_tokens.add(token)
        return_pct = _finite(outcome.get("conservative_return_pct"), -100.0)
        exit_time = _finite(outcome.get("exit_time"), entry_time)
        open_positions.append((max(entry_time, exit_time), config.fixed_stake_bnb, return_pct))
    close_until(float("inf"))
    peak = float(config.initial_equity_bnb)
    max_drawdown = 0.0
    for point in equity_points:
        peak = max(peak, point)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - point) / peak)
    final_equity = float(cash)
    trade_count = signal_count - capacity_skips - cash_skips - duplicate_skips - entry_failures
    return {
        "threshold": float(threshold),
        "signal_count": int(signal_count),
        "trade_count": int(max(0, trade_count)),
        "capacity_skips": int(capacity_skips),
        "cash_insufficient_skips": int(cash_skips),
        "duplicate_token_skips": int(duplicate_skips),
        "entry_failures": int(entry_failures),
        "final_equity_bnb": final_equity,
        "net_profit_bnb": final_equity - config.initial_equity_bnb,
        "net_return_pct": (final_equity / config.initial_equity_bnb - 1.0) * 100.0,
        "max_drawdown_pct": max_drawdown * 100.0,
    }


def _rank_summary(candidates, outcomes, scores, indices, *, fixed_stake_bnb: float = 0.1):
    rows = []
    for name, count in (("top_1pct", max(1, math.ceil(len(indices) * 0.01))), ("top_5pct", max(1, math.ceil(len(indices) * 0.05))), ("top_10pct", max(1, math.ceil(len(indices) * 0.10))), ("top_25", 25), ("top_50", 50)):
        selected = sorted(indices, key=lambda index: (_finite(scores[index], -math.inf), -index), reverse=True)[:count]
        returns = [_finite(outcomes[index].get("conservative_return_pct"), -100.0) for index in selected]
        tail_hits = sum(bool(outcomes[index].get("tail_10x")) for index in selected)
        rows.append({
            "selection": name,
            "candidate_count": len(selected),
            "mean_return_pct": float(np.mean(returns)) if returns else 0.0,
            "net_profit_bnb": float(sum(fixed_stake_bnb * value / 100.0 for value in returns)),
            "positive_rate": float(sum(value > 0 for value in returns) / len(returns)) if returns else 0.0,
            "tail_10x_hits": int(tail_hits),
            "tail_10x_precision": float(tail_hits / len(selected)) if selected else 0.0,
            "complete_count": int(sum(bool(outcomes[index].get("complete")) for index in selected)),
        })
    return rows


def _stress_configs(config: TailReplayConfig) -> dict[str, TailReplayConfig]:
    return {
        "base": config,
        "moderate": replace(config, fee_bps=max(config.fee_bps, 150.0), slippage_bps=max(config.slippage_bps, 400.0)),
        "harsh": replace(config, fee_bps=max(config.fee_bps, 250.0), slippage_bps=max(config.slippage_bps, 800.0)),
    }


def _expanding_walk_forward(
    candidates: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    labels: Sequence[float],
    *,
    horizon_seconds: int,
    config: TailReplayConfig,
    feature_names: Sequence[str],
    complete_indices: set[int],
) -> list[dict[str, Any]]:
    """Refit on expanding token-time slices and evaluate the next slice."""
    first_time: dict[str, int] = {}
    for candidate in candidates:
        token = _token(candidate.get("token"))
        timestamp = int(_finite(candidate.get("sample_time")))
        first_time[token] = min(timestamp, first_time.get(token, timestamp))
    ordered_tokens = sorted(first_time, key=lambda token: (first_time[token], token))
    if len(ordered_tokens) < 20:
        return []
    reports = []
    # The final slice remains untouched by threshold/model selection in the
    # main experiment; these folds provide an independent expanding check.
    for fold, (train_fraction, eval_fraction) in enumerate(((0.45, 0.15), (0.60, 0.15), (0.75, 0.15)), start=1):
        train_end = max(1, int(len(ordered_tokens) * train_fraction))
        eval_end = min(len(ordered_tokens), max(train_end + 1, int(len(ordered_tokens) * (train_fraction + eval_fraction))))
        train_token_set = set(ordered_tokens[:train_end])
        eval_token_set = set(ordered_tokens[train_end:eval_end])
        train_indices = [
            index for index, candidate in enumerate(candidates)
            if _token(candidate.get("token")) in train_token_set and index in complete_indices
        ]
        eval_indices = [
            index for index, candidate in enumerate(candidates)
            if _token(candidate.get("token")) in eval_token_set
        ]
        eval_complete = [index for index in eval_indices if index in complete_indices]
        eval_start = min((int(_finite(candidates[index].get("sample_time"))) for index in eval_indices), default=None)
        if eval_start is not None:
            train_indices = [
                index for index in train_indices
                if int(_finite(candidates[index].get("sample_time"))) + int(horizon_seconds) < eval_start
            ]
        if len(train_indices) < 20 or len(eval_complete) < 5 or not eval_indices:
            reports.append({"fold": fold, "status": "insufficient_support", "train_count": len(train_indices), "eval_count": len(eval_indices)})
            continue
        ranker = _fit_ranker(
            candidates,
            feature_names,
            train_indices,
            eval_complete,
            labels,
            {"iterations": 180},
            ranking_group_seconds=config.ranking_group_seconds,
        )
        train_scores = ranker.predict(_feature_frame(candidates, train_indices, feature_names))
        eval_scores = ranker.predict(_feature_frame(candidates, eval_indices, feature_names))
        threshold = float(np.quantile(train_scores, 0.90)) if len(train_scores) else float("inf")
        # Map fold-local predictions back to global indices for portfolio code.
        score_map = {index: float(score) for index, score in zip(eval_indices, eval_scores)}
        score_vector = [score_map.get(index, -math.inf) for index in range(len(candidates))]
        reports.append({
            "fold": fold,
            "status": "ok",
            "train_count": len(train_indices),
            "eval_count": len(eval_indices),
            "eval_complete_count": len(eval_complete),
            "threshold_from_train_p90": threshold,
            "portfolio": _portfolio_backtest(candidates, outcomes, score_vector, eval_indices, config, threshold),
        })
    return reports


def train_tail_capture_experiment(
    lifecycles: Sequence[Mapping[str, Any]],
    *,
    horizon_seconds: int,
    config: TailReplayConfig | None = None,
    feature_names: Sequence[str] = TAIL_FEATURES,
    positive_return_pct: float = 100.0,
    model_params: Mapping[str, Any] | None = None,
    output_dir: str | Path | None = None,
    market_context: Mapping[str, Mapping[str, Any]] | None = None,
    max_candidates_per_token: int = 24,
    rank_target: str = "utility",
) -> dict[str, Any]:
    """Train a fresh tail ranker and evaluate a causal, capacity-limited replay."""
    config = config or TailReplayConfig()
    rank_target = str(rank_target or "utility").strip().lower()
    if rank_target not in {"utility", "mfe"}:
        raise ValueError("rank_target must be utility or mfe")
    feature_names = list(feature_names)
    candidates = build_tail_candidates(
        lifecycles,
        config,
        feature_names=feature_names,
        market_context=market_context,
        max_candidates_per_token=max_candidates_per_token,
    )
    outcomes = [simulate_tail_path(candidate, horizon_seconds=horizon_seconds, config=config) for candidate in candidates]
    splits = _token_time_splits(candidates, int(horizon_seconds))
    eligible = [index for index, outcome in enumerate(outcomes) if outcome.get("complete") and outcome.get("status") == "ok"]
    # Complete paths are the only rows allowed to teach the model.  Portfolio
    # replay still uses every candidate in the split so inactive/censored
    # tokens remain visible as real capital losses or explicit skips.
    train_model_indices = [index for index in splits["train"] if index in eligible]
    validation_model_indices = [index for index in splits["validation"] if index in eligible]
    final_model_indices = [index for index in splits["final"] if index in eligible]
    result: dict[str, Any] = {
        "schema_version": 1,
        "experiment_type": "bsc_tail_capture_event_ranker",
        "inputs": {
            "horizon_seconds": int(horizon_seconds),
            "candidate_count": len(candidates),
            "token_count": len({_token(row.get("token")) for row in candidates}),
            "feature_names": feature_names,
            "positive_return_pct": float(positive_return_pct),
            "rank_target": rank_target,
            "config": {key: getattr(config, key) for key in config.__dataclass_fields__},
        },
        "split_counts": {
            name: {
                "candidate_count": len(indices),
                "complete_count": sum(index in eligible for index in indices),
                "token_count": len({_token(candidates[index].get("token")) for index in indices}),
            }
            for name, indices in splits.items()
        },
        "path_coverage": {
            "candidate_count": len(candidates),
            "complete_count": len(eligible),
            "complete_rate": float(len(eligible) / len(candidates)) if candidates else 0.0,
            "incomplete_status_counts": {
                status: sum(outcome.get("status") == status for outcome in outcomes)
                for status in sorted({str(outcome.get("status")) for outcome in outcomes if outcome.get("status") != "ok"})
            },
        },
        "decision": "research_only_until_final_walk_forward_and_stress_support",
    }
    if len(train_model_indices) < 20 or len(validation_model_indices) < 5 or len(final_model_indices) < 5:
        result["status"] = "insufficient_complete_paths"
        result["reason"] = "complete train/validation/final support is too small for a fresh model"
        return result
    labels = np.asarray(
        [
            _tail_utility(
                _finite(
                    outcome.get("mfe_pct") if rank_target == "mfe" else outcome.get("conservative_return_pct"),
                    -100.0,
                )
            )
            for outcome in outcomes
        ],
        dtype=float,
    )
    tail_labels = np.asarray([1 if _finite(outcome.get("conservative_return_pct"), -100.0) >= positive_return_pct else 0 for outcome in outcomes], dtype=int)
    ranker = _fit_ranker(
        candidates,
        feature_names,
        train_model_indices,
        validation_model_indices,
        labels,
        model_params,
        ranking_group_seconds=config.ranking_group_seconds,
    )
    classifier = _fit_classifier(
        candidates,
        feature_names,
        train_model_indices,
        validation_model_indices,
        tail_labels,
    )
    all_indices = list(range(len(candidates)))
    all_scores = ranker.predict(_feature_frame(candidates, all_indices, feature_names))
    classifier_scores = None
    if classifier is not None:
        classifier_scores = classifier.predict_proba(_feature_frame(candidates, all_indices, feature_names))[:, 1]
    train_score_values = [all_scores[index] for index in train_model_indices]
    thresholds = _score_thresholds(train_score_values)
    validation_grid = [
        _portfolio_backtest(candidates, outcomes, all_scores, splits["validation"], config, threshold)
        for threshold in thresholds
    ]
    eligible_grid = [row for row in validation_grid if row["trade_count"] >= 5]
    selected = max(eligible_grid or validation_grid, key=lambda row: (row["net_profit_bnb"], row["net_return_pct"], -row["trade_count"]))
    if selected["net_profit_bnb"] <= 0:
        no_trade = [row for row in validation_grid if row["trade_count"] == 0]
        if no_trade:
            selected = no_trade[0]
    selected_threshold = float(selected["threshold"])
    baseline_scores = [0.0] * len(candidates)
    model_indices_by_split = {
        "train": train_model_indices,
        "validation": validation_model_indices,
        "final": final_model_indices,
    }
    split_reports = {}
    for name, indices in (("train", splits["train"]), ("validation", splits["validation"]), ("final", splits["final"])):
        split_reports[name] = {
            "candidate_count": len(indices),
            "tail_positive_count": int(sum(tail_labels[index] for index in indices)),
            "ranker": _rank_summary(
                candidates,
                outcomes,
                all_scores,
                indices,
                fixed_stake_bnb=config.fixed_stake_bnb,
            ),
            "portfolio": _portfolio_backtest(candidates, outcomes, all_scores, indices, config, selected_threshold),
            "earliest_signal_baseline": _portfolio_backtest(
                candidates,
                outcomes,
                baseline_scores,
                indices,
                config,
                -1e30,
            ),
            "complete_only_portfolio": _portfolio_backtest(
                candidates,
                outcomes,
                all_scores,
                model_indices_by_split[name],
                config,
                selected_threshold,
            ),
        }
        if classifier_scores is not None:
            split_reports[name]["classifier_top_10pct"] = _rank_summary(
                candidates,
                outcomes,
                classifier_scores,
                indices,
                fixed_stake_bnb=config.fixed_stake_bnb,
            )[0]
    stress_reports = {}
    for stress_name, stress_config in _stress_configs(config).items():
        stress_outcomes = [simulate_tail_path(candidate, horizon_seconds=horizon_seconds, config=stress_config) for candidate in candidates]
        stress_reports[stress_name] = {
            name: _portfolio_backtest(candidates, stress_outcomes, all_scores, indices, stress_config, selected_threshold)
            for name, indices in (("validation", splits["validation"]), ("final", splits["final"]))
        }
        stress_reports[stress_name]["earliest_signal_baseline"] = {
            name: _portfolio_backtest(candidates, stress_outcomes, baseline_scores, indices, stress_config, -1e30)
            for name, indices in (("validation", splits["validation"]), ("final", splits["final"]))
        }
        stress_reports[stress_name]["complete_only"] = {
            name: _portfolio_backtest(
                candidates,
                stress_outcomes,
                all_scores,
                model_indices_by_split[name],
                stress_config,
                selected_threshold,
            )
            for name in ("validation", "final")
        }
    walk_forward = _expanding_walk_forward(
        candidates,
        outcomes,
        labels,
        horizon_seconds=horizon_seconds,
        config=config,
        feature_names=feature_names,
        complete_indices=set(eligible),
    )
    final_portfolio = split_reports["final"]["portfolio"]
    final_complete_portfolio = split_reports["final"]["complete_only_portfolio"]
    moderate_final = stress_reports["moderate"]["final"]
    harsh_final = stress_reports["harsh"]["final"]
    usable_walk_forward = [row for row in walk_forward if row.get("status") == "ok"]
    positive_walk_forward = sum(
        _finite((row.get("portfolio") or {}).get("net_profit_bnb"), -math.inf) > 0
        for row in usable_walk_forward
    )
    acceptance_checks = {
        "minimum_validation_trades_20": split_reports["validation"]["portfolio"]["trade_count"] >= 20,
        "minimum_final_trades_20": final_portfolio["trade_count"] >= 20,
        "minimum_final_complete_trades_10": final_complete_portfolio["trade_count"] >= 10,
        "final_net_profit_positive": _finite(final_portfolio.get("net_profit_bnb"), -math.inf) > 0,
        "final_complete_only_net_profit_positive": _finite(final_complete_portfolio.get("net_profit_bnb"), -math.inf) > 0,
        "moderate_stress_net_profit_non_negative": _finite(moderate_final.get("net_profit_bnb"), -math.inf) >= 0,
        "harsh_stress_net_profit_non_negative": _finite(harsh_final.get("net_profit_bnb"), -math.inf) >= 0,
        "two_positive_walk_forward_folds": positive_walk_forward >= 2,
    }
    result.update({
        "status": "ok",
        "selected_threshold": selected_threshold,
        "threshold_grid_validation": validation_grid,
        "splits": split_reports,
        "stress_replay": stress_reports,
        "walk_forward": walk_forward,
        "research_acceptance": {
            "checks": acceptance_checks,
            "passes": bool(all(acceptance_checks.values())),
            "runtime_switch": "none",
            "reason": "A positive final split with fewer than 20 trades or without stress and walk-forward support is not evidence of a deployable edge.",
        },
        "model": {
            "ranker": "trained_fresh",
            "classifier": "trained_fresh" if classifier is not None else "unavailable_single_class",
        },
        "feature_importance": [
            {"feature": name, "importance": float(value)}
            for name, value in sorted(
                zip(feature_names, ranker.get_feature_importance(type="PredictionValuesChange")),
                key=lambda item: float(item[1]),
                reverse=True,
            )[:30]
        ],
        "safe_for_live_switch": False,
    })
    if output_dir is not None:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        ranker_path = output / "tail_ranker.cbm"
        ranker.save_model(str(ranker_path))
        classifier_path = None
        if classifier is not None:
            classifier_path = output / "tail_classifier.cbm"
            classifier.save_model(str(classifier_path))
        schema_path = output / "feature_schema.json"
        schema_path.write_text(
            json.dumps({"feature_names": feature_names}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result["artifacts"] = {
            "ranker": str(ranker_path),
            "classifier": str(classifier_path) if classifier_path else None,
            "feature_schema": str(schema_path),
        }
    return result


__all__ = [
    "TAIL_FEATURES",
    "TailReplayConfig",
    "build_tail_candidates",
    "simulate_tail_path",
    "train_tail_capture_experiment",
]

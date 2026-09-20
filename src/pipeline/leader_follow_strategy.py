"""Causal leader/follower signal extraction for BSC meme launches.

This is a rule-based event probe, deliberately separate from the generic
return models.  A wallet is considered a candidate leader only when it has
appeared in the first minute of several *earlier* token launches.  The current
token's future outcome is never used to define the leader score.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.data.feature_extractor import extract_features
from src.pipeline.tail_capture_strategy import (
    TailReplayConfig,
    _finite,
    _point_at_or_before,
    _price_path,
    _token,
    simulate_tail_path,
)


@dataclass(frozen=True)
class LeaderFollowConfig:
    early_window_seconds: int = 60
    history_window_seconds: int = 7 * 24 * 3600
    min_prior_tokens: int = 3
    min_early_buyers: int = 2
    follower_delay_seconds: int = 10
    replay: TailReplayConfig = TailReplayConfig(
        entry_delay_seconds=0,
        exit_delay_seconds=3,
        trailing_stop_pct=40.0,
    )

    def __post_init__(self) -> None:
        if self.early_window_seconds <= 0 or self.history_window_seconds <= 0:
            raise ValueError("leader windows must be positive")
        if self.min_prior_tokens < 1 or self.min_early_buyers < 1:
            raise ValueError("leader thresholds must be positive")
        if self.follower_delay_seconds < 0:
            raise ValueError("follower_delay_seconds must be non-negative")


def _sorted_buy_rows(lifecycle: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [row for row in lifecycle.get("buys") or [] if isinstance(row, Mapping)]
    return sorted(rows, key=lambda row: (_finite(row.get("timestamp"), float("inf")), str(row.get("transaction_hash") or "")))


def build_leader_follow_candidates(
    lifecycles: Iterable[Mapping[str, Any]],
    config: LeaderFollowConfig | None = None,
) -> list[dict[str, Any]]:
    """Build one causal follower candidate per token with recognized leaders."""
    config = config or LeaderFollowConfig()
    lifecycles = list(lifecycles)
    wallet_records: dict[str, list[tuple[float, str, float]]] = defaultdict(list)
    for lifecycle in lifecycles:
        token = _token(lifecycle.get("token_address") or lifecycle.get("token"))
        create_time = _finite(lifecycle.get("create_timestamp", lifecycle.get("created_at")), 0.0)
        if not token or create_time <= 0.0:
            continue
        seen_wallets: set[str] = set()
        for buy in _sorted_buy_rows(lifecycle):
            timestamp = _finite(buy.get("timestamp"), 0.0)
            account = _token(buy.get("account"))
            if not account or timestamp < create_time or timestamp > create_time + config.early_window_seconds:
                continue
            if account in seen_wallets:
                continue
            seen_wallets.add(account)
            wallet_records[account].append((create_time, token, _finite(buy.get("bnb_amount"))))
    for rows in wallet_records.values():
        rows.sort(key=lambda row: (row[0], row[1]))

    candidates: list[dict[str, Any]] = []
    for lifecycle in lifecycles:
        token = _token(lifecycle.get("token_address") or lifecycle.get("token"))
        create_time = _finite(lifecycle.get("create_timestamp", lifecycle.get("created_at")), 0.0)
        path = _price_path(lifecycle)
        if not token or create_time <= 0.0 or not path:
            continue
        buys = _sorted_buy_rows(lifecycle)
        early_buys = [
            row for row in buys
            if create_time <= _finite(row.get("timestamp"), 0.0) <= create_time + config.early_window_seconds
        ]
        early_accounts = {_token(row.get("account")) for row in early_buys if _token(row.get("account"))}
        if len(early_accounts) < config.min_early_buyers:
            continue
        leaders = []
        for account in sorted(early_accounts):
            prior = [
                row for row in wallet_records.get(account, [])
                if row[1] != token
                and create_time - config.history_window_seconds <= row[0] < create_time
            ]
            prior_tokens = len({row[1] for row in prior})
            if prior_tokens >= config.min_prior_tokens:
                first_buy = min(
                    _finite(row.get("timestamp"), create_time + config.early_window_seconds)
                    for row in early_buys
                    if _token(row.get("account")) == account
                )
                leaders.append({
                    "account": account,
                    "prior_token_count": prior_tokens,
                    "first_buy_time": first_buy,
                    "prior_volume_bnb": float(sum(row[2] for row in prior)),
                })
        if not leaders:
            continue
        source_time = min(row["first_buy_time"] for row in leaders)
        anchor = _point_at_or_before(path, source_time)
        if anchor is None:
            continue
        past_buys = [row for row in buys if _finite(row.get("timestamp"), 0.0) <= source_time]
        past_sells = [
            row for row in lifecycle.get("sells") or []
            if isinstance(row, Mapping) and _finite(row.get("timestamp"), 0.0) <= source_time
        ]
        features = extract_features(dict(lifecycle), past_buys, past_sells, int(source_time), include_flow_features=True)
        features.update({
            "leader_count": float(len(leaders)),
            "leader_max_prior_tokens": float(max(row["prior_token_count"] for row in leaders)),
            "leader_mean_prior_tokens": float(sum(row["prior_token_count"] for row in leaders) / len(leaders)),
            "leader_share_of_early_buyers": float(len(leaders) / len(early_accounts)),
            "leader_prior_volume_bnb": float(sum(row["prior_volume_bnb"] for row in leaders)),
            "leader_source_age_seconds": float(source_time - create_time),
        })
        candidates.append({
            "token": token,
            "symbol": lifecycle.get("symbol"),
            "sample_time": int(source_time),
            "create_timestamp": int(create_time),
            "signal_price": float(anchor[1]),
            "features": {key: _finite(value) for key, value in features.items()},
            "path": path,
            "graduated": bool(lifecycle.get("graduated")),
            "graduate_time": lifecycle.get("graduate_time"),
            "leaders": leaders,
        })
    return sorted(candidates, key=lambda row: (int(row["sample_time"]), str(row["token"])))


def simulate_leader_follow(
    candidate: Mapping[str, Any],
    *,
    horizon_seconds: int,
    config: LeaderFollowConfig | None = None,
) -> dict[str, Any]:
    """Replay a follower entry after the first recognized leader buy."""
    config = config or LeaderFollowConfig()
    synthetic = dict(candidate)
    synthetic["sample_time"] = int(_finite(candidate.get("sample_time"))) + int(config.follower_delay_seconds) - config.replay.entry_delay_seconds
    outcome = simulate_tail_path(synthetic, horizon_seconds=horizon_seconds, config=config.replay)
    outcome.update({
        "leader_count": len(candidate.get("leaders") or []),
        "leader_max_prior_tokens": max(
            (int(row.get("prior_token_count", 0)) for row in candidate.get("leaders") or []),
            default=0,
        ),
        "follower_delay_seconds": int(config.follower_delay_seconds),
    })
    return outcome


__all__ = ["LeaderFollowConfig", "build_leader_follow_candidates", "simulate_leader_follow"]

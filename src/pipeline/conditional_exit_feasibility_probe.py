from __future__ import annotations

import datetime as dt
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_DIR = PROJECT_ROOT / "docs" / "research" / "20260521-conditional-exit-flow-state"
DEFAULT_MODEL_DIR = "data/models/20260519_v95_v84_selective_nearmiss_gate"
DEFAULT_LIVE_ATTRIBUTION = RESEARCH_DIR / "live_attribution.json"
DEFAULT_TRAIN_POST_TARGET = PROJECT_ROOT / "data" / "replay_reports" / "post_target_exit_state_probe_20260521_v95_train.json"
DEFAULT_VALIDATION_POST_TARGET = PROJECT_ROOT / "data" / "replay_reports" / "post_target_exit_state_probe_20260521_v95_validation.json"
DEFAULT_FINAL_POST_TARGET = PROJECT_ROOT / "data" / "replay_reports" / "post_target_exit_state_probe_20260521_v95_final.json"
DEFAULT_DEAD_FLOW_SUPPORT = RESEARCH_DIR / "dead-flow-support.json"

BUCKET_DEFINITIONS = {
    "post_target_collapse_or_live_mfe_giveback": {
        "decision_scope": "path_observed_from_entry_time_to_exit_decision_time_only",
        "live_label": "mfe_then_giveback",
        "replay_label": "post_target_collapse",
        "shared_required_shape": "hit +25% after entry, then collapse before durable continuation or close as loss",
        "shared_target_threshold_pct": 25,
    },
    "dead_flow_timeout": {
        "decision_scope": "entry_time_to_timeout_window_only",
        "live_label": "dead_flow_timeout",
        "shared_required_shape": "no meaningful post-entry MFE and timeout exit with heavy sell pressure",
    },
    "entry_slippage_failure": {
        "decision_scope": "signal_to_open_window_only",
        "live_label": "entry_slippage_failure",
        "shared_required_shape": "signal is tradable on paper but fill lag/slippage makes entry fragile",
    },
}

SUPPORT_GATES = {
    "min_validation_positives": 3,
    "min_final_positives": 3,
    "min_live_positives": 3,
}


def _json_default(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


def to_json_text(report: Mapping[str, Any]) -> str:
    return json.dumps(
        _json_sanitize(report),
        default=_json_default,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def _json_sanitize(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {key: _json_sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_sanitize(item) for item in value]
    return value


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _count_mapping(value: Any) -> dict[str, int]:
    counts = _as_mapping(value)
    normalized: dict[str, int] = {}
    for key, count in counts.items():
        try:
            parsed = int(count)
        except (TypeError, ValueError):
            continue
        if str(key):
            normalized[str(key)] = parsed
    return dict(sorted(normalized.items()))


def _counter_from_trade_rows(trades: list[dict[str, Any]], *, label_key: str) -> Counter:
    return Counter(
        str(row.get(label_key) or "")
        for row in trades
        if str(row.get(label_key) or "")
    )


def _live_trades(live_attribution: Mapping[str, Any]) -> list[dict[str, Any]]:
    trades = live_attribution.get("trades")
    if trades is None:
        trades = live_attribution.get("trade_sample") or []
    return [dict(trade) for trade in trades if isinstance(trade, Mapping)]


def _live_failure_counts(live_attribution: Mapping[str, Any], live_trades: list[dict[str, Any]]) -> dict[str, int]:
    reported_counts = _count_mapping(live_attribution.get("failure_label_counts"))
    if reported_counts:
        return reported_counts
    return dict(sorted(_counter_from_trade_rows(live_trades, label_key="failure_label").items()))


def _trade_symbols(trades: list[dict[str, Any]], *, failure_label: str) -> list[str]:
    return [str(row.get("symbol") or row.get("token") or "") for row in trades if row.get("failure_label") == failure_label]


def _entry_plus25_count(trades: list[dict[str, Any]]) -> int:
    count = 0
    for trade in trades:
        anchor = trade.get("entry_anchor") or {}
        if isinstance(anchor, Mapping) and anchor.get("time_to_plus_25_seconds") is not None:
            count += 1
    return count


def _near_threshold_breakdown(trades: list[dict[str, Any]]) -> dict[str, Any]:
    near_trades = [trade for trade in trades if bool(trade.get("near_threshold_like"))]
    primary_trades = [trade for trade in trades if not bool(trade.get("near_threshold_like"))]
    return {
        "near_trade_count": len(near_trades),
        "near_failure_labels": dict(sorted(_counter_from_trade_rows(near_trades, label_key="failure_label").items())),
        "primary_trade_count": len(primary_trades),
        "primary_failure_labels": dict(sorted(_counter_from_trade_rows(primary_trades, label_key="failure_label").items())),
    }


def _split_counts(report: Mapping[str, Any]) -> dict[str, Any]:
    candidate_counts = _as_mapping(report.get("candidate_counts"))
    return {
        "candidate_counts": candidate_counts or None,
        "class_counts": _as_mapping(report.get("class_counts")) or None,
        "policy_counts": _as_mapping(report.get("policy_counts")) or None,
    }


def _support_check(
    *,
    bucket: str,
    meaning: str,
    train_positives: int | None,
    validation_positives: int | None,
    final_positives: int | None,
    live_positives: int | None,
) -> dict[str, Any]:
    passes = (
        train_positives is not None
        and validation_positives is not None
        and final_positives is not None
        and live_positives is not None
        and validation_positives >= SUPPORT_GATES["min_validation_positives"]
        and final_positives >= SUPPORT_GATES["min_final_positives"]
        and live_positives >= SUPPORT_GATES["min_live_positives"]
    )
    if bucket == "post_target_collapse_or_live_mfe_giveback" and validation_positives == 0:
        reason = "validation_positives is 0, below the >=3 support gate; selecting a live exit rule now would be final/live overfit."
    elif bucket == "dead_flow_timeout":
        reason = "current replay probes do not emit the same dead-flow timeout label for train/validation/final, so this cannot yet select a deployable rule."
    else:
        reason = f"live support is only {live_positives or 0} and replay-equivalent slippage labels are absent in the current probe set."
    return {
        "bucket": bucket,
        "meaning": meaning,
        "train_positives": train_positives,
        "validation_positives": validation_positives,
        "final_positives": final_positives,
        "live_positives": live_positives,
        "passes_min_support_gate": passes,
        "falsification_reason": reason,
    }


def build_feasibility_report(
    *,
    live_attribution: Mapping[str, Any],
    train_post_target_report: Mapping[str, Any],
    validation_post_target_report: Mapping[str, Any],
    final_post_target_report: Mapping[str, Any],
    dead_flow_support_report: Mapping[str, Any] | None = None,
    generated_at: dt.datetime | None = None,
) -> dict[str, Any]:
    live_trades = _live_trades(live_attribution)
    live_failure_counts = _live_failure_counts(live_attribution, live_trades)
    train_class_counts = _as_mapping(train_post_target_report.get("class_counts"))
    validation_class_counts = _as_mapping(validation_post_target_report.get("class_counts"))
    final_class_counts = _as_mapping(final_post_target_report.get("class_counts"))
    dead_flow_support = _as_mapping(dead_flow_support_report or {})
    dead_flow_gate = _as_mapping(dead_flow_support.get("support_gate"))
    dead_flow_split_counts = _as_mapping(dead_flow_support.get("split_counts"))

    def _dead_flow_support_value(key: str) -> int | None:
        if key in dead_flow_gate:
            value = dead_flow_gate.get(key)
            return None if value is None else int(value)
        if dead_flow_split_counts:
            split_value = _as_mapping(dead_flow_split_counts.get(key.split("_")[0] if "_" in key else key))
            class_counts = _as_mapping(split_value.get("class_counts"))
            if "replay_dead_flow_timeout" in class_counts:
                return int(class_counts.get("replay_dead_flow_timeout") or 0)
        return None

    candidate_bucket_checks = [
        _support_check(
            bucket="post_target_collapse_or_live_mfe_giveback",
            meaning="Reached +25% after entry, then collapsed before durable continuation or closed as loss.",
            train_positives=int(train_class_counts.get("post_target_collapse") or 0),
            validation_positives=int(validation_class_counts.get("post_target_collapse") or 0),
            final_positives=int(final_class_counts.get("post_target_collapse") or 0),
            live_positives=int(live_failure_counts.get("mfe_then_giveback") or 0),
        ),
        _support_check(
            bucket="dead_flow_timeout",
            meaning="No meaningful post-entry MFE, timeout exit, often with heavy pre-signal sell pressure.",
            train_positives=_dead_flow_support_value("train_positives"),
            validation_positives=_dead_flow_support_value("validation_positives"),
            final_positives=_dead_flow_support_value("final_positives"),
            live_positives=(
                _dead_flow_support_value("live_positives")
                if dead_flow_gate
                else int(live_failure_counts.get("dead_flow_timeout") or 0)
            ),
        ),
        _support_check(
            bucket="entry_slippage_failure",
            meaning="Signal path looked tradable but live fill lag/slippage made entry immediately fragile.",
            train_positives=None,
            validation_positives=None,
            final_positives=None,
            live_positives=int(live_failure_counts.get("entry_slippage_failure") or 0),
        ),
    ]
    if dead_flow_gate:
        required_dead_flow_counts = (
            candidate_bucket_checks[1].get("train_positives"),
            candidate_bucket_checks[1].get("validation_positives"),
            candidate_bucket_checks[1].get("final_positives"),
            candidate_bucket_checks[1].get("live_positives"),
        )
        has_required_dead_flow_counts = all(value is not None for value in required_dead_flow_counts)
        candidate_bucket_checks[1]["passes_min_support_gate"] = (
            bool(dead_flow_gate.get("passes_support_gate")) and has_required_dead_flow_counts
        )
        if bool(dead_flow_gate.get("passes_support_gate")) and not has_required_dead_flow_counts:
            candidate_bucket_checks[1]["falsification_reason"] = (
                "dead-flow support report is missing one or more required positive counts"
            )
        elif dead_flow_gate.get("reason"):
            candidate_bucket_checks[1]["falsification_reason"] = str(dead_flow_gate.get("reason"))

    post_target_check = candidate_bucket_checks[0]
    supported_bucket_checks = [
        row
        for row in candidate_bucket_checks
        if bool(row.get("passes_min_support_gate"))
    ]
    if supported_bucket_checks:
        best_supported_check = max(
            supported_bucket_checks,
            key=lambda row: int(row.get("live_positives") or 0),
        )
        go_no_go = {
            "status": "NO_GO_FOR_LIVE_RULE",
            "supported_bucket": best_supported_check["bucket"],
            "reason": (
                f"{best_supported_check['bucket']} passes the diagnostic support gate with "
                f"train={best_supported_check['train_positives']}, "
                f"validation={best_supported_check['validation_positives']}, "
                f"final={best_supported_check['final_positives']}, "
                f"live={best_supported_check['live_positives']}. "
                "This report is still read-only and is not live-switch evidence; a default-off replay candidate "
                "must be implemented and validated before any live config or model artifact change."
            ),
            "next_node": "Design a default-off replay-only candidate for the supported bucket; do not modify live config or model artifacts.",
        }
    else:
        go_no_go = {
            "status": "NO_GO_FOR_LIVE_RULE",
            "supported_bucket": None,
            "reason": (
                f"validation_positives is {post_target_check['validation_positives']}, below the >=3 support gate. "
                "No candidate bucket has >=3 positives in validation, final, and live with a replay-equivalent label. "
                "The best-supported post-target direction has "
                f"train={post_target_check['train_positives']}, validation={post_target_check['validation_positives']}, "
                f"final={post_target_check['final_positives']}, live={post_target_check['live_positives']}."
            ),
            "next_node": "Summarize current research and design a default-off replay-only feasibility probe; do not modify live config or model artifacts.",
        }

    return {
        "generated_at": (generated_at or dt.datetime.now(dt.timezone.utc).astimezone()).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "active_model": str(live_attribution.get("active_model") or DEFAULT_MODEL_DIR),
        "contract": {
            "read_only": True,
            "live_switch_evidence": False,
            "requires_replay_before_live_change": True,
            "safe_for_live_switch": False,
            "position_fraction": 0.10,
        },
        "bucket_definitions": BUCKET_DEFINITIONS,
        "input_paths": {
            "live_attribution": str(live_attribution.get("_source_path") or DEFAULT_LIVE_ATTRIBUTION),
            "post_target_train": str(train_post_target_report.get("_source_path") or DEFAULT_TRAIN_POST_TARGET),
            "post_target_validation": str(validation_post_target_report.get("_source_path") or DEFAULT_VALIDATION_POST_TARGET),
            "post_target_final": str(final_post_target_report.get("_source_path") or DEFAULT_FINAL_POST_TARGET),
        },
        "split_counts": {
            "train": _split_counts(train_post_target_report),
            "validation": _split_counts(validation_post_target_report),
            "final": _split_counts(final_post_target_report),
        },
        "live_counts": {
            "restart_anchor": live_attribution.get("restart_anchor"),
            "trade_count": int(live_attribution.get("trade_count") or len(live_trades)),
            "win_count": int(live_attribution.get("win_count") or 0),
            "loss_count": int(live_attribution.get("loss_count") or 0),
            "net_profit_bnb": live_attribution.get("net_profit_bnb"),
            "reason_counts": _as_mapping(live_attribution.get("reason_counts")),
            "failure_label_counts": _as_mapping(live_attribution.get("failure_label_counts")) or live_failure_counts,
            "entry_plus25_count": _entry_plus25_count(live_trades),
            "post_target_loss_count": int(live_failure_counts.get("mfe_then_giveback") or 0),
            "dead_flow_timeout_count": int(live_failure_counts.get("dead_flow_timeout") or 0),
            "entry_slippage_failure_count": int(live_failure_counts.get("entry_slippage_failure") or 0),
        },
        "candidate_bucket_checks": candidate_bucket_checks,
        "near_threshold_breakdown": _near_threshold_breakdown(live_trades),
        "go_no_go": go_no_go,
        "dead_flow_support": dead_flow_support or None,
        "live_symbols_by_bucket": {
            "mfe_then_giveback": _trade_symbols(live_trades, failure_label="mfe_then_giveback"),
            "dead_flow_timeout": _trade_symbols(live_trades, failure_label="dead_flow_timeout"),
            "entry_slippage_failure": _trade_symbols(live_trades, failure_label="entry_slippage_failure"),
            "profitable_exit": _trade_symbols(live_trades, failure_label="profitable_exit"),
        },
    }


def to_markdown_text(report: Mapping[str, Any]) -> str:
    live_counts = _as_mapping(report.get("live_counts"))
    bucket_checks = [row for row in report.get("candidate_bucket_checks") or [] if isinstance(row, Mapping)]
    rows = [
        "| Bucket | Train positives | Validation positives | Final positives | Live positives | Decision |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in bucket_checks:
        def fmt(value: Any) -> str:
            return "n/a" if value is None else str(value)

        rows.append(
            f"| `{row.get('bucket')}` | {fmt(row.get('train_positives'))} | {fmt(row.get('validation_positives'))} | {fmt(row.get('final_positives'))} | {fmt(row.get('live_positives'))} | {'PASS' if row.get('passes_min_support_gate') else 'NO-GO'} |"
        )

    symbols = _as_mapping(report.get("live_symbols_by_bucket"))
    generated_at = report.get("generated_at")
    active_model = report.get("active_model")
    lines = [
        "# Exit-State Attribution Diagnostic",
        "",
        f"Generated: `{generated_at}`",
        "",
        f"Contract: read-only diagnostic, not live-switch evidence. Active model remains `{active_model}` with 10% position sizing.",
        "",
        "## Live Since Restart",
        "",
        f"- Restart anchor: `{live_counts.get('restart_anchor')}`",
        f"- Closed trades: `{live_counts.get('trade_count')}`; wins: `{live_counts.get('win_count')}`; losses: `{live_counts.get('loss_count')}`",
        f"- Net profit: `{live_counts.get('net_profit_bnb')}` BNB",
        f"- Failure labels: `{json.dumps(live_counts.get('failure_label_counts'), ensure_ascii=False, sort_keys=True)}`",
        f"- Close reasons: `{json.dumps(live_counts.get('reason_counts'), ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Support Gate",
        "",
        *rows,
        "",
        "## Decision",
        "",
        f"`{report.get('go_no_go', {}).get('status')}`: {report.get('go_no_go', {}).get('reason')}",
        "",
        "The next aligned step is a default-off replay-only feasibility probe or more live label accumulation, not a live config/model switch.",
        "",
        "## Symbols",
        "",
        f"- Live `mfe_then_giveback`: `{', '.join(symbols.get('mfe_then_giveback') or [])}`",
        f"- Live `dead_flow_timeout`: `{', '.join(symbols.get('dead_flow_timeout') or [])}`",
        f"- Live `entry_slippage_failure`: `{', '.join(symbols.get('entry_slippage_failure') or [])}`",
        f"- Live `profitable_exit`: `{', '.join(symbols.get('profitable_exit') or [])}`",
    ]
    return "\n".join(lines) + "\n"

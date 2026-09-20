#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_MODEL_DIR = "data/models/20260519_v95_v84_selective_nearmiss_gate"
DEFAULT_OUTPUT = "data/replay_reports/action_policy_candidate_gate_replay_20260526_support_lcb.json"
DEFAULT_SOURCE_LCB_REPORT = "data/replay_reports/action_policy_reward_lcb_probe_20260526_support_lcb_entryflow_thr02.json"
DEFAULT_TRAIN_REJECTED_REPORTS = [
    "data/replay_reports/time_to_barrier_probe_20260523_1615_since_142149_flowparity.json",
    "data/replay_reports/time_to_barrier_probe_20260523_2250_since_221344_correct_abstention.json",
    "data/replay_reports/time_to_barrier_probe_20260524_negative_return_reject_option_since_1546.json",
    "data/replay_reports/time_to_barrier_probe_20260525_next_round_since_132541.json",
]
DEFAULT_TRAIN_ACCEPTED_REPORTS = [
    "data/replay_reports/post_target_exit_state_probe_20260526_support_complete_entryflow_train_enriched.json",
]
LIVE_INITIAL_EQUITY_BNB = 0.005079303120051795
LIVE_POSITION_CAP = 0.1
STRICT_MAX_OPEN_POSITIONS = 8
MAX_TRADE_COUNT_EXPANSION_RATIO = 0.25
MAX_TRADE_COUNT_EXPANSION_MIN_EXTRA = 1
MAX_TRADE_COUNT_REDUCTION_RATIO = 0.25
MAX_TRADE_COUNT_REDUCTION_MIN_MISSING = 1
PROTECTED_MODEL_ARTIFACT_NAMES = frozenset((
    "hybrid_manifest.json",
    "buy_model.cbm",
    "buy_threshold.json",
    "feature_schema.json",
    "entry_value_model.cbm",
    "sell_policy.zip",
    "bc.pt",
    "trade_log.jsonl",
))


def _strict_live_fraction(value):
    fraction = float(value)
    if not math.isfinite(fraction) or not math.isclose(fraction, LIVE_POSITION_CAP, rel_tol=0.0, abs_tol=1e-12):
        raise argparse.ArgumentTypeError(f"value must be exactly {LIVE_POSITION_CAP}")
    return fraction


def _strict_max_open_positions(value):
    positions = int(value)
    if positions != STRICT_MAX_OPEN_POSITIONS:
        raise argparse.ArgumentTypeError(f"value must be exactly {STRICT_MAX_OPEN_POSITIONS}")
    return positions


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run a support-complete action-policy candidate-gate replay grid")
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR, help="Directory containing trained model artifacts")
    parser.add_argument("--lifecycle-dir", default="data/training", help="Directory containing lifecycle files")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Replay grid JSON output path")
    parser.add_argument("--source-lcb-report", default=DEFAULT_SOURCE_LCB_REPORT)
    parser.add_argument("--cache-dir", default=".cache/model_replay", help="Directory for replay sample cache files")
    parser.add_argument("--position-fraction", type=_strict_live_fraction, default=LIVE_POSITION_CAP)
    parser.add_argument("--max-position-fraction", type=_strict_live_fraction, default=LIVE_POSITION_CAP)
    parser.add_argument("--max-open-positions", type=_strict_max_open_positions, default=STRICT_MAX_OPEN_POSITIONS)
    parser.add_argument("--force", action="store_true", help="Overwrite an existing replay report")
    parser.add_argument("--no-cache", dest="use_cache", action="store_false", help="Rebuild replay samples instead of using cache")
    parser.add_argument("--train-rejected-report", action="append", default=None)
    parser.add_argument("--train-accepted-report", action="append", default=None)
    parser.set_defaults(use_cache=True)
    return parser.parse_args(argv)


def candidate_grid():
    for score_floor in (0.2, 0.4, 0.6, 0.8):
        yield {"buy_path_state_meta_gate_min_score": float(score_floor)}


def _base_overrides(args):
    return {
        "initial_equity_bnb": LIVE_INITIAL_EQUITY_BNB,
        "position_fraction": float(args.position_fraction),
        "max_position_fraction": float(args.max_position_fraction),
        "fixed_stake_bnb": None,
        "skip_all_in_replay": True,
        "max_open_positions": int(args.max_open_positions),
    }


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # Historical probe inputs are optional after the latest-data cleanup.
        # Keep the replay conservative by supplying an empty report; the gate
        # will fail closed rather than preventing unrelated diagnostics from running.
        return {
            "_missing_report": str(path),
            "rows": [],
            "evaluation": {},
            "summary": {},
        }


def _paths(values):
    return [Path(value) for value in values]


def _finite_metric(row, key):
    if not isinstance(row, dict) or key not in row:
        return None
    try:
        value = float(row[key])
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _source_lcb_gate(report):
    validation_lcb = _finite_metric(report.get("validation", {}) or {}, "reward_lcb_pct")
    final_lcb = _finite_metric(report.get("final", {}) or {}, "reward_lcb_pct")
    support_passes = bool((report.get("support_gate") or {}).get("passes"))
    stability_passes = bool((report.get("stability_gate") or {}).get("passes"))
    decision = str(report.get("decision") or "")
    reasons = []
    if decision != "shadow_reward_positive_lcb_replay_required":
        reasons.append("source_lcb_decision_not_replay_required")
    if not support_passes:
        reasons.append("source_lcb_support_gate_failed")
    if not stability_passes:
        reasons.append("source_lcb_stability_gate_failed")
    if validation_lcb is None or validation_lcb <= 0.0:
        reasons.append("source_lcb_validation_non_positive")
    if final_lcb is None or final_lcb <= 0.0:
        reasons.append("source_lcb_final_non_positive")
    return {
        "passes": not reasons,
        "reasons": reasons,
        "decision": decision,
        "validation_lcb_pct": validation_lcb,
        "final_lcb_pct": final_lcb,
        "support_gate_passes": support_passes,
        "stability_gate_passes": stability_passes,
    }


def _evaluation(report):
    return dict((report or {}).get("evaluation", {}) or {})


def _valid_stress_metrics(evaluation):
    required = ("net_return_pct", "net_profit_bnb", "max_drawdown_pct")
    rows = []
    for row in evaluation.get("stress_replay", []) or []:
        metrics = {key: _finite_metric(row, key) for key in required}
        if all(value is not None for value in metrics.values()):
            rows.append(metrics)
    return rows


def _stress_worst(evaluation, key):
    values = [metrics[key] for metrics in _valid_stress_metrics(evaluation)]
    return min(values) if values else None


def _stress_replay_scenarios(evaluation):
    names = []
    seen = set()
    rows = list(evaluation.get("stress_replay", []) or [])
    if not rows:
        return [], False
    required = ("net_return_pct", "net_profit_bnb", "max_drawdown_pct")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return names, False
        name = str(row.get("name") or f"stress_{index}").strip()
        if not name or name in seen:
            return names, False
        seen.add(name)
        names.append(name)
        if any(_finite_metric(row, key) is None for key in required):
            return names, False
    return names, True


def _int_metric(evaluation, key):
    try:
        return int(evaluation.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _summary(evaluation):
    net_profit_bnb = _finite_metric(evaluation, "net_profit_bnb")
    total_trades = _finite_metric(evaluation, "total_trades")
    max_drawdown_pct = _finite_metric(evaluation, "max_drawdown_pct")
    win_rate = _finite_metric(evaluation, "win_rate")
    has_primary_metrics = (
        net_profit_bnb is not None
        and total_trades is not None
        and total_trades >= 0.0
        and max_drawdown_pct is not None
        and win_rate is not None
    )
    walk_forward_worst_net_return_pct = _finite_metric(evaluation, "walk_forward_worst_net_return_pct")
    walk_forward_worst_max_drawdown_pct = _finite_metric(evaluation, "walk_forward_worst_max_drawdown_pct")
    has_walk_forward_metrics = (
        walk_forward_worst_net_return_pct is not None
        and walk_forward_worst_max_drawdown_pct is not None
    )
    stress_replay_scenarios, has_complete_stress_replay = _stress_replay_scenarios(evaluation)
    has_stress_replay = bool(_valid_stress_metrics(evaluation) and has_complete_stress_replay)
    return {
        "has_primary_metrics": bool(has_primary_metrics),
        "net_profit_bnb": 0.0 if net_profit_bnb is None else float(net_profit_bnb),
        "total_trades": 0 if total_trades is None else int(total_trades),
        "max_drawdown_pct": 0.0 if max_drawdown_pct is None else float(max_drawdown_pct),
        "win_rate": 0.0 if win_rate is None else float(win_rate),
        "has_walk_forward_metrics": bool(has_walk_forward_metrics),
        "has_stress_replay": bool(has_stress_replay),
        "stress_replay_scenarios": stress_replay_scenarios,
        "walk_forward_worst_net_return_pct": walk_forward_worst_net_return_pct,
        "walk_forward_worst_max_drawdown_pct": walk_forward_worst_max_drawdown_pct,
        "stress_worst_net_return_pct": _stress_worst(evaluation, "net_return_pct"),
        "stress_worst_net_profit_bnb": _stress_worst(evaluation, "net_profit_bnb"),
        "stress_worst_max_drawdown_pct": _stress_worst(evaluation, "max_drawdown_pct"),
        "path_state_meta_gate_signal_count": _int_metric(evaluation, "path_state_meta_gate_signal_count"),
        "path_state_meta_gate_entry_count": _int_metric(evaluation, "path_state_meta_gate_entry_count"),
        "path_state_meta_gate_reject_count": _int_metric(evaluation, "path_state_meta_gate_reject_count"),
    }


def _acceptance_gate():
    return {
        "baseline": "current_v95_strict_live_sized_replay",
        "source_lcb_gate_required": True,
        "requires_net_profit_bnb_above_baseline": True,
        "requires_max_drawdown_pct_not_worse": True,
        "requires_total_trades_not_materially_lower": True,
        "max_trade_count_reduction_ratio": MAX_TRADE_COUNT_REDUCTION_RATIO,
        "max_trade_count_reduction_min_missing": MAX_TRADE_COUNT_REDUCTION_MIN_MISSING,
        "requires_total_trades_not_materially_higher": True,
        "max_trade_count_expansion_ratio": MAX_TRADE_COUNT_EXPANSION_RATIO,
        "max_trade_count_expansion_min_extra": MAX_TRADE_COUNT_EXPANSION_MIN_EXTRA,
        "requires_win_rate_not_lower": True,
        "requires_walk_forward_worst_net_return_pct_not_lower": True,
        "requires_walk_forward_worst_max_drawdown_pct_not_worse": True,
        "requires_stress_worst_net_return_pct_not_lower": True,
        "requires_stress_worst_net_profit_bnb_not_lower": True,
        "requires_stress_worst_max_drawdown_pct_not_worse": True,
        "requires_path_state_meta_gate_entries": True,
    }


def _gate_details(candidate_summary, baseline_summary, source_lcb_gate):
    has_primary_metrics = bool(candidate_summary["has_primary_metrics"] and baseline_summary["has_primary_metrics"])
    has_walk_forward_metrics = bool(
        candidate_summary["has_walk_forward_metrics"] and baseline_summary["has_walk_forward_metrics"]
    )
    has_stress_replay = bool(candidate_summary["has_stress_replay"] and baseline_summary["has_stress_replay"])
    same_stress_scenarios = (
        list(candidate_summary.get("stress_replay_scenarios") or [])
        == list(baseline_summary.get("stress_replay_scenarios") or [])
    )
    has_stress_replay = bool(has_stress_replay and same_stress_scenarios)
    baseline_trades = int(baseline_summary["total_trades"])
    max_extra_trades = max(
        MAX_TRADE_COUNT_EXPANSION_MIN_EXTRA,
        math.ceil(baseline_trades * MAX_TRADE_COUNT_EXPANSION_RATIO),
    )
    max_missing_trades = max(
        MAX_TRADE_COUNT_REDUCTION_MIN_MISSING,
        math.ceil(baseline_trades * MAX_TRADE_COUNT_REDUCTION_RATIO),
    )
    return {
        "source_lcb_gate": bool(source_lcb_gate.get("passes")),
        "has_primary_metrics": has_primary_metrics,
        "has_walk_forward_metrics": has_walk_forward_metrics,
        "has_stress_replay": has_stress_replay,
        "same_stress_replay_scenarios": same_stress_scenarios,
        "net_profit_bnb": (
            has_primary_metrics
            and candidate_summary["net_profit_bnb"] > baseline_summary["net_profit_bnb"]
        ),
        "max_drawdown_pct": (
            has_primary_metrics
            and candidate_summary["max_drawdown_pct"] >= baseline_summary["max_drawdown_pct"]
        ),
        "total_trades_not_materially_lower": (
            has_primary_metrics
            and candidate_summary["total_trades"] >= max(0, baseline_trades - max_missing_trades)
        ),
        "total_trades_not_materially_higher": (
            has_primary_metrics
            and candidate_summary["total_trades"] <= baseline_trades + max_extra_trades
        ),
        "win_rate": (
            has_primary_metrics
            and candidate_summary["win_rate"] >= baseline_summary["win_rate"]
        ),
        "walk_forward_worst_net_return_pct": (
            has_walk_forward_metrics
            and candidate_summary["walk_forward_worst_net_return_pct"]
            >= baseline_summary["walk_forward_worst_net_return_pct"]
        ),
        "walk_forward_worst_max_drawdown_pct": (
            has_walk_forward_metrics
            and candidate_summary["walk_forward_worst_max_drawdown_pct"]
            >= baseline_summary["walk_forward_worst_max_drawdown_pct"]
        ),
        "stress_worst_net_return_pct": (
            has_stress_replay
            and candidate_summary["stress_worst_net_return_pct"] >= baseline_summary["stress_worst_net_return_pct"]
        ),
        "stress_worst_net_profit_bnb": (
            has_stress_replay
            and candidate_summary["stress_worst_net_profit_bnb"] >= baseline_summary["stress_worst_net_profit_bnb"]
        ),
        "stress_worst_max_drawdown_pct": (
            has_stress_replay
            and candidate_summary["stress_worst_max_drawdown_pct"] >= baseline_summary["stress_worst_max_drawdown_pct"]
        ),
        "path_state_meta_gate_entry_count": candidate_summary["path_state_meta_gate_entry_count"] > 0,
    }


def _candidate_score(row):
    if not row["summary"].get("has_primary_metrics"):
        return (-math.inf, -math.inf, -math.inf, -row["candidate_index"])
    return (
        row["summary"]["net_profit_bnb"],
        row["summary"]["max_drawdown_pct"],
        row["summary"]["total_trades"],
        -row["candidate_index"],
    )


def _score_maps_summary(score_maps):
    maps = list(score_maps or [])
    scored_counts = [
        sum(1 for key in row if isinstance(key, int) or (isinstance(key, str) and key.isdigit()))
        if isinstance(row, dict)
        else 0
        for row in maps
    ]
    return {
        "episode_count": len(maps),
        "non_empty_episode_count": sum(1 for count in scored_counts if count > 0),
        "scored_sample_count": sum(scored_counts),
        "max_episode_score_count": max(scored_counts, default=0),
    }


def _compact_replay_config(config):
    compact = dict(config or {})
    if "path_state_scores_by_episode" in compact:
        compact["path_state_scores_by_episode_summary"] = _score_maps_summary(
            compact.pop("path_state_scores_by_episode")
        )
    return compact


def _report_metadata(report, args):
    report = dict(report or {})
    return {
        "generated_at": report.get("generated_at"),
        "split": report.get("split"),
        "selection_role": report.get("selection_role"),
        "git": report.get("git"),
        "model_checksums": report.get("model_checksums"),
        "replay_config": _compact_replay_config(report.get("replay_config")),
        "sample_count": report.get("sample_count"),
        "lifecycle_paths": list(report.get("lifecycle_paths") or []),
        "cache_dir": args.cache_dir,
        "use_cache": bool(args.use_cache),
    }


def _assert_safe_output_path(model_dir, output_path):
    output_path = Path(output_path)
    if output_path.name not in PROTECTED_MODEL_ARTIFACT_NAMES:
        return
    try:
        resolved_output_path = output_path.resolve(strict=False)
        resolved_model_dir = Path(model_dir).resolve(strict=False)
        resolved_output_path.relative_to(resolved_model_dir)
    except ValueError:
        return
    raise SystemExit(f"refusing to write replay report to protected model artifact: {output_path}")


def _assert_output_writable(model_dir, output_path, *, force=False):
    _assert_safe_output_path(model_dir, output_path)
    output_path = Path(output_path)
    if output_path.exists() and not force:
        raise SystemExit(f"refusing to overwrite existing replay report without --force: {output_path}")


def _run_replay(run_model_replay, args, overrides, *, split, eval_samples=None):
    replay_overrides = dict(overrides or {})
    if eval_samples is not None:
        replay_overrides["eval_samples"] = eval_samples
        replay_overrides["eval_samples_already_split_filtered"] = True
    return run_model_replay(
        model_dir=args.model_dir,
        lifecycle_dir=args.lifecycle_dir,
        output_path=None,
        cache_dir=args.cache_dir,
        split=split,
        max_open_positions=args.max_open_positions,
        include_trade_log=False,
        overrides=replay_overrides,
        use_cache=args.use_cache,
        write_report=False,
    )


def _load_common_context(args, base_overrides):
    from src.pipeline.candidate_ranker_probe import runtime_params_with_buy_threshold
    from src.pipeline.model_replay import (
        apply_model_schema_feature_flags,
        live_replay_config_from_manifest,
        load_manifest,
        load_model_artifacts,
        load_or_build_samples,
        resolve_replay_split,
    )
    from src.pipeline.train_hybrid import _build_eval_episodes

    manifest = load_manifest(args.model_dir)
    replay_config = live_replay_config_from_manifest(
        manifest,
        max_open_positions=args.max_open_positions,
        include_trade_log=False,
        overrides=dict(base_overrides),
    )
    replay_config = apply_model_schema_feature_flags(replay_config, args.model_dir)
    replay_split = resolve_replay_split(manifest, args.lifecycle_dir)
    artifacts = load_model_artifacts(args.model_dir)
    buy_artifact = artifacts.buy_artifact
    runtime_params = runtime_params_with_buy_threshold(replay_config, buy_artifact)

    split_sample_cache = {}

    def split_samples(split):
        if split in split_sample_cache:
            return split_sample_cache[split]
        if split == "validation":
            files = replay_split.validation_files
            excluded_tokens = replay_split.excluded_validation_tokens
        elif split == "final":
            files = replay_split.eval_files
            excluded_tokens = replay_split.excluded_final_tokens
        else:
            raise ValueError(f"unsupported split for action-policy scoring: {split}")
        samples = load_or_build_samples(
            replay_config,
            files,
            excluded_tokens,
            cache_dir=args.cache_dir,
            use_cache=args.use_cache,
        )
        split_sample_cache[split] = samples
        return samples

    def split_episodes(split):
        return _build_eval_episodes(split_samples(split))

    return {
        "buy_artifact": buy_artifact,
        "runtime_params": runtime_params,
        "split_samples": split_samples,
        "split_episodes": split_episodes,
    }


def _split_samples_for_replay(args, split, base_overrides, context):
    if context is None:
        context = {}
    if "loaded" not in context:
        context["loaded"] = _load_common_context(args, base_overrides)
    return context["loaded"]["split_samples"](split)


def _candidate_gate_score_maps_for_split(args, *, split, base_overrides, train_inputs, context=None):
    from src.pipeline.action_policy_replay_gate import fit_action_policy_candidate_gate_and_score_episodes

    if context is None:
        context = {}
    if "loaded" not in context:
        context["loaded"] = _load_common_context(args, base_overrides)
    loaded = context["loaded"]
    score_maps, metadata = fit_action_policy_candidate_gate_and_score_episodes(
        train_rejected_reports=train_inputs["train_rejected_reports"],
        train_accepted_reports=train_inputs["train_accepted_reports"],
        eval_episodes=loaded["split_episodes"](split),
        buy_artifact=loaded["buy_artifact"],
        runtime_params=loaded["runtime_params"],
        train_rejected_source_names=train_inputs.get("train_rejected_source_names"),
        train_accepted_source_names=train_inputs.get("train_accepted_source_names"),
        max_depth=train_inputs.get("max_depth", 3),
        min_samples_leaf=train_inputs.get("min_samples_leaf", 50),
        min_common_features=train_inputs.get("min_common_features", 2),
    )
    return score_maps, metadata


def main(argv=None):
    args = parse_args(argv)
    if not math.isclose(args.position_fraction, LIVE_POSITION_CAP, rel_tol=0.0, abs_tol=1e-12):
        raise SystemExit(f"position_fraction must be exactly {LIVE_POSITION_CAP}")
    if not math.isclose(args.max_position_fraction, LIVE_POSITION_CAP, rel_tol=0.0, abs_tol=1e-12):
        raise SystemExit(f"max_position_fraction must be exactly {LIVE_POSITION_CAP}")
    if args.max_open_positions != STRICT_MAX_OPEN_POSITIONS:
        raise SystemExit(f"max_open_positions must be exactly {STRICT_MAX_OPEN_POSITIONS}")
    _assert_output_writable(args.model_dir, args.output, force=bool(args.force))

    from src.pipeline.model_replay import run_model_replay

    base_overrides = _base_overrides(args)
    source_lcb_gate = _source_lcb_gate(_load_json(Path(args.source_lcb_report)))
    train_inputs = {
        "train_rejected_reports": [_load_json(path) for path in _paths(args.train_rejected_report or DEFAULT_TRAIN_REJECTED_REPORTS)],
        "train_accepted_reports": [_load_json(path) for path in _paths(args.train_accepted_report or DEFAULT_TRAIN_ACCEPTED_REPORTS)],
        "train_rejected_source_names": [Path(path).stem for path in (args.train_rejected_report or DEFAULT_TRAIN_REJECTED_REPORTS)],
        "train_accepted_source_names": [Path(path).stem for path in (args.train_accepted_report or DEFAULT_TRAIN_ACCEPTED_REPORTS)],
        "max_depth": 3,
        "min_samples_leaf": 50,
        "min_common_features": 2,
    }
    score_context = {}
    validation_samples = _split_samples_for_replay(args, "validation", base_overrides, score_context)
    validation_baseline_report = _run_replay(
        run_model_replay,
        args,
        base_overrides,
        split="validation",
        eval_samples=validation_samples,
    )
    validation_baseline_summary = _summary(_evaluation(validation_baseline_report))

    candidates = []
    validation_score_maps, validation_model_metadata = _candidate_gate_score_maps_for_split(
        args,
        split="validation",
        base_overrides=base_overrides,
        train_inputs=train_inputs,
        context=score_context,
    )
    for index, params in enumerate(candidate_grid()):
        overrides = dict(base_overrides)
        overrides.update(params)
        overrides["path_state_scores_by_episode"] = validation_score_maps
        report = _run_replay(
            run_model_replay,
            args,
            overrides,
            split="validation",
            eval_samples=validation_samples,
        )
        evaluation = _evaluation(report)
        summary = _summary(evaluation)
        gate_details = _gate_details(summary, validation_baseline_summary, source_lcb_gate)
        candidates.append({
            "candidate_index": int(index),
            "params": params,
            "summary": summary,
            "passes_acceptance_gate": all(gate_details.values()),
            "gate_details": gate_details,
            "evaluation": evaluation,
            "replay_metadata": _report_metadata(report, args),
        })

    best_validation_raw_candidate = max(candidates, key=_candidate_score)
    accepted = [candidate for candidate in candidates if candidate["passes_acceptance_gate"]]
    best_validation_accepted = max(accepted, key=_candidate_score, default=None)
    validation_selected = best_validation_accepted or best_validation_raw_candidate

    final_samples = _split_samples_for_replay(args, "final", base_overrides, score_context)
    final_baseline_report = _run_replay(
        run_model_replay,
        args,
        base_overrides,
        split="final",
        eval_samples=final_samples,
    )
    final_baseline_summary = _summary(_evaluation(final_baseline_report))
    final_score_maps, final_model_metadata = _candidate_gate_score_maps_for_split(
        args,
        split="final",
        base_overrides=base_overrides,
        train_inputs=train_inputs,
        context=score_context,
    )
    final_candidate_overrides = dict(base_overrides)
    final_candidate_overrides.update(validation_selected["params"])
    final_candidate_overrides["path_state_scores_by_episode"] = final_score_maps
    final_candidate_report = _run_replay(
        run_model_replay,
        args,
        final_candidate_overrides,
        split="final",
        eval_samples=final_samples,
    )
    final_candidate_evaluation = _evaluation(final_candidate_report)
    final_candidate_summary = _summary(final_candidate_evaluation)
    final_gate_details = _gate_details(final_candidate_summary, final_baseline_summary, source_lcb_gate)
    final_candidate = {
        "candidate_index": validation_selected["candidate_index"],
        "params": validation_selected["params"],
        "summary": final_candidate_summary,
        "passes_acceptance_gate": all(final_gate_details.values()),
        "gate_details": final_gate_details,
        "evaluation": final_candidate_evaluation,
        "replay_metadata": _report_metadata(final_candidate_report, args),
    }
    final_confirmation = {
        "baseline": {
            "summary": final_baseline_summary,
            "evaluation": _evaluation(final_baseline_report),
        },
        "candidate": final_candidate,
        "passes_acceptance_gate": bool(final_candidate["passes_acceptance_gate"]),
    }

    decision = (
        "accept"
        if best_validation_accepted is not None and final_confirmation["passes_acceptance_gate"]
        else "reject"
    )
    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model_dir": str(args.model_dir),
        "lifecycle_dir": str(args.lifecycle_dir),
        "strict_assumptions": base_overrides,
        "source_lcb_report": str(args.source_lcb_report),
        "source_lcb_gate": source_lcb_gate,
        "acceptance_gate": _acceptance_gate(),
        "action_policy_model": {
            "validation": validation_model_metadata,
            "final": final_model_metadata,
        },
        "baseline": {
            "split": "validation",
            "summary": validation_baseline_summary,
            "evaluation": _evaluation(validation_baseline_report),
            "replay_metadata": _report_metadata(validation_baseline_report, args),
        },
        "candidates": candidates,
        "best_validation_raw_candidate": best_validation_raw_candidate,
        "best_validation_candidate": validation_selected,
        "best_validation_accepted_candidate": best_validation_accepted,
        "selected_candidate": validation_selected,
        "best_candidate": validation_selected,
        "best_accepted_candidate": best_validation_accepted,
        "final_confirmation": final_confirmation,
        "replay_metadata": {
            "validation_baseline": _report_metadata(validation_baseline_report, args),
            "final_baseline": _report_metadata(final_baseline_report, args),
            "final_candidate": _report_metadata(final_candidate_report, args),
        },
        "decision": decision,
        "live_switch_evidence": False,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(
        "decision={decision} validation_baseline_net_profit_bnb={baseline:.12f} "
        "best_validation_net_profit_bnb={best:.12f} "
        "final_confirmation_passed={final_passed} candidates={count} output={output}".format(
            decision=report["decision"],
            baseline=validation_baseline_summary["net_profit_bnb"],
            best=validation_selected["summary"]["net_profit_bnb"],
            final_passed=final_confirmation["passes_acceptance_gate"],
            count=len(candidates),
            output=str(output_path),
        )
    )
    return report


if __name__ == "__main__":
    main()

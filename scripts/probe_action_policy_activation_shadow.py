#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_action_policy_candidate_gate_replay import (  # noqa: E402
    DEFAULT_TRAIN_ACCEPTED_REPORTS,
    DEFAULT_TRAIN_REJECTED_REPORTS,
)
from src.model.action_policy_router_runtime import ActionPolicyRouterRuntime  # noqa: E402
from src.pipeline import action_policy_live_shadow as shadow  # noqa: E402
from src.pipeline import reentry_probe  # noqa: E402


DEFAULT_SIGNAL_AUDIT = "data/signal_audit.jsonl"
DEFAULT_PAPER_TRADES = "data/paper_trades.jsonl"
DEFAULT_COLLECTOR_STATE = "data/training/collector_runtime_state.json"
DEFAULT_LIFECYCLE_DIR = "data/training"
DEFAULT_OUTPUT_JSON = "data/replay_reports/action_policy_activation_shadow.json"
DEFAULT_OUTPUT_MD = "data/replay_reports/action_policy_activation_shadow.md"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Score queued live shadow routes with activation/release path outcomes")
    parser.add_argument("--signal-audit", default=DEFAULT_SIGNAL_AUDIT)
    parser.add_argument("--paper-trades", default=DEFAULT_PAPER_TRADES)
    parser.add_argument("--collector-state", default=DEFAULT_COLLECTOR_STATE)
    parser.add_argument("--lifecycle-dir", default=DEFAULT_LIFECYCLE_DIR)
    parser.add_argument("--recent-lifecycle-files", type=int, default=24)
    parser.add_argument("--lifecycle-file", action="append", default=[])
    parser.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", default=DEFAULT_OUTPUT_MD)
    parser.add_argument("--since", default=None)
    parser.add_argument("--until", default=None)
    parser.add_argument("--active-model", default=None)
    parser.add_argument("--primary-min-prob", type=float, default=0.98)
    parser.add_argument("--activation-pct", type=float, default=35.0)
    parser.add_argument("--release-pct", type=float, default=75.0)
    parser.add_argument("--stop-loss-pct", type=float, default=-18.0)
    parser.add_argument("--hard-stop-pct", type=float, default=-25.0)
    parser.add_argument("--max-match-seconds", type=float, default=20.0)
    parser.add_argument("--max-sample-rows", type=int, default=100)
    parser.add_argument("--train-rejected-report", action="append", default=None)
    parser.add_argument("--train-accepted-report", action="append", default=None)
    parser.add_argument("--router-min-confidence", type=float, default=0.40)
    parser.add_argument("--router-max-depth", type=int, default=3)
    parser.add_argument("--router-min-samples-leaf", type=int, default=10)
    parser.add_argument("--router-min-common-features", type=int, default=2)
    parser.add_argument("--router-min-live-features", type=int, default=2)
    parser.add_argument("--router-min-prob", type=float, default=None)
    parser.add_argument("--router-max-pred-return", type=float, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.recent_lifecycle_files < 0:
        parser.error("--recent-lifecycle-files must be non-negative")
    if args.max_sample_rows < 0:
        parser.error("--max-sample-rows must be non-negative")
    if args.primary_min_prob <= 0.0 or args.primary_min_prob > 1.0:
        parser.error("--primary-min-prob must be in (0, 1]")
    if args.router_min_prob is not None and (
        not math.isfinite(args.router_min_prob) or args.router_min_prob <= 0.0 or args.router_min_prob > 1.0
    ):
        parser.error("--router-min-prob must be in (0, 1]")
    if args.router_max_pred_return is not None and not math.isfinite(args.router_max_pred_return):
        parser.error("--router-max-pred-return must be finite")
    if args.release_pct <= args.activation_pct:
        parser.error("--release-pct must be greater than --activation-pct")
    if args.stop_loss_pct >= 0.0 or args.hard_stop_pct >= 0.0:
        parser.error("--stop-loss-pct and --hard-stop-pct must be negative")
    return args


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _assert_output(path_text: str, *, force: bool) -> Path:
    path = Path(path_text)
    resolved = path if path.is_absolute() else PROJECT_ROOT / path
    resolved = resolved.resolve()
    allowed_roots = [
        (PROJECT_ROOT / "data" / "replay_reports").resolve(),
        (PROJECT_ROOT / "docs" / "research").resolve(),
    ]
    if not any(_is_relative_to(resolved, root) for root in allowed_roots):
        raise SystemExit(f"refusing output outside replay/research roots: {path_text}")
    if resolved.exists() and not force:
        raise SystemExit(f"refusing to overwrite existing output without --force: {resolved}")
    return resolved


def _paths(values):
    return [str(path) for path in values or []]


def _load_lifecycles(args):
    lifecycle_paths = list(args.lifecycle_file or [])
    lifecycle_paths.extend(
        str(path)
        for path in reentry_probe.latest_lifecycle_files(
            args.lifecycle_dir,
            limit=args.recent_lifecycle_files,
        )
    )
    lifecycle_paths = list(dict.fromkeys(lifecycle_paths))
    lifecycles = reentry_probe.load_lifecycles(
        collector_state_path=args.collector_state,
        lifecycle_paths=lifecycle_paths,
    )
    return lifecycles, lifecycle_paths


def main(argv=None):
    args = parse_args(argv)
    output_json = _assert_output(args.output_json, force=bool(args.force))
    output_md = _assert_output(args.output_md, force=bool(args.force))
    signal_rows = shadow.load_jsonl(args.signal_audit)
    filtered = shadow.filter_signal_decisions(signal_rows, since=args.since, until=args.until, decisions=("queued", "rejected"))
    runtime_params = shadow.runtime_params_from_signal_rows(
        filtered,
        primary_min_prob=args.primary_min_prob,
        router_min_prob=args.router_min_prob,
        router_max_pred_return=args.router_max_pred_return,
    )
    runtime = ActionPolicyRouterRuntime.from_report_paths(
        train_rejected_report_paths=_paths(args.train_rejected_report or DEFAULT_TRAIN_REJECTED_REPORTS),
        train_accepted_report_paths=_paths(args.train_accepted_report or DEFAULT_TRAIN_ACCEPTED_REPORTS),
        runtime_params=runtime_params,
        min_confidence=float(args.router_min_confidence),
        max_depth=int(args.router_max_depth),
        min_samples_leaf=int(args.router_min_samples_leaf),
        min_common_features=int(args.router_min_common_features),
        min_live_features=int(args.router_min_live_features),
    )
    lifecycles, lifecycle_paths = _load_lifecycles(args)
    report = shadow.build_activation_shadow_report(
        signal_rows=signal_rows,
        trade_rows=shadow.load_jsonl(args.paper_trades),
        lifecycles=lifecycles,
        runtime=runtime,
        since=args.since,
        until=args.until,
        active_model=args.active_model,
        primary_min_prob=float(args.primary_min_prob),
        max_match_seconds=float(args.max_match_seconds),
        max_sample_rows=int(args.max_sample_rows),
        activation_pct=float(args.activation_pct),
        release_pct=float(args.release_pct),
        stop_loss_pct=float(args.stop_loss_pct),
        hard_stop_pct=float(args.hard_stop_pct),
    )
    report["input_paths"] = {
        "signal_audit": args.signal_audit,
        "paper_trades": args.paper_trades,
        "collector_state": args.collector_state,
        "lifecycle_dir": args.lifecycle_dir,
        "lifecycle_files": lifecycle_paths,
        "train_rejected_reports": _paths(args.train_rejected_report or DEFAULT_TRAIN_REJECTED_REPORTS),
        "train_accepted_reports": _paths(args.train_accepted_report or DEFAULT_TRAIN_ACCEPTED_REPORTS),
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(shadow.to_json_text(report), encoding="utf-8")
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(shadow.activation_to_markdown_text(report), encoding="utf-8")
    print(f"wrote {output_json}")
    print(f"wrote {output_md}")
    print(
        "status={status} matched={matched} activation={activation} release={release} activated_stop={activated_stop}".format(
            status=report["go_no_go"]["status"],
            matched=report["summary"]["queued_shadow_used_matched_count"],
            activation=report["summary"]["activation_hit_count"],
            release=report["summary"]["release_hit_count"],
            activated_stop=report["summary"]["activated_then_stop_count"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

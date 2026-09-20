#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_SIGNAL_AUDIT = "data/signal_audit.jsonl"
DEFAULT_COLLECTOR_STATE = "data/training/collector_runtime_state.json"
DEFAULT_LIFECYCLE_DIR = "data/training"
DEFAULT_OUTPUT_JSON = "data/replay_reports/entry_protection_skip_outcomes.json"
DEFAULT_OUTPUT_MD = "data/replay_reports/entry_protection_skip_outcomes.md"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Build a read-only ENTRY_PRICE_PROTECTION_SKIP outcome report")
    parser.add_argument("--signal-audit", default=DEFAULT_SIGNAL_AUDIT)
    parser.add_argument("--collector-state", default=DEFAULT_COLLECTOR_STATE)
    parser.add_argument("--lifecycle-dir", default=DEFAULT_LIFECYCLE_DIR)
    parser.add_argument("--recent-lifecycle-files", type=int, default=24)
    parser.add_argument("--lifecycle-file", action="append", default=[])
    parser.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", default=DEFAULT_OUTPUT_MD)
    parser.add_argument("--since", default=None)
    parser.add_argument("--until", default=None)
    parser.add_argument("--active-model", default=None)
    parser.add_argument("--max-hold-seconds", type=float, default=560.0)
    parser.add_argument("--horizon-seconds", type=float, default=10800.0)
    parser.add_argument("--min-support", type=int, default=7)
    parser.add_argument("--max-sample", type=int, default=100, help="0 emits all scored skip events")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.recent_lifecycle_files < 0:
        parser.error("--recent-lifecycle-files must be non-negative")
    if args.max_hold_seconds <= 0.0:
        parser.error("--max-hold-seconds must be positive")
    if args.horizon_seconds < args.max_hold_seconds:
        parser.error("--horizon-seconds must be at least --max-hold-seconds")
    if args.min_support < 1:
        parser.error("--min-support must be at least 1")
    if args.max_sample < 0:
        parser.error("--max-sample must be non-negative")
    return args


def _normalized_relative_text(path_text: str) -> str:
    text = Path(path_text).as_posix()
    while text.startswith("./"):
        text = text[2:]
    return text


def _protected_exact_paths() -> set[str]:
    return {
        ".env",
        ".env.example",
        "docs/goals/live-model-optimization-goal.md",
    }


def _allowed_output_roots() -> list[Path]:
    return [
        PROJECT_ROOT / "data" / "replay_reports",
        PROJECT_ROOT / "docs" / "research",
    ]


def _validate_output_path(output_text: str) -> Path:
    normalized = _normalized_relative_text(output_text)
    if normalized in _protected_exact_paths() or normalized.startswith("docs/goals/"):
        raise ValueError(f"refusing output path: {output_text}")

    output_path = Path(output_text)
    logical_output = output_path if output_path.is_absolute() else PROJECT_ROOT / output_path
    resolved_output = logical_output.resolve()
    for allowed_root in [root.resolve() for root in _allowed_output_roots()]:
        try:
            resolved_output.relative_to(allowed_root)
            return resolved_output
        except ValueError:
            continue
    allowed_text = ", ".join(str(root) for root in _allowed_output_roots())
    raise ValueError(f"refusing output path outside allowed roots ({allowed_text}): {output_text}")


def _read_path_snapshot(path: str | Path) -> tuple[dict, bytes]:
    resolved = Path(path)
    stat_before = resolved.stat() if resolved.exists() and resolved.is_file() else None
    data = resolved.read_bytes() if stat_before is not None else b""
    exists_after_read = resolved.exists() and resolved.is_file()
    stat_after = resolved.stat() if exists_after_read else None
    fingerprint = {
        "path": str(resolved),
        "exists": stat_before is not None,
        "exists_after_read": exists_after_read,
        "size_bytes": len(data),
        "path_size_bytes_before_read": int(stat_before.st_size) if stat_before is not None else 0,
        "path_size_bytes_after_read": int(stat_after.st_size) if stat_after is not None else 0,
        "mtime_ns": int(stat_before.st_mtime_ns) if stat_before is not None else None,
        "mtime_ns_after_read": int(stat_after.st_mtime_ns) if stat_after is not None else None,
        "changed_during_read": (
            stat_before is not None
            and (
                stat_after is None
                or int(stat_before.st_size) != int(stat_after.st_size)
                or int(stat_before.st_mtime_ns) != int(stat_after.st_mtime_ns)
            )
        ),
        "snapshot_read_mode": "single_read_bytes",
        "sha256": None,
    }
    if stat_before is not None:
        fingerprint["sha256"] = hashlib.sha256(data).hexdigest()
    return fingerprint, data


def _iter_jsonl_bytes(data: bytes):
    for line in data.decode("utf-8").splitlines():
        text = line.strip()
        if text:
            yield json.loads(text)


def _input_fingerprint_policy() -> dict:
    return {
        "mutable_live_inputs": True,
        "fingerprints_are_run_snapshot": True,
        "snapshot_read_mode": "single_read_bytes",
        "current_paths_may_change_after_run": True,
    }


def _dedupe_paths(paths) -> list:
    unique = []
    seen = set()
    for path in paths:
        resolved = Path(path).resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def main(argv=None) -> int:
    args = parse_args(argv)
    probe = importlib.import_module("src.pipeline.entry_protection_skip_probe")
    reentry_probe = probe.reentry_probe
    try:
        output_json = _validate_output_path(args.output_json)
        output_md = _validate_output_path(args.output_md)
        if output_json.exists() and not args.force:
            raise ValueError(f"refusing to overwrite existing output without --force: {output_json}")
        if output_md.exists() and not args.force:
            raise ValueError(f"refusing to overwrite existing output without --force: {output_md}")

        lifecycle_paths = _dedupe_paths(
            list(args.lifecycle_file or [])
            + list(reentry_probe.latest_lifecycle_files(args.lifecycle_dir, limit=args.recent_lifecycle_files))
        )
        signal_fingerprint, signal_bytes = _read_path_snapshot(args.signal_audit)
        signal_rows = list(_iter_jsonl_bytes(signal_bytes)) if signal_fingerprint["exists"] else []

        lifecycle_maps = []
        collector_fingerprint, collector_bytes = _read_path_snapshot(args.collector_state)
        if collector_fingerprint["exists"] and collector_bytes.strip():
            lifecycle_maps.append(
                reentry_probe.extract_lifecycles_from_runtime_state(json.loads(collector_bytes.decode("utf-8")))
            )
        lifecycle_fingerprints = []
        for lifecycle_path in lifecycle_paths:
            lifecycle_fingerprint, lifecycle_bytes = _read_path_snapshot(lifecycle_path)
            lifecycle_fingerprints.append(lifecycle_fingerprint)
            if lifecycle_fingerprint["exists"]:
                lifecycle_maps.append(reentry_probe.extract_lifecycles_from_rows(_iter_jsonl_bytes(lifecycle_bytes)))
        lifecycles = reentry_probe.merge_lifecycle_maps(*lifecycle_maps)

        report = probe.build_skip_outcome_report(
            signal_rows=signal_rows,
            lifecycles=lifecycles,
            since=args.since,
            until=args.until,
            active_model=args.active_model,
            max_hold_seconds=args.max_hold_seconds,
            horizon_seconds=args.horizon_seconds,
            min_support=args.min_support,
            max_sample=args.max_sample,
        )
        report["input_paths"] = {
            "signal_audit": args.signal_audit,
            "collector_state": args.collector_state,
            "lifecycle_dir": args.lifecycle_dir,
            "recent_lifecycle_files": args.recent_lifecycle_files,
            "lifecycle_files": [str(path) for path in lifecycle_paths],
        }
        report["input_status"] = reentry_probe.build_input_status(
            paper_trades="data/paper_trades.jsonl",
            signal_audit=args.signal_audit,
            collector_state=args.collector_state,
            lifecycle_dir=args.lifecycle_dir,
            lifecycle_paths=lifecycle_paths,
        )
        report["input_fingerprints"] = {
            "signal_audit": signal_fingerprint,
            "collector_state": collector_fingerprint,
            "lifecycle_files": lifecycle_fingerprints,
        }
        report["input_fingerprint_policy"] = _input_fingerprint_policy()

        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(probe.to_json_text(report), encoding="utf-8")
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(probe.to_markdown_text(report), encoding="utf-8")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"wrote {output_json}")
    print(f"wrote {output_md}")
    print(f"decision={report.get('decision', {}).get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

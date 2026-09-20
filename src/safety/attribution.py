"""Per-filter attribution: which filters separated shadow winners from losers."""

from __future__ import annotations

from collections import defaultdict


def build_attribution(store) -> dict:
    """Join shadow closes with the latest prior safety report for the same token."""
    closes = store.rows("shadow_close", limit=10_000)
    reports = store.rows("safety_report", limit=10_000)
    by_token = defaultdict(list)
    for report in reports:
        by_token[report["entity"]].append(report)
    stats = defaultdict(lambda: {"pass": 0, "fail": 0, "error": 0, "pass_pnl": 0.0, "fail_pnl": 0.0})
    joined = 0
    for close in closes:
        payload = close.get("payload") or {}
        token = close.get("entity")
        prior = [r for r in by_token.get(token, []) if r["observed_at"] <= close["observed_at"]]
        if not prior:
            continue
        report = max(prior, key=lambda r: (r["observed_at"], r["seq"]))
        pnl = float(payload.get("pnl_quote", 0.0))
        joined += 1
        for result in (report["payload"] or {}).get("results", []):
            entry = stats[result.get("filter_id", "unknown")]
            status = result.get("status", "error")
            if status == "pass":
                entry["pass"] += 1
                entry["pass_pnl"] += pnl
            elif status == "fail":
                entry["fail"] += 1
                entry["fail_pnl"] += pnl
            else:
                entry["error"] += 1
    attribution = {}
    for filter_id, entry in sorted(stats.items()):
        pass_n, fail_n = entry["pass"], entry["fail"]
        attribution[filter_id] = {
            **entry,
            "pass_avg_pnl": entry["pass_pnl"] / pass_n if pass_n else None,
            "fail_avg_pnl": entry["fail_pnl"] / fail_n if fail_n else None,
        }
    return {"joined_closes": joined, "filters": attribution}

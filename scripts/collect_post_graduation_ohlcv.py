#!/usr/bin/env python3
"""Collect timestamped GeckoTerminal DEX history after Four.meme graduation."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Mapping

import requests
from requests.adapters import HTTPAdapter
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import Config  # noqa: E402
from src.pipeline.cross_boundary import build_cross_boundary_lifecycle  # noqa: E402
from src.pipeline.runner_reserve_profile import load_lifecycles_from_paths  # noqa: E402

logger = logging.getLogger("collect_post_graduation_ohlcv")
BASE_URL = "https://api.geckoterminal.com/api/v2"
_thread_local = threading.local()
_rate_limit_lock = threading.Lock()
_next_request_at = 0.0


def _token(value: Any) -> str:
    return str(value or "").strip().lower()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if parsed == parsed and abs(parsed) != float("inf") else float(default)


def _timestamp(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = None
    if parsed is not None and parsed == parsed and abs(parsed) != float("inf"):
        return parsed
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed_dt = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
    return parsed_dt.timestamp()


def _session(proxy: str) -> requests.Session:
    session = requests.Session()
    pool_size = 8
    session.mount("https://", HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size))
    session.headers.update({"Accept": "application/json", "User-Agent": "meme-post-graduation-research/1.0"})
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def _wait_for_request(interval_seconds: float) -> None:
    """Throttle requests across workers so the public endpoint is not bursty."""
    global _next_request_at
    interval = max(0.0, float(interval_seconds))
    if interval <= 0.0:
        return
    with _rate_limit_lock:
        now = time.monotonic()
        wait = max(0.0, _next_request_at - now)
        if wait > 0.0:
            time.sleep(wait)
            now = time.monotonic()
        _next_request_at = now + interval


def _get_json(
    session: requests.Session,
    url: str,
    *,
    attempts: int = 4,
    request_interval_seconds: float = 2.2,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            _wait_for_request(request_interval_seconds)
            response = session.get(url, timeout=30)
            if response.status_code == 429:
                # The public endpoint often returns ``Retry-After: 0`` even
                # while the shared proxy is still in its minute bucket.  A
                # short retry loop simply burns the remaining quota, so honor
                # the header only when it is meaningful and otherwise wait a
                # full rate-limit window.
                retry_after = _number(response.headers.get("Retry-After"), 60.0)
                last_error = RuntimeError(
                    f"HTTP 429 rate limited (retry_after={retry_after:g}s)"
                )
                if retry_after <= 0.0:
                    retry_after = 60.0
                time.sleep(min(120.0, max(5.0, retry_after)))
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("GeckoTerminal response is not an object")
            # Keep the public endpoint below its burst limit when collecting
            # many graduated tokens through one proxy.
            time.sleep(0.35)
            return payload
        except Exception as exc:  # pragma: no cover - provider failures vary
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(5.0, 0.5 * (attempt + 1)))
    raise RuntimeError(f"GET {url} failed: {last_error or 'unknown error'}")


def _pool_side(token: str, pool: Mapping[str, Any]) -> str | None:
    relationships = pool.get("relationships") or {}
    base_id = _token(((relationships.get("base_token") or {}).get("data") or {}).get("id"))
    quote_id = _token(((relationships.get("quote_token") or {}).get("data") or {}).get("id"))
    token_id = f"bsc_{token}"
    if base_id == token_id or base_id == token:
        return "base"
    if quote_id == token_id or quote_id == token:
        return "quote"
    return None


def _pool_score(pool: Mapping[str, Any]) -> tuple[float, float, float]:
    attrs = pool.get("attributes") or {}
    volume = attrs.get("volume_usd") or {}
    return (
        _number(attrs.get("reserve_in_usd")),
        _number(volume.get("h24")),
        _number(attrs.get("fdv_usd")),
    )


def _collect_one(
    lifecycle: Mapping[str, Any],
    *,
    proxy: str,
    limit: int,
    min_liquidity_usd: float,
    as_of_timestamp: float | None,
    request_interval_seconds: float,
) -> dict[str, Any]:
    token = _token(lifecycle.get("token_address") or lifecycle.get("token"))
    graduation = _timestamp(lifecycle.get("graduate_time"))
    if not token or graduation is None or not lifecycle.get("graduated"):
        return {"token": token, "status": "skipped", "reason": "not_graduated_or_missing_time"}
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = _session(proxy)
        _thread_local.session = session
    pools_url = f"{BASE_URL}/networks/bsc/tokens/{token}/pools?page=1"
    try:
        pools_payload = _get_json(
            session,
            pools_url,
            request_interval_seconds=request_interval_seconds,
        )
        pools = [row for row in pools_payload.get("data") or [] if isinstance(row, Mapping)]
        candidates = []
        for pool in pools:
            side = _pool_side(token, pool)
            if side is None:
                continue
            attrs = pool.get("attributes") or {}
            liquidity = _number(attrs.get("reserve_in_usd"))
            if liquidity >= min_liquidity_usd:
                candidates.append((pool, side))
        if not candidates:
            candidates = [(pool, _pool_side(token, pool)) for pool in pools if _pool_side(token, pool)]
        if not candidates:
            return {"token": token, "status": "skipped", "reason": "no_token_pool", "pools_url": pools_url}
        pool, side = max(candidates, key=lambda item: _pool_score(item[0]))
        attrs = pool.get("attributes") or {}
        pool_address = _token(attrs.get("address"))
        ohlcv_url = (
            f"{BASE_URL}/networks/bsc/pools/{pool_address}/ohlcv/hour"
            f"?aggregate=1&limit={int(limit)}&currency=usd&token={side}"
        )
        ohlcv_payload = _get_json(
            session,
            ohlcv_url,
            request_interval_seconds=request_interval_seconds,
        )
        bars = ((ohlcv_payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        normalized_bars = []
        for bar in bars:
            if not isinstance(bar, (list, tuple)) or len(bar) < 6:
                continue
            values = [_timestamp(bar[0]), *[_number(value, float("nan")) for value in bar[1:]]]
            if values[0] is None or any(value != value for value in values[1:]):
                continue
            if values[1] <= 0 or values[2] <= 0 or values[3] <= 0 or values[4] <= 0 or values[5] < 0:
                continue
            normalized_bars.append(values)
        normalized_bars = sorted({tuple(row) for row in normalized_bars}, key=lambda row: row[0])
        if not normalized_bars:
            return {"token": token, "status": "skipped", "reason": "no_ohlcv", "pool_address": pool_address}
        # Keep only bars that are causally post-graduation in the research
        # artifact.  The endpoint returns the pool's full history, which may
        # include hours before this token graduated.
        bridge_bars = [bar for bar in normalized_bars if bar[0] >= graduation]
        if not bridge_bars:
            return {
                "token": token,
                "status": "skipped",
                "reason": "no_post_graduation_ohlcv",
                "pool_address": pool_address,
                "bars_count": len(normalized_bars),
            }
        dex_row = {
            "token": token,
            "pool_address": pool_address,
            "quote_side": side,
            "bars": bridge_bars,
            "source_url": ohlcv_url,
            "pool": dict(pool),
        }
        cross_boundary = build_cross_boundary_lifecycle(
            lifecycle,
            dex_row,
            as_of_timestamp=as_of_timestamp,
        )
        if cross_boundary is None:
            return {
                "token": token,
                "status": "skipped",
                "reason": "no_curve_dex_overlap",
                "pool_address": pool_address,
                "bars_count": len(normalized_bars),
            }
        return {
            "token": token,
            "symbol": lifecycle.get("symbol"),
            "status": "ok",
            "graduation_time": graduation,
            "pool_address": pool_address,
            "token_side": side,
            "pool": dict(pool),
            "bars": [list(row) for row in bridge_bars],
            "all_pool_bars_count": len(normalized_bars),
            "cross_boundary": cross_boundary.get("cross_boundary"),
            "pools_url": pools_url,
            "ohlcv_url": ohlcv_url,
            "lifecycle": cross_boundary,
        }
    except Exception as exc:
        return {"token": token, "status": "error", "reason": str(exc), "pools_url": pools_url}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-dir", default="data/training/latest_bsc_20260913_31d")
    parser.add_argument("--lifecycle-file", action="append", default=None)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--limit-bars", type=int, default=1000)
    parser.add_argument("--max-tokens", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--request-interval-seconds",
        type=float,
        default=3.2,
        help="Minimum delay between public API requests across all workers",
    )
    parser.add_argument(
        "--resume-from",
        default=None,
        help="Reuse successful rows from a previous output and retry only missing/error tokens",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=10,
        help="Write a resumable checkpoint after this many completed tokens (0 disables)",
    )
    parser.add_argument("--min-liquidity-usd", type=float, default=5_000.0)
    parser.add_argument("--as-of", default=None)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    load_dotenv(PROJECT_ROOT / ".env")
    Config.validate_rpc_config()
    paths = [Path(value) for value in args.lifecycle_file] if args.lifecycle_file else sorted(
        Path(args.lifecycle_dir).glob("lifecycle_*.jsonl")
    )
    lifecycles = load_lifecycles_from_paths(paths)
    graduated = [row for row in lifecycles if row.get("graduated")]
    graduated.sort(key=lambda row: (_timestamp(row.get("graduate_time")) or 0.0, _token(row.get("token_address"))))
    if args.max_tokens > 0:
        graduated = graduated[-int(args.max_tokens):]
    as_of = _timestamp(args.as_of) if args.as_of is not None else datetime.now(timezone.utc).timestamp()
    proxy = Config.get_local_proxy_url()
    rows_by_token: dict[str, dict[str, Any]] = {}
    resume_path = Path(args.resume_from) if args.resume_from else None
    if resume_path and resume_path.exists():
        try:
            previous = json.loads(resume_path.read_text(encoding="utf-8"))
            for row in previous.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                token = _token(row.get("token"))
                # Only successful paths are reusable.  Errors and rate-limit
                # failures must be retried so a partial run cannot bias coverage.
                if token and row.get("status") == "ok":
                    rows_by_token[token] = row
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Unable to load resume file %s: %s", resume_path, exc)
    pending = [
        lifecycle
        for lifecycle in graduated
        if _token(lifecycle.get("token_address") or lifecycle.get("token"))
        not in rows_by_token
    ]
    rows = list(rows_by_token.values())
    logger.info(
        "Post-graduation collection: graduated=%d reusable_ok=%d pending=%d interval=%.2fs workers=%d",
        len(graduated),
        len(rows_by_token),
        len(pending),
        max(0.0, float(args.request_interval_seconds)),
        max(1, int(args.workers)),
    )
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        futures = [
            executor.submit(
                _collect_one,
                lifecycle,
                proxy=proxy,
                limit=max(1, int(args.limit_bars)),
                min_liquidity_usd=max(0.0, float(args.min_liquidity_usd)),
                as_of_timestamp=as_of,
                request_interval_seconds=max(0.0, float(args.request_interval_seconds)),
            )
            for lifecycle in pending
        ]
        for completed, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            rows_by_token[_token(result.get("token"))] = result
            rows = list(rows_by_token.values())
            if args.checkpoint_every > 0 and completed % int(args.checkpoint_every) == 0:
                checkpoint = Path(str(args.output) + ".partial")
                checkpoint.parent.mkdir(parents=True, exist_ok=True)
                checkpoint.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                            "as_of_timestamp": as_of,
                            "source": "GeckoTerminal public API",
                            "graduated_input_count": len(graduated),
                            "rows": sorted(rows, key=lambda row: _token(row.get("token"))),
                            "partial": True,
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
    # Re-normalize reused rows as well as freshly fetched rows.  This repairs
    # resumable files written by older collector versions that retained
    # pre-graduation pool bars or dropped the curve anchor at the boundary.
    lifecycle_by_token = {
        _token(lifecycle.get("token_address") or lifecycle.get("token")): lifecycle
        for lifecycle in graduated
    }
    for row in rows_by_token.values():
        if row.get("status") != "ok":
            continue
        token = _token(row.get("token"))
        base_lifecycle = lifecycle_by_token.get(token)
        graduation = _timestamp(row.get("graduation_time"))
        bars = []
        for value in row.get("bars") or []:
            if not isinstance(value, (list, tuple)) or len(value) < 6:
                continue
            timestamp = _timestamp(value[0])
            if timestamp is not None and (graduation is None or timestamp >= graduation) and timestamp <= as_of:
                bars.append(list(value))
        row["bars"] = bars
        if base_lifecycle and bars:
            rebuilt = build_cross_boundary_lifecycle(
                base_lifecycle,
                {
                    "bars": bars,
                    "pool_address": row.get("pool_address"),
                    "quote_side": row.get("token_side", row.get("quote_side")),
                    "source_url": row.get("ohlcv_url"),
                },
                as_of_timestamp=as_of,
            )
            if rebuilt is not None:
                row["lifecycle"] = rebuilt
                row["cross_boundary"] = rebuilt.get("cross_boundary")
                row["graduation_time"] = _timestamp(rebuilt.get("graduate_time"))
    rows = list(rows_by_token.values())
    rows.sort(key=lambda row: _token(row.get("token")))
    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of_timestamp": as_of,
        "as_of_utc": datetime.fromtimestamp(as_of, timezone.utc).isoformat(),
        "source": "GeckoTerminal public API",
        "lifecycle_dir": str(args.lifecycle_dir),
        "lifecycle_files": [str(path) for path in paths],
        "proxy_enabled": bool(proxy),
        "graduated_input_count": len(graduated),
        "status_counts": status_counts,
        "rows": rows,
        "live_switch_evidence": False,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema_version": 1,
        "generated_at": payload["generated_at"],
        "source_output": str(output),
        "source_urls": sorted({url for row in rows for url in (row.get("pools_url"), row.get("ohlcv_url")) if url}),
        "as_of_timestamp": as_of,
        "proxy_enabled": bool(proxy),
        "graduated_input_count": len(graduated),
        "status_counts": status_counts,
        "ok_rows": [
            {
                "token": row.get("token"),
                "symbol": row.get("symbol"),
                "pool_address": row.get("pool_address"),
                "bars_count": len(row.get("bars") or []),
                "graduation_time": row.get("graduation_time"),
                "dex_last_timestamp": (row.get("cross_boundary") or {}).get("dex_last_timestamp"),
            }
            for row in rows
            if row.get("status") == "ok"
        ],
        "live_switch_evidence": False,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "report": str(report_path), "status_counts": status_counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

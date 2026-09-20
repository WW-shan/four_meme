#!/usr/bin/env python3
"""Backfill Four.meme logs with synchronous JSON-RPC requests through the proxy.

Some public Web3 providers reject archive getLogs through aiohttp even though
the same endpoint serves JSON-RPC over the local proxy. This collector keeps
the raw request path explicit and writes an isolated lifecycle directory.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from dotenv import load_dotenv
from web3 import Web3

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import Config  # noqa: E402
from src.data.collector import DataCollector  # noqa: E402
from src.data.fourmeme_log_decoder import (  # noqa: E402
    TOPIC_CREATE,  # noqa: F401 - re-exported: tests import it from this script
    TOPIC_STOP,  # noqa: F401 - re-exported: tests import it from this script
    TRADE_TOPICS,
    decode_fourmeme_log,
)


logger = logging.getLogger("backfill_fourmeme_month_raw")
CONTRACT = "0x5c952063c7fc8610ffdb798152d69f0b9550762b"
TOPICS = dict(TRADE_TOPICS)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, default=30.0)
    parser.add_argument("--output-dir", default="data/training/bsc_month_20260911")
    parser.add_argument("--rpc-endpoint", default="https://bsc.rpc.blxrbdn.com")
    parser.add_argument("--chunk-blocks", type=int, default=5000)
    parser.add_argument("--max-workers", type=int, default=16)
    parser.add_argument("--fetch-workers", type=int, default=8)
    parser.add_argument("--from-block", type=int, default=None)
    parser.add_argument("--to-block", type=int, default=None)
    parser.add_argument("--log-file", default=None)
    return parser.parse_args(argv)


def _rpc(
    session: requests.Session,
    endpoint: str,
    method: str,
    params: list[Any],
    *,
    retries: int = 5,
    timeout: float = 60.0,
) -> Any:
    payload = {"jsonrpc": "2.0", "id": int(time.time_ns() % 2_000_000_000), "method": method, "params": params}
    last = None
    for attempt in range(retries):
        try:
            response = session.post(endpoint, json=payload, timeout=float(timeout))
            response.raise_for_status()
            body = response.json()
            if "error" in body:
                raise RuntimeError(body["error"])
            return body.get("result")
        except Exception as exc:
            last = exc
            time.sleep(min(2.0, 0.25 * (attempt + 1)))
    raise RuntimeError(f"RPC {method} failed after {retries} attempts: {last}")


def _block_number(session: requests.Session, endpoint: str) -> int:
    return int(_rpc(session, endpoint, "eth_blockNumber", []), 16)


def _block_timestamp(session: requests.Session, endpoint: str, block: int) -> int:
    result = _rpc(session, endpoint, "eth_getBlockByNumber", [hex(int(block)), False])
    return int(result["timestamp"], 16)


def _estimate_start(session: requests.Session, endpoint: str, head: int, days: float) -> tuple[int, int]:
    head_timestamp = _block_timestamp(session, endpoint, head)
    recent_block = max(0, head - 1000)
    recent_timestamp = _block_timestamp(session, endpoint, recent_block)
    rate = max(0.1, (head - recent_block) / max(1, head_timestamp - recent_timestamp))
    return max(0, head - int(rate * float(days) * 86400.0)), head_timestamp


def _fetch_logs_range(
    session: requests.Session,
    endpoint: str,
    start: int,
    end: int,
    *,
    min_chunk_blocks: int = 250,
) -> list[dict]:
    """Fetch a range and bisect provider-limited/high-volume failures."""
    try:
        result = _rpc(
            session,
            endpoint,
            "eth_getLogs",
            [{"fromBlock": hex(start), "toBlock": hex(end), "address": Web3.to_checksum_address(CONTRACT)}],
            retries=2,
            timeout=30.0,
        )
        return list(result or [])
    except Exception as exc:
        if end <= start or (end - start + 1) <= int(min_chunk_blocks):
            raise
        middle = (start + end) // 2
        logger.warning(
            "splitting getLogs range %s-%s after %s: %s",
            start,
            end,
            type(exc).__name__,
            exc,
        )
        return _fetch_logs_range(
            session,
            endpoint,
            start,
            middle,
            min_chunk_blocks=min_chunk_blocks,
        ) + _fetch_logs_range(
            session,
            endpoint,
            middle + 1,
            end,
            min_chunk_blocks=min_chunk_blocks,
        )


def _decode_trade(log: dict) -> dict | None:
    """Return quantities only for ABI-verified purchase and sale events."""
    decoded = decode_fourmeme_log(log)
    if decoded is not None and decoded[0] in ("TokenPurchase", "TokenSale"):
        return decoded[1]
    return None


def _decode_log(log: dict) -> tuple[str, dict] | None:
    """Keep the backfill tuple interface without guessing event layouts."""
    return decode_fourmeme_log(log)


def _event_data(name: str, args: dict, log: dict, timestamp: int) -> dict:
    return {
        "event_name": name,
        "args": args,
        "transactionHash": log.get("transactionHash"),
        "logIndex": int(str(log.get("logIndex") or "0"), 16),
        "blockNumber": int(str(log.get("blockNumber") or "0"), 16),
        "timestamp": int(timestamp),
    }


def run(args) -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    Config.validate_rpc_config()
    endpoint = str(args.rpc_endpoint)
    proxy = Config.get_local_proxy_url()
    session = requests.Session()
    pool_size = max(16, int(args.max_workers) * 2)
    session.mount("http://", HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size))
    session.mount("https://", HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size))
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    head = _block_number(session, endpoint)
    if args.from_block is None:
        from_block, head_timestamp = _estimate_start(session, endpoint, head, args.days)
    else:
        from_block, head_timestamp = int(args.from_block), _block_timestamp(session, endpoint, head)
    to_block = min(head, int(args.to_block) if args.to_block is not None else head)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    collector = DataCollector(output_dir=str(output_dir), incremental_run_id=f"month_raw_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    chunk_blocks = max(1, int(args.chunk_blocks))
    total_chunks = (to_block - from_block + chunk_blocks) // chunk_blocks
    block_cache: dict[int, int] = {}
    logger.info("range=%s-%s chunks=%s endpoint=%s proxy=%s", from_block, to_block, total_chunks, endpoint, bool(proxy))

    ranges = [
        (start, min(to_block, start + chunk_blocks - 1))
        for start in range(from_block, to_block + 1, chunk_blocks)
    ]
    fetch_workers = max(1, int(args.fetch_workers))

    def fetch_range(item):
        start, end = item
        local_session = requests.Session()
        if proxy:
            local_session.proxies.update({"http": proxy, "https": proxy})
        pool_size = max(4, int(args.max_workers))
        local_session.mount("http://", HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size))
        local_session.mount("https://", HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size))
        try:
            logs = _fetch_logs_range(local_session, endpoint, start, end)
            return start, end, logs
        finally:
            local_session.close()

    for batch_start in range(0, len(ranges), fetch_workers):
        batch_ranges = ranges[batch_start:batch_start + fetch_workers]
        with ThreadPoolExecutor(max_workers=fetch_workers) as fetch_pool:
            fetched = list(fetch_pool.map(fetch_range, batch_ranges))
        for index, (start, end, logs) in enumerate(fetched, start=batch_start + 1):
            # Fetch ranges concurrently, but process events in chain order.
            logs = sorted(
                logs,
                key=lambda row: (
                    int(str(row.get("blockNumber") or "0"), 16),
                    int(str(row.get("logIndex") or "0"), 16),
                ),
            )
            missing_blocks = sorted(
                {
                    int(str(log.get("blockNumber") or "0"), 16)
                    for log in logs
                    if log.get("blockNumber") and not log.get("blockTimestamp")
                }
            )
            with ThreadPoolExecutor(max_workers=max(1, int(args.max_workers))) as pool:
                futures = {pool.submit(_block_timestamp, session, endpoint, block): block for block in missing_blocks}
                for future, block in [(future, block) for future, block in futures.items()]:
                    try:
                        block_cache[block] = future.result()
                    except Exception:
                        block_cache[block] = head_timestamp
            for log in logs:
                decoded_event = _decode_log(log)
                if decoded_event is None:
                    continue
                name, args_payload = decoded_event
                block = int(str(log.get("blockNumber") or "0"), 16)
                event_data = _event_data(name, args_payload, log, block_cache.get(block, head_timestamp))
                if log.get("blockTimestamp"):
                    event_data["timestamp"] = int(str(log["blockTimestamp"]), 16)
                if name == "TokenCreate":
                    collector.on_token_create(event_data)
                elif name == "TokenPurchase":
                    collector.on_token_purchase(event_data)
                elif name == "TokenSale":
                    collector.on_token_sale(event_data)
                elif name == "TradeStop":
                    collector.on_trade_stop(event_data)
            if index == 1 or index % 10 == 0 or index == total_chunks:
                logger.info("progress=%s/%s blocks=%s-%s logs=%s stats=%s", index, total_chunks, start, end, len(logs), collector.get_stats())
    collector.flush_all_to_incremental()
    collector.save_token_metadata_index()
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "from_block": from_block,
        "to_block": to_block,
        "head_block": head,
        "days_requested": float(args.days),
        "chunk_blocks": chunk_blocks,
        "rpc_endpoint": endpoint,
        "proxy_enabled": bool(proxy),
        "output_dir": str(output_dir),
        "collector_stats": collector.get_stats(),
        "live_switch_evidence": False,
    }
    (output_dir / "backfill_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("complete stats=%s", collector.get_stats())
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", handlers=[logging.StreamHandler(), *([logging.FileHandler(args.log_file, encoding="utf-8")] if args.log_file else [])])
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Backfill Four.meme lifecycle events for a recent BSC calendar window.

This is an isolated historical collector. It uses the configured HTTP RPC pool
and proxy, writes to a separate output directory, and never touches the live
collector checkpoint or bot state.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import Config  # noqa: E402
from src.core.listener import FourMemeListener  # noqa: E402
from src.data.collector import DataCollector  # noqa: E402


logger = logging.getLogger("backfill_fourmeme_month")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, default=30.0)
    parser.add_argument("--output-dir", default="data/training/bsc_month_20260911")
    parser.add_argument("--chunk-blocks", type=int, default=10_000)
    parser.add_argument("--flush-every-chunks", type=int, default=10)
    parser.add_argument("--from-block", type=int, default=None)
    parser.add_argument("--to-block", type=int, default=None)
    parser.add_argument("--log-file", default=None)
    return parser.parse_args(argv)


async def _block_timestamp(w3, block_number: int) -> int:
    block = await w3.eth.get_block(int(block_number))
    return int(block["timestamp"])


async def _find_start_block(w3, head: int, days: float) -> tuple[int, int, int]:
    head_timestamp = await _block_timestamp(w3, head)
    target_timestamp = head_timestamp - int(float(days) * 86400.0)
    # Public RPC nodes may not expose old block headers even when getLogs can
    # serve the range. Fall back to a recent block-rate estimate in that case.
    try:
        recent_block = max(0, int(head) - 1_000)
        recent_timestamp = await _block_timestamp(w3, recent_block)
        rate = max(0.1, (int(head) - recent_block) / max(1, head_timestamp - recent_timestamp))
    except Exception:
        rate = 2.2
    low, high = 0, int(head)
    try:
        while high - low > 2_000:
            middle = (low + high) // 2
            middle_timestamp = await _block_timestamp(w3, middle)
            if middle_timestamp < target_timestamp:
                low = middle
            else:
                high = middle
    except Exception:
        estimated_start = max(0, int(head) - int(rate * float(days) * 86400.0))
        logger.warning("historical block headers unavailable; using block-rate estimate start=%s", estimated_start)
        return estimated_start, int(head), int(head_timestamp)
    return int(high), int(head), int(head_timestamp)


async def run(args) -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    Config.validate_rpc_config()
    from web3 import AsyncWeb3
    from web3.middleware import ExtraDataToPOAMiddleware
    from web3.providers.rpc import AsyncHTTPProvider

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    collector = DataCollector(
        output_dir=str(output_dir),
        incremental_run_id=f"month_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
    )
    endpoints = Config.get_log_http_pool()
    provider = AsyncHTTPProvider(endpoints[0], request_kwargs=Config.get_http_request_kwargs())
    w3 = AsyncWeb3(provider)
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    current_head = int(await w3.eth.block_number)
    if args.from_block is None:
        from_block, estimated_to_block, head_timestamp = await _find_start_block(w3, current_head, args.days)
    else:
        from_block = max(0, int(args.from_block))
        estimated_to_block = current_head
        head_timestamp = await _block_timestamp(w3, current_head)
    to_block = min(current_head, int(args.to_block) if args.to_block is not None else estimated_to_block)
    if to_block < from_block:
        raise ValueError(f"invalid block range {from_block}-{to_block}")

    contract_config = Config.get_contract_config()
    listener = FourMemeListener(
        w3,
        {
            "contract_address": contract_config["contract_address"],
            "contract_abi": contract_config["contract_abi"],
            "log_http_endpoints": endpoints,
            "max_lag_skip_blocks": 0,
            "lag_skip_keep_recent_blocks": 0,
            "log_provider_cooldown_seconds": contract_config["log_provider_cooldown_seconds"],
            "listener_poll_interval_seconds": 0.1,
            "event_batch_size": 500,
            "timestamp_prefetch_concurrency": 32,
        },
    )

    async def handle_event(event_name: str, event_data: dict):
        if event_name == "TokenCreate":
            collector.on_token_create(event_data)
        elif "Purchase" in event_name:
            collector.on_token_purchase(event_data)
        elif "Sale" in event_name:
            collector.on_token_sale(event_data)
        elif event_name == "TradeStop":
            collector.on_trade_stop(event_data)

    for event_name in (
        "TokenCreate",
        "TokenPurchase",
        "TokenSale",
        "TokenPurchaseV1",
        "TokenSaleV1",
        "TokenPurchase2",
        "TokenSale2",
        "TradeStop",
    ):
        listener.register_handler(event_name, handle_event)

    chunk_blocks = max(1, int(args.chunk_blocks))
    flush_every = max(1, int(args.flush_every_chunks))
    total_chunks = (to_block - from_block + chunk_blocks) // chunk_blocks
    logger.info(
        "backfill range=%s-%s blocks=%s days=%s head_timestamp=%s output=%s proxy=%s",
        from_block,
        to_block,
        to_block - from_block + 1,
        args.days,
        head_timestamp,
        output_dir,
        bool(Config.get_local_proxy_url()),
    )
    try:
        chunk_index = 0
        for chunk_start in range(from_block, to_block + 1, chunk_blocks):
            chunk_index += 1
            chunk_end = min(to_block, chunk_start + chunk_blocks - 1)
            ok = await listener._process_block_range(chunk_start, chunk_end)
            if not ok:
                raise RuntimeError(f"failed historical range {chunk_start}-{chunk_end}")
            if chunk_index % flush_every == 0:
                collector.flush_eligible_tokens(
                    current_time=head_timestamp,
                    min_age_seconds=900,
                    inactivity_seconds=900,
                )
            if chunk_index == 1 or chunk_index % 10 == 0 or chunk_index == total_chunks:
                stats = collector.get_stats()
                logger.info(
                    "progress chunk=%s/%s blocks=%s-%s tracked=%s memory=%s flushed=%s",
                    chunk_index,
                    total_chunks,
                    chunk_start,
                    chunk_end,
                    stats["tokens_tracked"],
                    stats["tokens_in_memory"],
                    stats["tokens_flushed"],
                )
        collector.flush_all_to_incremental()
        collector.save_token_metadata_index()
        summary = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "from_block": from_block,
            "to_block": to_block,
            "head_block": current_head,
            "head_timestamp": head_timestamp,
            "days_requested": float(args.days),
            "chunk_blocks": chunk_blocks,
            "output_dir": str(output_dir),
            "rpc_endpoint": endpoints[0],
            "proxy_enabled": bool(Config.get_local_proxy_url()),
            "collector_stats": collector.get_stats(),
            "listener_stats": listener.get_stats(),
            "live_switch_evidence": False,
        }
        (output_dir / "backfill_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("backfill complete stats=%s", summary["collector_stats"])
        return 0
    finally:
        await listener.close_log_providers()
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            result = disconnect()
            if asyncio.iscoroutine(result):
                await result


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(), *([logging.FileHandler(args.log_file, encoding="utf-8")] if args.log_file else [])],
    )
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

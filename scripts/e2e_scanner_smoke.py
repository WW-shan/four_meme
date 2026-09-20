#!/usr/bin/env python3
"""Real-chain end-to-end smoke: logs -> decode -> radar -> snapshot -> safety -> shadow -> gate -> API."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests  # noqa: E402

from config.scanner_config import ScannerConfig  # noqa: E402
from src.data.fourmeme_log_decoder import decode_fourmeme_log  # noqa: E402
from src.radar.api import make_scanner_server  # noqa: E402
from src.radar.pipeline import ScannerPipeline  # noqa: E402
from src.radar.store import ScannerStore  # noqa: E402
from src.safety.fetchers import SnapshotFetcher  # noqa: E402
from src.shadow.executor import shadow_buy  # noqa: E402
from src.shadow.report import ShadowGateConfig, build_gate_report, records_from_store  # noqa: E402
from src.shadow.tracker import ShadowTracker  # noqa: E402

RPC_FALLBACKS = [
    "https://bsc-dataseed.binance.org",
    "https://bsc-dataseed1.defibit.io",
    "https://bsc-dataseed1.ninicoin.io",
]
TEST_TOKEN = os.getenv("E2E_TOKEN", "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82")  # CAKE
CONTROL = {
    "liquidity_usd": 25_000, "dev_holding_pct": 0.5, "buy_tax_pct": 1.0, "sell_tax_pct": 1.0,
    "trades_recent": 42, "mcap_usd": 45_000, "mint_authority": None, "freeze_authority": None,
    "owner_renounced": True, "blacklist": False, "pausable": False, "top1_pct": 8.0,
    "top10_pct": 22.0, "non_lp_max_pct": 5.0, "lp_burned": True, "lp_locked_pct": None,
    "honeypot_sim": True, "bundle_current_held_pct": 5.0, "bundle_wallet_count": 2,
    "bundle_total_pct": 8.0, "deployer_rug_rate": 0.1, "early_sniper_count": 3,
}


def rpc(method, params):
    from config.config import Config

    configured = list(getattr(Config, "FAST_RPC_ENDPOINTS", []) or [])
    configured += list(getattr(Config, "LOG_HTTP_ENDPOINTS", []) or [])
    last_error = None
    for url in [os.getenv("BSC_E2E_RPC")] + configured + RPC_FALLBACKS:
        if not url:
            continue
        try:
            response = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20)
            payload = response.json()
            if "result" in payload:
                return payload["result"]
            last_error = payload.get("error")
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"rpc failed: {last_error}")


def fetch_real_events(limit_blocks: int = 3000):
    from config.config import Config

    contract = Config.get_contract_config()["contract_address"]
    latest = int(rpc("eth_blockNumber", []), 16)
    logs = None
    for span in (limit_blocks, 1000, 500, 200, 50):
        try:
            logs = rpc("eth_getLogs", [{"address": contract,
                                        "fromBlock": hex(max(0, latest - span)), "toBlock": "latest"}])
            break
        except RuntimeError:
            continue
    if logs is None:
        raise RuntimeError("eth_getLogs failed for every block range")
    events = []
    for log in logs or []:
        topics = log.get("topics") or []
        if len(topics) != 1:
            continue
        decoded = decode_fourmeme_log({"topics": topics, "data": log.get("data", "0x")})
        if decoded is None:
            continue
        name, args = decoded
        if name not in {"TokenCreate", "LiquidityAdded"}:
            continue
        events.append({"event_name": name, "data": {
            "args": args, "timestamp": 0, "received_at": time.time(),
            "blockNumber": int(log.get("blockNumber", "0x0"), 16),
            "logIndex": int(log.get("logIndex", "0x0"), 16),
            "transactionHash": log.get("transactionHash", ""),
        }})
    return latest, len(logs or []), events


async def main() -> int:
    report = {"ok": False, "steps": {}}
    with tempfile.TemporaryDirectory() as tmp:
        store = ScannerStore(Path(tmp) / "scanner.sqlite")
        pipeline = ScannerPipeline(store, ScannerConfig(mode="safe"),
                                   fetcher=SnapshotFetcher(session=requests.Session(),
                                                           goplus_key=os.getenv("GOPLUS_API_KEY") or None,
                                                           gmgn_key=os.getenv("GMGN_API_KEY") or None))
        latest, raw_count, events = fetch_real_events()
        for event in events:
            await pipeline.handle_event(event["event_name"], event["data"])
        report["steps"]["chain"] = {"latest_block": latest, "raw_logs": raw_count, "decoded_events": len(events),
                                    "stored_launches": len(store.rows("launch", limit=1000)),
                                    "stored_graduations": len(store.rows("graduation", limit=1000))}

        snapshot = pipeline.snapshot(TEST_TOKEN, override={"trades_recent": 10})
        report["steps"]["snapshot"] = {"sources": snapshot.get("sources"), "liquidity_usd": snapshot.get("liquidity_usd"),
                                       "mcap_usd": snapshot.get("mcap_usd")}
        report["steps"]["real_audit"] = pipeline.audit(TEST_TOKEN).to_dict()["verdict"]
        report["steps"]["control_audit"] = pipeline.audit(TEST_TOKEN, override=CONTROL).to_dict()["verdict"]

        decision = pipeline.decide(TEST_TOKEN, report=pipeline.audit(TEST_TOKEN, override=CONTROL),
                                   funding_confirmed=True, mcap_usd=45_000)
        report["steps"]["decision"] = decision.to_dict()

        price = float(snapshot.get("price_usd") or 1.0)
        now0 = time.time() - 120
        tracker = ShadowTracker(store=store)
        tracker.open(shadow_buy(TEST_TOKEN, 100.0, price, slippage_pct=2.0, fee_pct=1.0, at=now0),
                     baseline_liquidity=snapshot.get("liquidity_usd"))
        tracker.update(TEST_TOKEN, price * 1.6, now=now0 + 30)
        tracker.update(TEST_TOKEN, price * 0.5, now=now0 + 45)
        records = records_from_store(store)
        gate = build_gate_report(records, ShadowGateConfig(min_trades=1))
        report["steps"]["shadow"] = {"records": records, "gate": gate.to_dict()}

        server = make_scanner_server(store, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urllib.request.urlopen(base + "/", timeout=10) as response:
                html_ok = b"READ-ONLY" in response.read()
            with urllib.request.urlopen(base + "/api/v1/scanner/summary", timeout=10) as response:
                summary = json.load(response)
            report["steps"]["api"] = {"dashboard": html_ok, "summary": summary}
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        report["ok"] = bool(events) and html_ok and len(records) == 1
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

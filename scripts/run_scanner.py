#!/usr/bin/env python3
"""Scanner CLI: audit a snapshot, simulate a shadow round trip, or decode a radar event."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.scanner_config import ScannerConfig  # noqa: E402
from src.radar.api import make_scanner_server  # noqa: E402
from src.radar.events import launch_from_event  # noqa: E402
from src.radar.store import ScannerStore  # noqa: E402
from src.safety.attribution import build_attribution  # noqa: E402
from src.safety.fetchers import SnapshotFetcher  # noqa: E402
from src.safety.snapshot import build_snapshot  # noqa: E402
from src.walletflow.gmgn import GmgnOpenApiClient  # noqa: E402
from src.safety.orchestrator import build_report  # noqa: E402
from src.shadow.executor import shadow_buy, shadow_sell  # noqa: E402
from src.shadow.report import ShadowGateConfig, build_gate_report, records_from_store  # noqa: E402


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _live_fetcher() -> SnapshotFetcher:
    import requests

    return SnapshotFetcher(
        session=requests.Session(),
        goplus_key=os.getenv("GOPLUS_API_KEY") or None,
        gmgn_key=os.getenv("GMGN_API_KEY") or None,
    )


def _gmgn_session():
    import requests

    return requests.Session()


def command_audit(args) -> int:
    config = ScannerConfig.load(args.config)
    snapshot = _load(args.snapshot)
    token = str(snapshot.get("token") or args.token)
    report = build_report(token, snapshot, config.thresholds, mode=args.mode or config.mode)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.verdict == "pass" else 2


def command_snapshot(args) -> int:
    fetcher = _live_fetcher()
    fetched = fetcher.fetch_all(args.token)
    snapshot = build_snapshot(args.token, fetched)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
    return 0 if any(getattr(result, "ok", False) for result in fetched.values()) else 2


def command_audit_live(args) -> int:
    fetcher = _live_fetcher()
    fetched = fetcher.fetch_all(args.token)
    snapshot = build_snapshot(args.token, fetched)
    config = ScannerConfig.load(args.config)
    report = build_report(args.token, snapshot, config.thresholds, mode=args.mode or config.mode)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.verdict == "pass" else 2


def command_wallet_ingest(args) -> int:
    client = GmgnOpenApiClient(os.getenv("GMGN_API_KEY", ""), session=_gmgn_session())
    events = client.wallet_trades(args.address, limit=args.limit, chain=args.chain)
    store = ScannerStore(args.db)
    now = __import__("time").time()
    for event in events:
        store.append("wallet_event", f"{args.chain}:{event.wallet}", event.__dict__,
                     event.chain_time or now, event.received_at or now)
    print(json.dumps({"wallet": args.address, "events": len(events), "db": args.db}, ensure_ascii=False))
    return 0 if events else 2


def command_solana_event(args) -> int:
    from src.radar.solana import launch_from_solana_event

    launch = launch_from_solana_event(_load(args.event))
    if launch is None:
        print(json.dumps({"ok": False, "error": "invalid_solana_event"}, ensure_ascii=False))
        return 2
    if args.db:
        ScannerStore(args.db).append("launch", f"sol:{launch.mint}", launch.to_dict(),
                                     launch.chain_time or launch.received_at, launch.received_at)
    print(json.dumps({"ok": True, "launch": launch.to_dict()}, ensure_ascii=False, indent=2))
    return 0


def command_shadow(args) -> int:
    scenario = _load(args.scenario)
    buy = shadow_buy(
        scenario.get("token", "0xToken"),
        float(scenario["quote_amount"]),
        float(scenario["buy_price"]),
        slippage_pct=float(scenario.get("slippage_pct", 2.0)),
        fee_pct=float(scenario.get("fee_pct", 1.0)),
        at=float(scenario.get("buy_at", 0.0)),
    )
    sell = shadow_sell(
        buy.token,
        buy.token_amount * float(scenario.get("sell_fraction", 1.0)),
        float(scenario["sell_price"]),
        slippage_pct=float(scenario.get("slippage_pct", 2.0)),
        fee_pct=float(scenario.get("fee_pct", 1.0)),
        at=float(scenario.get("sell_at", 1.0)),
    )
    pnl = sell.quote_amount - buy.quote_amount
    print(json.dumps({
        "buy": buy.__dict__,
        "sell": sell.__dict__,
        "pnl_quote": pnl,
        "pnl_pct": pnl / buy.quote_amount,
    }, ensure_ascii=False, indent=2, default=str))
    return 0


def command_radar(args) -> int:
    event = _load(args.event)
    launch = launch_from_event(event.get("event_name", ""), event.get("data", {}))
    if launch is None:
        print(json.dumps({"ok": False, "error": "unsupported_event"}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "launch": launch.to_dict()}, ensure_ascii=False, indent=2))
    return 0


def command_gate(args) -> int:
    if getattr(args, "db", None):
        records = records_from_store(ScannerStore(args.db))
    else:
        records = _load(args.records)
    if isinstance(records, dict):
        records = records.get("records", [])
    report = build_gate_report(records, ShadowGateConfig())
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.verdict == "pass" else 2


def command_report(args) -> int:
    store = ScannerStore(args.db)
    records = records_from_store(store)
    gate = build_gate_report(records, ShadowGateConfig())
    print(json.dumps({"gate": gate.to_dict(), "attribution": build_attribution(store)},
                     ensure_ascii=False, indent=2))
    return 0 if gate.verdict == "pass" else 2


def command_live_check(args) -> int:
    from src.decision.live_gate import LiveGateConfig, evaluate_live_gate

    store = ScannerStore(args.db)
    gate = build_gate_report(records_from_store(store), ShadowGateConfig())
    result = evaluate_live_gate(
        LiveGateConfig(enabled=args.enabled, operator_confirmed=args.confirmed), gate)
    print(json.dumps({"shadow_gate": gate.to_dict(), "live_gate": {
        "allowed": result.allowed, "reason_codes": list(result.reason_codes)}}, ensure_ascii=False, indent=2))
    return 0 if result.allowed else 2


def command_serve(args) -> int:
    store = ScannerStore(args.db)
    server = make_scanner_server(store, args.host, args.port)
    print(f"Scanner API: http://{args.host}:{server.server_port} | read-only | trading=off", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit", help="Run safety filters over a snapshot JSON")
    audit.add_argument("--snapshot", required=True)
    audit.add_argument("--token", default="unknown")
    audit.add_argument("--mode", choices=("safe", "learning"))
    audit.add_argument("--config", help="Optional scanner config JSON")
    audit.set_defaults(func=command_audit)
    shadow = sub.add_parser("shadow", help="Simulate a shadow buy/sell round trip")
    shadow.add_argument("--scenario", required=True)
    shadow.set_defaults(func=command_shadow)
    snapshot = sub.add_parser("snapshot", help="Fetch a live token snapshot from configured providers")
    snapshot.add_argument("--token", required=True)
    snapshot.set_defaults(func=command_snapshot)
    audit_live = sub.add_parser("audit-live", help="Fetch a live snapshot and run safety filters")
    audit_live.add_argument("--token", required=True)
    audit_live.add_argument("--mode", choices=("safe", "learning"))
    audit_live.add_argument("--config", help="Optional scanner config JSON")
    audit_live.set_defaults(func=command_audit_live)
    wallet = sub.add_parser("wallet-ingest", help="Ingest GMGN smart-money trades into the scanner DB")
    wallet.add_argument("--db", required=True)
    wallet.add_argument("--address", required=True)
    wallet.add_argument("--chain", default="bsc")
    wallet.add_argument("--limit", type=int, default=100)
    wallet.set_defaults(func=command_wallet_ingest)
    solana = sub.add_parser("solana-event", help="Normalize (and optionally store) a decoded Solana launch event")
    solana.add_argument("--event", required=True)
    solana.add_argument("--db")
    solana.set_defaults(func=command_solana_event)
    radar = sub.add_parser("radar", help="Decode a listener event JSON into a launch record")
    radar.add_argument("--event", required=True)
    radar.set_defaults(func=command_radar)
    gate = sub.add_parser("gate", help="Evaluate the shadow P&L gate over closed trades")
    gate.add_argument("--records", help="Closed trades JSON file")
    gate.add_argument("--db", help="Scanner SQLite database with shadow_close records")
    gate.set_defaults(func=command_gate)
    report = sub.add_parser("report", help="Compute shadow gate + per-filter attribution from a scanner DB")
    report.add_argument("--db", required=True)
    report.set_defaults(func=command_report)
    live_check = sub.add_parser("live-check", help="Evaluate the live gate against stored shadow records")
    live_check.add_argument("--db", required=True)
    live_check.add_argument("--enabled", action="store_true")
    live_check.add_argument("--confirmed", action="store_true")
    live_check.set_defaults(func=command_live_check)
    serve = sub.add_parser("serve", help="Serve the read-only scanner API")
    serve.add_argument("--db", default="data/scanner/evidence.sqlite")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8790)
    serve.set_defaults(func=command_serve)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

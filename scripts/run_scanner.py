#!/usr/bin/env python3
"""Scanner CLI: audit a snapshot, simulate a shadow round trip, or decode a radar event."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.scanner_config import ScannerConfig  # noqa: E402
from src.radar.api import make_scanner_server  # noqa: E402
from src.radar.events import launch_from_event  # noqa: E402
from src.radar.store import ScannerStore  # noqa: E402
from src.safety.orchestrator import build_report  # noqa: E402
from src.shadow.executor import shadow_buy, shadow_sell  # noqa: E402
from src.shadow.report import ShadowGateConfig, build_gate_report  # noqa: E402


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def command_audit(args) -> int:
    config = ScannerConfig.load(args.config)
    snapshot = _load(args.snapshot)
    token = str(snapshot.get("token") or args.token)
    report = build_report(token, snapshot, config.thresholds, mode=args.mode or config.mode)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.verdict == "pass" else 2


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
    records = _load(args.records)
    if isinstance(records, dict):
        records = records.get("records", [])
    report = build_gate_report(records, ShadowGateConfig())
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.verdict == "pass" else 2


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
    radar = sub.add_parser("radar", help="Decode a listener event JSON into a launch record")
    radar.add_argument("--event", required=True)
    radar.set_defaults(func=command_radar)
    gate = sub.add_parser("gate", help="Evaluate the shadow P&L gate over closed trades JSON")
    gate.add_argument("--records", required=True)
    gate.set_defaults(func=command_gate)
    serve = sub.add_parser("serve", help="Serve the read-only scanner API")
    serve.add_argument("--db", default="data/scanner/evidence.sqlite")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8790)
    serve.set_defaults(func=command_serve)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

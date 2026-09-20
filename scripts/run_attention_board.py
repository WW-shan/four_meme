#!/usr/bin/env python3
"""Independent multi-chain attention dashboard and bounded collector."""
import argparse
from pathlib import Path
import signal
import sys
import threading

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.attention_config import AttentionConfig
from src.attention.collector import Collector
from src.attention.demo import seed_demo
from src.attention.server import make_server
from src.attention.store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve", "collect", "demo"))
    parser.add_argument("--db", help="SQLite path; demo and live must be separate")
    parser.add_argument("--sources", help="Source/query JSON; see config/attention_sources.example.json")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--collect", action="store_true", help="Run bounded polling alongside the server")
    parser.add_argument("--once", action="store_true", help="Run one collection cycle and exit")
    args = parser.parse_args(argv)
    if args.command == "demo" and (args.collect or args.once):
        parser.error("demo cannot collect live data")
    if args.once and args.command != "collect":
        parser.error("--once requires collect")
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    try:
        config = AttentionConfig.load(args.sources)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    path = args.db or str(PROJECT_ROOT / "data/attention" / ("demo.sqlite" if args.command == "demo" else "live.sqlite"))
    if args.command == "demo":
        existing = Store(path)
        if existing.get("mode") == "demo":
            store = existing
        else:
            store = seed_demo(path)
    else:
        store = Store(path)
        if store.get("mode") == "demo":
            parser.error("use demo to serve synthetic data, or select a live database")
    stop = threading.Event()
    try:
        collector = Collector(store, config) if args.collect or args.command == "collect" else None
    except ValueError as exc:
        parser.error(str(exc))
    if args.command == "collect" and args.once:
        collector.tick()
        for row in store.health_rows():
            print(f"{row['source']}: {row['status']} - {row['detail']}")
        required = ({"gmgn"} if config.gmgn_key else set()) | ({"x:" + t["id"] for t in config.topics} if config.x_token else set())
        statuses = {row["source"]: row["status"] for row in store.health_rows()}
        return 0 if required and all(statuses.get(source) == "ok" for source in required) else 2
    thread = None
    if args.command == "collect":
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        try:
            collector.run(stop)
        except KeyboardInterrupt:
            stop.set()
        return 0
    server = make_server(store, args.host, args.port)
    if collector:
        thread = threading.Thread(target=collector.run, args=(stop,), daemon=True)
        thread.start()
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print(f"Attention board: http://{args.host}:{server.server_port} | mode={store.get('mode', 'live')} | trading=off", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        if thread:
            thread.join(timeout=35)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Read-only HTTP API over the scanner evidence store."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
from urllib.parse import parse_qs, urlsplit

from src.radar.store import ScannerStore, SCHEMA_VERSION


def make_scanner_server(store: ScannerStore, host: str = "127.0.0.1", port: int = 8790):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def respond(self, status: int, body):
            payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            path = urlsplit(self.path)
            query = parse_qs(path.query)
            limit = int(query.get("limit", ["200"])[0])
            if not 1 <= limit <= 1000:
                self.respond(400, {"error": "limit must be between 1 and 1000"})
                return
            if path.path == "/api/v1/scanner/launches":
                rows = store.rows("launch", limit=limit)
            elif path.path == "/api/v1/scanner/graduations":
                rows = store.rows("graduation", limit=limit)
            elif path.path == "/api/v1/scanner/summary":
                self.respond(200, {
                    "schema_version": SCHEMA_VERSION,
                    "as_of": time.time(),
                    "trading_enabled": False,
                    "launches": len(store.rows("launch", limit=1000)),
                    "graduations": len(store.rows("graduation", limit=1000)),
                    "shadow_opens": len(store.rows("shadow_open", limit=1000)),
                    "shadow_exits": len(store.rows("shadow_exit", limit=1000)),
                })
                return
            elif path.path == "/api/v1/scanner/health":
                self.respond(200, {"schema_version": SCHEMA_VERSION, "as_of": time.time(),
                                   "trading_enabled": False, "mode": "read_only"})
                return
            else:
                self.respond(404, {"error": "not_found"})
                return
            self.respond(200, {"schema_version": SCHEMA_VERSION, "as_of": time.time(),
                               "trading_enabled": False, "count": len(rows), "rows": rows})

        def do_POST(self):
            self.respond(405, {"error": "read_only"})

        do_PUT = do_POST
        do_DELETE = do_POST

    return ThreadingHTTPServer((host, port), Handler)

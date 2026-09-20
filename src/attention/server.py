"""Local read-only HTTP surface, shared by the dashboard and future clients."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import time
from urllib.parse import parse_qs, urlsplit

from src.attention.board import build_board

STATIC = Path(__file__).with_name("web")


def make_server(store, host="127.0.0.1", port=8787):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def respond(self, status, body, content_type="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path)
            query = parse_qs(path.query)
            try:
                if path.path == "/api/v1/board":
                    stamp = float(query["as_of"][0]) if "as_of" in query else None
                    if stamp is not None and (not math.isfinite(stamp) or stamp <= 0 or stamp > time.time() + 1):
                        raise ValueError("as_of must be a positive, non-future timestamp")
                    board = build_board(store, stamp)
                    chain = query.get("chain", ["all"])[0]
                    if chain not in {"all", "sol", "bsc", "base", "eth"}:
                        raise ValueError("invalid chain")
                    if chain != "all":
                        board["tokens"] = [t for t in board["tokens"] if t["chain"] == chain]
                    self.respond(200, board)
                elif path.path == "/api/v1/events":
                    after = int(query.get("after", [0])[0])
                    limit = int(query.get("limit", [100])[0])
                    if after < 0 or not 1 <= limit <= 500:
                        raise ValueError("after >= 0; limit between 1 and 500 required")
                    events = store.events(after, limit)
                    self.respond(200, {"schema_version": 1, "mode": store.get("mode", "live"),
                                      "as_of": time.time(), "trading_enabled": False, "events": events,
                                      "next_cursor": events[-1]["seq"] if events else after})
                elif path.path == "/api/v1/health":
                    board = build_board(store)
                    self.respond(200, {k: board[k] for k in ("schema_version", "as_of", "mode", "trading_enabled", "sources")})
                elif path.path == "/favicon.ico":
                    self.respond(204, b"", "image/x-icon")
                elif path.path in {"/", "/app.js", "/style.css"}:
                    filename = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path.path]
                    mime = {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[filename]
                    self.respond(200, (STATIC / filename).read_bytes(), mime + "; charset=utf-8")
                else:
                    self.respond(404, {"error": "not_found"})
            except (ValueError, TypeError) as exc:
                self.respond(400, {"error": str(exc)})

        def do_POST(self):
            self.respond(405, {"error": "read_only"})

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

    return ThreadingHTTPServer((host, port), Handler)

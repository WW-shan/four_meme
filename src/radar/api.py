"""Read-only HTTP API and dashboard over the scanner evidence store."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
from urllib.parse import parse_qs, urlsplit

from src.radar.store import ScannerStore, SCHEMA_VERSION

INDEX_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scanner - read-only</title><link rel="stylesheet" href="/style.css"></head>
<body><header><h1>Scanner</h1><span>READ-ONLY / NO ORDERS</span></header>
<main>
<section><h2>Instant discovery</h2><p class="note">New launches and graduations observed by the radar.</p><div id="launches">Loading...</div></section>
<section><h2>Strict audit</h2><p class="note">Fail-closed safety reports. Unknown fields never pass.</p><div id="safety">Loading...</div></section>
<section><h2>Shadow tracking</h2><p class="note">Closed shadow trades and gate inputs; no wallet access.</p><div id="shadow">Loading...</div></section>
</main><script src="/app.js"></script></body></html>"""

APP_JS = """async function load(id, url, render) {
  const target = document.getElementById(id);
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const data = await response.json();
    target.textContent = '';
    if (!data.rows || data.rows.length === 0) { target.innerHTML = '<p class="empty">No records yet.</p>'; return; }
    render(target, data.rows);
  } catch (error) { target.innerHTML = '<p class="empty">Unavailable: ' + error.message + '</p>'; }
}
function rows(target, rows, fields) {
  const table = document.createElement('table');
  const head = document.createElement('tr');
  fields.forEach(field => { const th = document.createElement('th'); th.textContent = field; head.append(th); });
  table.append(head);
  rows.slice(-50).reverse().forEach(row => {
    const tr = document.createElement('tr');
    fields.forEach(field => { const td = document.createElement('td'); td.textContent = row.payload[field] ?? row[field] ?? '-'; tr.append(td); });
    table.append(tr);
  });
  target.append(table);
}
load('launches', '/api/v1/scanner/launches', (target, rowsData) => rows(target, rowsData, ['token', 'source_event', 'quote_symbol', 'chain_time']));
load('safety', '/api/v1/scanner/safety', (target, rowsData) => rows(target, rowsData, ['token', 'verdict', 'score', 'reason_codes']));
load('shadow', '/api/v1/scanner/shadow', (target, rowsData) => rows(target, rowsData, ['token', 'reason', 'pnl_quote', 'latency_seconds']));
"""

STYLE_CSS = """body{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;margin:0;background:#f4f1e8;color:#24291f}
header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #24291f;padding:18px 4vw}
h1{font-size:20px;margin:0}header span{font-size:10px;letter-spacing:1px;color:#717565}
main{max-width:1100px;margin:auto;padding:24px 4vw}section{margin-bottom:36px}h2{font-size:18px;margin:0 0 4px}
.note,.empty{color:#717565;font-size:12px}table{border-collapse:collapse;width:100%;font-size:12px}
th,td{text-align:left;border-bottom:1px solid #d8d8c9;padding:8px 6px}th{color:#717565}
@media(max-width:600px){th,td{font-size:10px;padding:6px 3px}}"""


def make_scanner_server(store: ScannerStore, host: str = "127.0.0.1", port: int = 8790):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def respond(self, status: int, body, content_type: str = "application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path)
            query = parse_qs(path.query)
            try:
                limit = int(query.get("limit", ["200"])[0])
            except ValueError:
                self.respond(400, {"error": "limit must be an integer"})
                return
            if not 1 <= limit <= 1000:
                self.respond(400, {"error": "limit must be between 1 and 1000"})
                return
            if path.path == "/":
                self.respond(200, INDEX_HTML, "text/html; charset=utf-8")
                return
            if path.path == "/app.js":
                self.respond(200, APP_JS, "text/javascript; charset=utf-8")
                return
            if path.path == "/style.css":
                self.respond(200, STYLE_CSS, "text/css; charset=utf-8")
                return
            if path.path == "/api/v1/scanner/summary":
                self.respond(200, {
                    "schema_version": SCHEMA_VERSION, "as_of": time.time(), "trading_enabled": False,
                    "launches": len(store.rows("launch", limit=1000)),
                    "graduations": len(store.rows("graduation", limit=1000)),
                    "safety_reports": len(store.rows("safety_report", limit=1000)),
                    "decisions": len(store.rows("decision", limit=1000)),
                    "shadow_closes": len(store.rows("shadow_close", limit=1000)),
                })
                return
            if path.path == "/api/v1/scanner/health":
                self.respond(200, {"schema_version": SCHEMA_VERSION, "as_of": time.time(),
                                   "trading_enabled": False, "mode": "read_only"})
                return
            kind = {
                "/api/v1/scanner/launches": "launch",
                "/api/v1/scanner/graduations": "graduation",
                "/api/v1/scanner/safety": "safety_report",
                "/api/v1/scanner/decisions": "decision",
                "/api/v1/scanner/shadow": "shadow_close",
            }.get(path.path)
            if kind is None:
                self.respond(404, {"error": "not_found"})
                return
            rows = store.rows(kind, limit=limit)
            self.respond(200, {"schema_version": SCHEMA_VERSION, "as_of": time.time(),
                               "trading_enabled": False, "count": len(rows), "rows": rows})

        def do_POST(self):
            self.respond(405, {"error": "read_only"})

        do_PUT = do_POST
        do_DELETE = do_POST

    return ThreadingHTTPServer((host, port), Handler)

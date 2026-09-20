"""Read-only HTTP API and dashboard over the scanner evidence store."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import time
from urllib.parse import parse_qs, urlsplit

from src.radar.store import ScannerStore, SCHEMA_VERSION

DEFAULT_CHAINS_CONFIG = Path(__file__).resolve().parents[2] / "config" / "chains.json"

# A chain whose newest observation is older than this is reported as stale. A quiet chain must
# never be rendered as "heat falling" -- the dashboard says the source is not reporting.
STALE_AFTER_SECONDS = 1800.0

INDEX_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scanner - read-only</title><link rel="stylesheet" href="/style.css"></head>
<body><header><h1>Scanner</h1><span>READ-ONLY / NO ORDERS</span></header>
<main>
<section id="chains-section"><h2>Chain status</h2><p class="note">Per-chain source health. A stale chain is a reporting gap, not falling heat.</p><div id="chains">Loading...</div></section>
<section id="filters-section"><h2>Filters</h2>
<div class="filters">
<label>Chain <select id="filter-chain"><option value="">all</option></select></label>
<label>Platform <select id="filter-platform"><option value="">all</option></select></label>
<label>Window <select id="filter-window">
<option value="">all time</option><option value="3600">1h</option><option value="21600">6h</option>
<option value="86400">24h</option><option value="604800">7d</option></select></label>
<button type="button" id="filter-reset">reset</button>
</div><p class="note" id="filter-note"></p></section>
<section><h2>Instant discovery</h2><p class="note">New launches and graduations observed by the radar. Heat is never merged across chains.</p><div id="launches">Loading...</div></section>
<section><h2>Strict audit</h2><p class="note">Fail-closed safety reports. Unknown fields never pass.</p><div id="safety">Loading...</div></section>
<section><h2>Shadow tracking</h2><p class="note">Closed shadow trades and gate inputs; no wallet access.</p><div id="shadow">Loading...</div></section>
</main><script src="/app.js"></script></body></html>"""

APP_JS = """const state = { chain: '', platform: '', window: '' };
function qs(extra) {
  const params = new URLSearchParams();
  if (state.chain) params.set('chain', state.chain);
  if (state.platform) params.set('platform', state.platform);
  if (state.window) params.set('window', state.window);
  if (extra) Object.entries(extra).forEach(([key, value]) => params.set(key, value));
  const text = params.toString();
  return text ? '?' + text : '';
}
async function load(id, url, render) {
  const target = document.getElementById(id);
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const data = await response.json();
    target.textContent = '';
    if (!data.rows || data.rows.length === 0) { target.innerHTML = '<p class="empty">No records for this filter.</p>'; return; }
    render(target, data.rows, data);
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
function ageText(seconds) {
  if (seconds === null || seconds === undefined) return 'no data';
  if (seconds < 90) return Math.round(seconds) + 's ago';
  if (seconds < 5400) return Math.round(seconds / 60) + 'm ago';
  if (seconds < 172800) return (seconds / 3600).toFixed(1) + 'h ago';
  return (seconds / 86400).toFixed(1) + 'd ago';
}
async function loadChains() {
  const target = document.getElementById('chains');
  try {
    const response = await fetch('/api/v1/scanner/chains');
    const data = await response.json();
    target.textContent = '';
    const strip = document.createElement('div');
    strip.className = 'chips';
    (data.chains || []).forEach(chain => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'chip status-' + chain.status;
      chip.textContent = chain.chain + ' - ' + ageText(chain.age_seconds) + ' - ' + chain.observations;
      chip.title = chain.status === 'stale' ? 'source not reporting (stale)' : chain.status;
      chip.addEventListener('click', () => { document.getElementById('filter-chain').value = chain.chain; state.chain = chain.chain; refresh(); });
      strip.append(chip);
    });
    target.append(strip);
    const select = document.getElementById('filter-chain');
    (data.chains || []).forEach(chain => {
      if ([...select.options].some(option => option.value === chain.chain)) return;
      const option = document.createElement('option');
      option.value = chain.chain; option.textContent = chain.chain;
      select.append(option);
    });
    const platforms = document.getElementById('filter-platform');
    (data.platforms || []).forEach(item => {
      if ([...platforms.options].some(option => option.value === item.platform)) return;
      const option = document.createElement('option');
      option.value = item.platform; option.textContent = item.platform + ' (' + item.chain + ')';
      platforms.append(option);
    });
  } catch (error) { target.innerHTML = '<p class="empty">Unavailable: ' + error.message + '</p>'; }
}
function refresh() {
  document.getElementById('filter-note').textContent =
    'chain=' + (state.chain || 'all') + ' platform=' + (state.platform || 'all') + ' window=' + (state.window || 'all');
  load('launches', '/api/v1/scanner/launches' + qs(), (target, rowsData) => rows(target, rowsData, ['chain', 'token', 'source_event', 'quote_symbol']));
  load('safety', '/api/v1/scanner/safety' + qs(), (target, rowsData) => rows(target, rowsData, ['chain', 'token', 'verdict', 'score']));
  load('shadow', '/api/v1/scanner/shadow' + qs(), (target, rowsData) => rows(target, rowsData, ['chain', 'token', 'reason', 'pnl_quote']));
}
['chain', 'platform', 'window'].forEach(name => {
  document.getElementById('filter-' + name).addEventListener('change', event => { state[name] = event.target.value; refresh(); });
});
document.getElementById('filter-reset').addEventListener('click', () => {
  state.chain = ''; state.platform = ''; state.window = '';
  document.getElementById('filter-chain').value = '';
  document.getElementById('filter-platform').value = '';
  document.getElementById('filter-window').value = '';
  refresh();
});
loadChains();
refresh();
setInterval(loadChains, 60000);
"""

STYLE_CSS = """body{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;margin:0;background:#f4f1e8;color:#24291f}
header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #24291f;padding:18px 4vw}
h1{font-size:20px;margin:0}header span{font-size:10px;letter-spacing:1px;color:#717565}
main{max-width:1100px;margin:auto;padding:24px 4vw}section{margin-bottom:36px}h2{font-size:18px;margin:0 0 4px}
.note,.empty{color:#717565;font-size:12px}table{border-collapse:collapse;width:100%;font-size:12px}
th,td{text-align:left;border-bottom:1px solid #d8d8c9;padding:8px 6px}th{color:#717565}
.filters{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:8px 0}
.filters label{display:flex;flex-direction:column;font-size:11px;color:#717565;gap:4px}
.filters select,.filters button{font:inherit;font-size:12px;padding:6px 8px;border:1px solid #b9b9a4;background:#fff;color:inherit}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{font:inherit;font-size:11px;padding:6px 10px;border:1px solid #b9b9a4;background:#fff;color:inherit;cursor:pointer}
.status-live{border-color:#3f6f3f;color:#274b27}
.status-stale{border-color:#a34a2a;color:#7d3418;background:#fbeee7}
.status-idle,.status-no-data{color:#717565}
@media(max-width:600px){th,td{font-size:10px;padding:6px 3px}.filters{gap:8px}}
"""


def load_declared_chains(path: Path | str | None = DEFAULT_CHAINS_CONFIG) -> list[dict]:
    """Declared chains from config/chains.json, so a silent chain still shows up as stale."""
    if not path:
        return []
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []
    declared = []
    for item in payload.get("chains") or []:
        chain = item.get("chain")
        if not chain:
            continue
        declared.append({
            "chain": chain,
            "enabled": bool(item.get("enabled")),
            "family": item.get("family"),
            "chain_id": item.get("chain_id"),
            "platforms": list(item.get("platforms") or []),
            "launchpads": len(item.get("launchpads") or []),
        })
    return declared


def chain_statuses(store: ScannerStore, *, declared: list[dict] | None = None,
                   since: float = 0, stale_after: float = STALE_AFTER_SECONDS) -> list[dict]:
    """Merge declared chains with observed activity; a quiet chain reports stale, not cold."""
    now = time.time()
    observed = {row["chain"]: row for row in store.chain_activity(since=since)}
    rows: list[dict] = []
    seen: set[str] = set()
    for item in declared or []:
        chain = item["chain"]
        seen.add(chain)
        activity = observed.get(chain) or {}
        last = activity.get("last_observed_at")
        age = (now - float(last)) if last else None
        status = "idle" if age is None else ("live" if age <= stale_after else "stale")
        rows.append({**item, "observations": int(activity.get("observations") or 0),
                     "last_observed_at": last, "age_seconds": age, "status": status})
    for chain, activity in observed.items():
        if chain in seen:
            continue
        last = activity.get("last_observed_at")
        age = (now - float(last)) if last else None
        rows.append({"chain": chain, "enabled": False, "observations": int(activity.get("observations") or 0),
                     "last_observed_at": last, "age_seconds": age,
                     "status": "live" if (age is not None and age <= stale_after) else ("stale" if age is not None else "idle")})
    rows.sort(key=lambda item: (item["status"] != "live", item["chain"]))
    return rows


def make_scanner_server(store: ScannerStore, host: str = "127.0.0.1", port: int = 8790,
                        chains_config: Path | str | None = DEFAULT_CHAINS_CONFIG,
                        stale_after: float = STALE_AFTER_SECONDS):
    declared = load_declared_chains(chains_config)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def respond(self, status: int, body, content_type: str = "application/json; charset=utf-8"):
            if isinstance(body, bytes):
                payload = body
            elif content_type.startswith("application/json"):
                payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            else:
                # HTML/JS/CSS must go out verbatim: JSON-encoding them makes the browser treat
                # the body as data, not markup, and the dashboard never renders.
                payload = str(body).encode("utf-8")
            body = payload
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def fail(self, message: str):
            self.respond(400, {"error": message})

        def do_GET(self):
            path = urlsplit(self.path)
            query = parse_qs(path.query)
            try:
                limit = int(query.get("limit", ["200"])[0])
            except ValueError:
                self.fail("limit must be an integer")
                return
            if not 1 <= limit <= 1000:
                self.fail("limit must be between 1 and 1000")
                return
            chain = (query.get("chain", [""])[0] or "").strip() or None
            platform = (query.get("platform", [""])[0] or "").strip() or None
            try:
                window = int(query.get("window", ["0"])[0])
            except ValueError:
                self.fail("window must be an integer number of seconds")
                return
            if window < 0:
                self.fail("window must be zero or positive")
                return
            since = time.time() - window if window else 0
            if path.path == "/":
                self.respond(200, INDEX_HTML, "text/html; charset=utf-8")
                return
            if path.path == "/app.js":
                self.respond(200, APP_JS, "text/javascript; charset=utf-8")
                return
            if path.path == "/style.css":
                self.respond(200, STYLE_CSS, "text/css; charset=utf-8")
                return
            if path.path == "/favicon.ico":
                # Browsers always ask; answering keeps the console free of 404 noise.
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
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
            if path.path == "/api/v1/scanner/chains":
                self.respond(200, {
                    "schema_version": SCHEMA_VERSION, "as_of": time.time(), "trading_enabled": False,
                    "stale_after_seconds": stale_after, "window": window,
                    "chains": chain_statuses(store, declared=declared, since=since, stale_after=stale_after),
                    "platforms": store.platform_activity(chain=chain, since=since),
                })
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
            rows = store.rows(kind, limit=limit, since=since, chain=chain, platform=platform)
            self.respond(200, {"schema_version": SCHEMA_VERSION, "as_of": time.time(),
                               "trading_enabled": False, "count": len(rows),
                               "filters": {"chain": chain, "platform": platform, "window_seconds": window},
                               "rows": rows})

        def do_POST(self):
            self.respond(405, {"error": "read_only"})

        do_PUT = do_POST
        do_DELETE = do_POST

    return ThreadingHTTPServer((host, port), Handler)

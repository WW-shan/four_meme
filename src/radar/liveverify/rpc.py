"""Minimal JSON-RPC client with endpoint rotation and explicit failure evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Iterable, Mapping

import requests


@dataclass
class RpcCallError(Exception):
    method: str
    attempts: list[dict] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - formatting helper
        last = self.attempts[-1] if self.attempts else {}
        return f"{self.method} failed: {last.get('error') or last.get('exception') or 'unknown'}"


class JsonRpcClient:
    """JSON-RPC over HTTP with ordered endpoint fallback.

    The client never hides failures: every attempt (endpoint, error, latency) is kept so a
    verification report can show *why* a chain or contract could not be probed.
    """

    def __init__(self, endpoints: Iterable[str], *, timeout: float = 30.0, session=None):
        urls = [url for url in endpoints if url]
        if not urls:
            raise ValueError("at least one RPC endpoint is required")
        self.endpoints = urls
        self.timeout = timeout
        self.session = session or requests.Session()
        self.attempts: list[dict] = []

    def call_raw(self, method: str, params: list | None = None, *, max_retries: int = 2) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        last_error: str | None = None
        for url in self.endpoints:
            for attempt in range(max_retries + 1):
                started = time.time()
                try:
                    response = self.session.post(url, json=payload, timeout=self.timeout)
                    body = response.json()
                except Exception as exc:  # network / decode failures
                    last_error = f"{type(exc).__name__}: {exc}"
                    self.attempts.append({"url": url, "method": method, "exception": last_error,
                                          "seconds": round(time.time() - started, 3)})
                    break
                seconds = round(time.time() - started, 3)
                if isinstance(body, Mapping) and "result" in body:
                    self.attempts.append({"url": url, "method": method, "ok": True, "seconds": seconds})
                    return body["result"]
                error = body.get("error") if isinstance(body, Mapping) else body
                last_error = str(error)
                rate_limited = "429" in last_error or "too many requests" in last_error.lower()
                self.attempts.append({"url": url, "method": method, "ok": False, "error": last_error,
                                      "seconds": seconds, "rate_limited": rate_limited})
                if rate_limited and attempt < max_retries:
                    time.sleep(3 * (attempt + 1))
                    continue
                break
        raise RpcCallError(method, list(self.attempts))

    def call(self, method: str, params: list | None = None, *, max_retries: int = 2) -> Any:
        return self.call_raw(method, params, max_retries=max_retries)

    @property
    def last_endpoint(self) -> str | None:
        for attempt in reversed(self.attempts):
            if attempt.get("ok"):
                return attempt["url"]
        return None


def hex_int(value: str | int | None) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return int(value, 16)


def probe_log_span(client: JsonRpcClient, latest_block: int, *, spans: Iterable[int] = (5, 20, 100, 500, 2000)) -> dict:
    """Find the largest block window the endpoint accepts for an unfiltered eth_getLogs.

    Returns evidence for each span tried. Chains that refuse unfiltered queries are reported
    as ``requires_address_filter`` instead of being treated as broken.
    """
    results: list[dict] = []
    accepted: int | None = None
    for span in spans:
        from_block = max(0, latest_block - span)
        try:
            logs = client.call("eth_getLogs", [{"fromBlock": hex(from_block), "toBlock": "latest"}])
        except RpcCallError as exc:
            message = str(exc)
            results.append({"span": span, "ok": False, "error": message})
            if "specify an address" in message.lower():
                return {"mode": "requires_address_filter", "accepted_span": None, "attempts": results}
            continue
        results.append({"span": span, "ok": True, "logs": len(logs or [])})
        accepted = span
    return {"mode": "unfiltered" if accepted else "unavailable", "accepted_span": accepted, "attempts": results}

"""Snapshot fetchers with injectable HTTP, per-source caching and rate limits."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable


@dataclass(frozen=True)
class FetchResult:
    source: str
    ok: bool
    payload: Any = None
    error: str | None = None
    http_status: int | None = None
    fetched_at: float = 0.0


class SourceCache:
    def __init__(self, ttl_seconds: float = 60.0, clock: Callable[[], float] = time.time):
        self.ttl_seconds = float(ttl_seconds)
        self.clock = clock
        self._entries: dict[tuple[str, str], tuple[float, FetchResult]] = {}

    def get(self, source: str, key: str) -> FetchResult | None:
        entry = self._entries.get((source, key))
        if entry is None:
            return None
        stored_at, result = entry
        if self.clock() - stored_at > self.ttl_seconds:
            return None
        return result

    def set(self, source: str, key: str, result: FetchResult) -> None:
        self._entries[(source, key)] = (self.clock(), result)


class SnapshotFetcher:
    """Fetch raw provider payloads. Mapping to snapshot fields lives in snapshot.py."""

    def __init__(self, session=None, *, cache: SourceCache | None = None,
                 clock: Callable[[], float] = time.time, goplus_key: str | None = None,
                 min_intervals: dict[str, float] | None = None, timeout: float = 15.0):
        self.session = session
        self.cache = cache if cache is not None else SourceCache(clock=clock)
        self.clock = clock
        self.goplus_key = goplus_key
        self.timeout = timeout
        self.min_intervals = {"goplus": 0.5, "honeypot": 0.5, "dexscreener": 0.2, "gmgn": 0.5}
        self.min_intervals.update(min_intervals or {})
        self._last_call: dict[str, float] = {}

    def _request(self, source: str, url: str, params: dict | None = None,
                 headers: dict | None = None) -> FetchResult:
        cached = self.cache.get(source, url + repr(sorted((params or {}).items())))
        if cached is not None:
            return cached
        if self.session is None:
            return FetchResult(source, False, error="no_http_session", fetched_at=self.clock())
        last = self._last_call.get(source, 0.0)
        wait = self.min_intervals.get(source, 0.0) - (self.clock() - last)
        if wait > 0:
            time.sleep(wait)
        self._last_call[source] = self.clock()
        try:
            response = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        except Exception as exc:  # network failures are data, not crashes
            return FetchResult(source, False, error=str(exc)[:200], fetched_at=self.clock())
        status = getattr(response, "status_code", None)
        if status != 200:
            return FetchResult(source, False, error=f"HTTP {status}", http_status=status, fetched_at=self.clock())
        try:
            payload = response.json()
        except Exception as exc:
            return FetchResult(source, False, error=f"invalid_json:{exc}"[:200], http_status=status, fetched_at=self.clock())
        result = FetchResult(source, True, payload=payload, http_status=status, fetched_at=self.clock())
        self.cache.set(source, url + repr(sorted((params or {}).items())), result)
        return result

    def goplus_token_security(self, token: str, chain_id: int = 56) -> FetchResult:
        params = {"contract_addresses": token, "chain_id": chain_id}
        if self.goplus_key:
            params["app_key"] = self.goplus_key
        return self._request("goplus", "https://api.gopluslabs.io/api/v1/token_security/56", params)

    def honeypot_check(self, token: str, chain_id: int = 56) -> FetchResult:
        return self._request("honeypot", "https://api.honeypot.is/v2/IsHoneypot",
                             {"address": token, "chainID": chain_id})

    def dexscreener_pairs(self, token: str) -> FetchResult:
        return self._request("dexscreener", f"https://api.dexscreener.com/token-pairs/v1/bsc/{token}", None)

    def gmgn_token_info(self, token: str, chain: str = "bsc") -> FetchResult:
        if not self.goplus_key and not getattr(self, "gmgn_key", None):
            return FetchResult("gmgn", False, error="gmgn_api_key_missing", fetched_at=self.clock())
        return self._request("gmgn", "https://openapi.gmgn.ai/v1/token/info",
                             {"chain": chain, "address": token},
                             {"X-APIKEY": getattr(self, "gmgn_key", "")})

    def fetch_all(self, token: str) -> dict[str, FetchResult]:
        return {
            "goplus": self.goplus_token_security(token),
            "honeypot": self.honeypot_check(token),
            "dexscreener": self.dexscreener_pairs(token),
            "gmgn": self.gmgn_token_info(token),
        }

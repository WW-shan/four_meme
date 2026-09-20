"""Snapshot fetchers with injectable HTTP, per-source caching and rate limits."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable


# DexScreener keys its token-pairs endpoint by chain slug, not by chain id. Chains with no
# public slug must fail closed instead of silently querying BSC.
DEXSCREENER_CHAIN_SLUGS = {
    1: "ethereum",
    56: "bsc",
    8453: "base",
    42161: "arbitrum",
}


def coerce_chain_id(chain_id: object) -> int | None:
    """Return a usable positive chain id, or None when the chain is unknown.

    ``None`` is a real answer here: the caller has no chain mapping, so the fetcher must
    fail closed rather than defaulting to BSC.
    """
    if chain_id is None or isinstance(chain_id, bool):
        return None
    try:
        value = int(chain_id)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


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
                 gmgn_key: str | None = None,
                 min_intervals: dict[str, float] | None = None, timeout: float = 15.0):
        self.session = session
        self.cache = cache if cache is not None else SourceCache(clock=clock)
        self.clock = clock
        self.goplus_key = goplus_key
        self.gmgn_key = gmgn_key
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

    def goplus_token_security(self, token: str, chain_id: object = 56) -> FetchResult:
        # GoPlus puts the chain in the path; sending chain_id as a query parameter while
        # calling the BSC path would silently return BSC data for another chain's token.
        resolved = coerce_chain_id(chain_id)
        if resolved is None:
            return FetchResult("goplus", False, error=f"unsupported_chain:{chain_id}",
                               fetched_at=self.clock())
        params = {"contract_addresses": token}
        if self.goplus_key:
            params["app_key"] = self.goplus_key
        url = f"https://api.gopluslabs.io/api/v1/token_security/{resolved}"
        return self._request("goplus", url, params)

    def honeypot_check(self, token: str, chain_id: object = 56) -> FetchResult:
        resolved = coerce_chain_id(chain_id)
        if resolved is None:
            return FetchResult("honeypot", False, error=f"unsupported_chain:{chain_id}",
                               fetched_at=self.clock())
        return self._request("honeypot", "https://api.honeypot.is/v2/IsHoneypot",
                             {"address": token, "chainID": resolved})

    def dexscreener_pairs(self, token: str, chain_id: object = 56) -> FetchResult:
        resolved = coerce_chain_id(chain_id)
        slug = DEXSCREENER_CHAIN_SLUGS.get(resolved) if resolved is not None else None
        if slug is None:
            return FetchResult("dexscreener", False, error=f"unsupported_chain:{chain_id}",
                               fetched_at=self.clock())
        return self._request("dexscreener", f"https://api.dexscreener.com/token-pairs/v1/{slug}/{token}", None)

    def gmgn_token_info(self, token: str, chain: str = "bsc") -> FetchResult:
        if not self.gmgn_key:
            return FetchResult("gmgn", False, error="gmgn_api_key_missing", fetched_at=self.clock())
        return self._request("gmgn", "https://openapi.gmgn.ai/v1/token/info",
                             {"chain": chain, "address": token},
                             {"X-APIKEY": self.gmgn_key})

    def fetch_all(self, token: str, chain_id: object = 56, chain: str = "bsc") -> dict[str, FetchResult]:
        """Fetch every safety source for one token.

        ``chain_id`` selects the provider endpoint, so a non-BSC token is never scored with
        BSC data. Unknown chains fail closed: the filters see an error and reject.
        """
        return {
            "goplus": self.goplus_token_security(token, chain_id),
            "honeypot": self.honeypot_check(token, chain_id),
            "dexscreener": self.dexscreener_pairs(token, chain_id),
            "gmgn": self.gmgn_token_info(token, chain),
        }

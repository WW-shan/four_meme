"""Keyless verified-contract lookup through Sourcify (source + ABI, no API key required)."""

from __future__ import annotations

from dataclasses import dataclass, field

import requests

SOURCIFY_V2 = "https://sourcify.dev/server/v2/contract/{chain_id}/{address}"


@dataclass
class SourcifyResult:
    chain_id: int
    address: str
    verified: bool
    name: str | None = None
    compiler: str | None = None
    abi: list = field(default_factory=list)
    http_status: int | None = None
    error: str | None = None

    def to_dict(self, *, include_abi: bool = False) -> dict:
        payload = {
            "chain_id": self.chain_id,
            "address": self.address,
            "verified": self.verified,
            "name": self.name,
            "compiler": self.compiler,
            "http_status": self.http_status,
            "error": self.error,
            "abi_entries": len(self.abi),
        }
        if include_abi:
            payload["abi"] = self.abi
        return payload


class SourcifyClient:
    def __init__(self, session=None, *, timeout: float = 25.0):
        self.session = session or requests.Session()
        self.timeout = timeout
        self._cache: dict[tuple[int, str], SourcifyResult] = {}

    def lookup(self, chain_id: int, address: str) -> SourcifyResult:
        key = (int(chain_id), address.lower())
        if key in self._cache:
            return self._cache[key]
        url = SOURCIFY_V2.format(chain_id=key[0], address=key[1])
        result = SourcifyResult(chain_id=key[0], address=key[1], verified=False)
        try:
            response = self.session.get(url, params={"fields": "compilation,abi"}, timeout=self.timeout)
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            self._cache[key] = result
            return result
        result.http_status = response.status_code
        if response.status_code != 200:
            result.error = f"http_status={response.status_code}"
            self._cache[key] = result
            return result
        try:
            body = response.json()
        except Exception as exc:
            result.error = f"invalid_json: {exc}"
            self._cache[key] = result
            return result
        compilation = body.get("compilation") or {}
        result.verified = True
        result.name = compilation.get("name") or compilation.get("fullyQualifiedName")
        result.compiler = compilation.get("compilerVersion") or compilation.get("compiler")
        abi = body.get("abi") or []
        result.abi = abi if isinstance(abi, list) else []
        self._cache[key] = result
        return result

"""ABI acquisition for live checks: Sourcify, GitHub contents API, and EIP-1967 proxies."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import requests

EIP1967_IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
CACHE_DIR = Path("/tmp/meme-liveverify-abi")
PROXY_EVENT_NAMES = {"AdminChanged", "Upgraded", "BeaconUpgraded"}


def load_local_abi(path: str | Path) -> list:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_github_abi(api_url: str, *, session=None, timeout: float = 25.0) -> list:
    """Load an ABI from a GitHub contents API URL (raw.githubusercontent.com is blocked in this env)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = base64.urlsafe_b64encode(api_url.encode()).decode()[:120]
    cache_path = CACHE_DIR / f"{cache_key}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    response = (session or requests).get(api_url, timeout=timeout)
    body = response.json()
    if isinstance(body, list):
        abi = body
    elif isinstance(body, dict) and body.get("content"):
        abi = json.loads(base64.b64decode(body["content"]).decode("utf-8"))
    elif isinstance(body, dict) and isinstance(body.get("abi"), list):
        abi = body["abi"]
    else:
        raise ValueError(f"unsupported ABI payload from {api_url}: {str(body)[:120]}")
    if isinstance(abi, dict):
        abi = abi.get("abi") or []
    cache_path.write_text(json.dumps(abi), encoding="utf-8")
    return abi


def is_proxy_only_abi(abi: list) -> bool:
    events = {entry.get("name") for entry in abi if entry.get("type") == "event"}
    functions = {entry.get("name") for entry in abi if entry.get("type") == "function"}
    return bool(events) and events <= PROXY_EVENT_NAMES and len(functions) <= 3


def read_proxy_implementation(client, address: str) -> str | None:
    raw = client.call("eth_getStorageAt", [address, EIP1967_IMPLEMENTATION_SLOT, "latest"])
    value = str(raw or "").removeprefix("0x")
    if len(value) < 40:
        return None
    implementation = "0x" + value[-40:]
    if implementation == "0x" + "00" * 20:
        return None
    return implementation

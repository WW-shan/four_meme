"""Map provider payloads into the scanner TokenSnapshot contract."""

from __future__ import annotations

from typing import Any, Mapping

from src.safety.fetchers import FetchResult


def _float(value: Any) -> float | None:
    if value in (None, "", "unknown"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def _goplus_map(result: FetchResult, token: str) -> dict:
    payload = result.payload if isinstance(result.payload, Mapping) else {}
    entry = (payload.get("result") or {}).get(token.lower()) or {}
    if not entry:
        return {}
    holders = entry.get("holders") or []
    top1 = max((_float(item.get("percent")) or 0.0) for item in holders[:1]) if holders else None
    top10 = sum(_float(item.get("percent")) or 0.0 for item in holders[:10]) if holders else None
    locked = entry.get("lp_holder_count")
    return {
        "honeypot_sim": not bool(_bool(entry.get("is_honeypot"))),
        "buy_tax_pct": _float(entry.get("buy_tax")) and (_float(entry.get("buy_tax")) * 100),
        "sell_tax_pct": _float(entry.get("sell_tax")) and (_float(entry.get("sell_tax")) * 100),
        "mint_authority": "renounced" if _bool(entry.get("is_mintable")) is False else entry.get("is_mintable"),
        "freeze_authority": None,
        "owner_renounced": _bool(entry.get("can_take_back_ownership")) is False,
        "blacklist": _bool(entry.get("is_blacklisted")),
        "pausable": _bool(entry.get("transfer_pausable")),
        "top1_pct": top1,
        "top10_pct": top10,
        "non_lp_max_pct": max((_float(item.get("percent")) or 0.0) for item in holders[:1]) if holders else None,
        "lp_holder_count": locked,
        "_source": "goplus",
    }


def _honeypot_map(result: FetchResult) -> dict:
    payload = result.payload if isinstance(result.payload, Mapping) else {}
    simulation = payload.get("simulationResult") or {}
    honeypot = payload.get("honeypotResult") or {}
    return {
        "honeypot_sim": (not bool(honeypot.get("isHoneypot"))) if honeypot else None,
        "buy_tax_pct": _float(simulation.get("buyTax")) and 100 * _float(simulation.get("buyTax")),
        "sell_tax_pct": _float(simulation.get("sellTax")) and 100 * _float(simulation.get("sellTax")),
        "_source": "honeypot",
    }


def _dexscreener_map(result: FetchResult) -> dict:
    payload = result.payload
    if isinstance(payload, list):
        pairs = payload
    elif isinstance(payload, Mapping):
        pairs = payload.get("pairs") or payload.get("pair") or []
    else:
        pairs = []
    if isinstance(pairs, Mapping):
        pairs = [pairs]
    best = None
    for pair in pairs or []:
        if not isinstance(pair, Mapping):
            continue
        liquidity = _float((pair.get("liquidity") or {}).get("usd"))
        if best is None or (liquidity or 0) > (best[0] or 0):
            best = (liquidity, pair)
    if best is None:
        return {}
    liquidity, pair = best
    return {
        "liquidity_usd": liquidity,
        "mcap_usd": _float(pair.get("marketCap")) or _float(pair.get("fdv")),
        "price_usd": _float(pair.get("priceUsd")),
        "social_url": ((pair.get("info") or {}).get("socials") or [{}])[0].get("url"),
        "_source": "dexscreener",
    }


def _gmgn_map(result: FetchResult) -> dict:
    payload = result.payload if isinstance(result.payload, Mapping) else {}
    data = payload.get("data") or {}
    if not isinstance(data, Mapping):
        return {}
    return {
        "honeypot_sim": (not bool(data.get("is_honeypot"))) if "is_honeypot" in data else None,
        "buy_tax_pct": _float(data.get("buy_tax")) and 100 * _float(data.get("buy_tax")),
        "sell_tax_pct": _float(data.get("sell_tax")) and 100 * _float(data.get("sell_tax")),
        "liquidity_usd": _float(data.get("liquidity")),
        "mcap_usd": _float(data.get("market_cap")),
        "top10_pct": _float(data.get("top_10_holder_rate")) and 100 * _float(data.get("top_10_holder_rate")),
        "dev_holding_pct": _float(data.get("dev_team_hold_rate")) and 100 * _float(data.get("dev_team_hold_rate")),
        "_source": "gmgn",
    }


def build_snapshot(token: str, fetched: Mapping[str, FetchResult], onchain: Mapping[str, Any] | None = None) -> dict:
    """Merge provider payloads and on-chain reads. Missing fields stay missing."""
    snapshot: dict[str, Any] = {"token": token, "sources": {}}
    for name, result in fetched.items():
        if not getattr(result, "ok", False):
            snapshot["sources"][name] = "error" if result.error else "unknown"
            continue
        if name == "goplus":
            mapped = _goplus_map(result, token)
        elif name == "honeypot":
            mapped = _honeypot_map(result)
        elif name == "dexscreener":
            mapped = _dexscreener_map(result)
        elif name == "gmgn":
            mapped = _gmgn_map(result)
        else:
            mapped = {}
        source_name = mapped.pop("_source", name)
        for key, value in mapped.items():
            if value is not None and snapshot.get(key) is None:
                snapshot[key] = value
        snapshot["sources"][source_name] = "ok"
    for key, value in (onchain or {}).items():
        if value is not None:
            snapshot[key] = value
    return snapshot

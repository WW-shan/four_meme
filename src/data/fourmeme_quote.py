"""Quote-asset classification for Four.meme market data.

Four.meme tokens can be quoted in native BNB or in other assets (USDT, GMEB and
others). A raw ``etherAmount``/``cost`` field is denominated in the token's own
quote asset, so it must never be presented as BNB or converted with a BNB/USD
rate without recording the quote asset and a historical FX rate.
"""

from __future__ import annotations

from typing import Any

NATIVE_QUOTE_ADDRESS = "0x0000000000000000000000000000000000000000"

KNOWN_QUOTE_ASSETS = {
    NATIVE_QUOTE_ADDRESS: "BNB",
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": "WBNB",
    "0x55d398326f99059ff775485246999027b3197955": "USDT",
    "0xe9e7cea3dedca5984780bafc599bd69add087d56": "BUSD",
}


def normalize_quote(value: Any) -> str | None:
    """Return a lowercase 20-byte address, or None when the value is not one."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if len(raw) != 20:
            return None
        return "0x" + raw.hex()
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if len(text) != 40:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return "0x" + text


def classify_quote(value: Any) -> str:
    """Return the known quote symbol or ``unknown``.

    ``unknown`` means the asset is not in the local registry. It must not be
    treated as BNB; callers should keep the raw address and mark the record as
    unverified.
    """
    normalized = normalize_quote(value)
    if normalized is None:
        return "unknown"
    return KNOWN_QUOTE_ASSETS.get(normalized, "unknown")


def is_native_bnb(value: Any) -> bool:
    return normalize_quote(value) == NATIVE_QUOTE_ADDRESS


def is_known_non_native(value: Any) -> bool:
    normalized = normalize_quote(value)
    return normalized is not None and normalized != NATIVE_QUOTE_ADDRESS and normalized in KNOWN_QUOTE_ASSETS

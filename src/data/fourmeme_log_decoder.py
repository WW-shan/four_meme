"""Decode offline Four.meme logs using the two checked-in manager ABIs.

The caller selects the chain and emitting contract. This module only decodes
verified event layouts; it does not infer quote units, prices, or executions.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from eth_abi import decode as abi_decode, encode as abi_encode
from eth_abi.exceptions import DecodingError, EncodingError
from web3 import Web3


_ABI_DIRECTORY = Path(__file__).resolve().parents[2] / "config"
_TRADE_EVENTS = frozenset(("TokenPurchase", "TokenSale"))


def _load_event_abis() -> dict[str, dict]:
    events: dict[str, dict] = {}
    for filename in ("TokenManager2.lite.abi", "TokenManager.lite.abi"):
        abi = json.loads((_ABI_DIRECTORY / filename).read_text(encoding="utf-8"))
        for event in abi:
            if event.get("type") != "event":
                continue
            # Both local ABIs use single-topic, non-anonymous events. Fail
            # explicitly if a future ABI needs another decoding convention.
            if event.get("anonymous") or any(item["indexed"] for item in event["inputs"]):
                raise ValueError(f"Unsupported Four.meme event layout: {event['name']}")
            signature = event["name"] + "(" + ",".join(
                item["type"] for item in event["inputs"]
            ) + ")"
            topic = Web3.keccak(text=signature).hex().removeprefix("0x")
            if topic in events and events[topic] != event:
                raise ValueError(f"Conflicting Four.meme event ABI: {signature}")
            events[topic] = event
    return events


EVENT_ABIS_BY_TOPIC = MappingProxyType(_load_event_abis())
EVENT_NAME_BY_TOPIC = MappingProxyType({
    topic: event["name"] for topic, event in EVENT_ABIS_BY_TOPIC.items()
})
TRADE_TOPICS = MappingProxyType({
    topic: "purchase" if event["name"] == "TokenPurchase" else "sale"
    for topic, event in EVENT_ABIS_BY_TOPIC.items()
    if event["name"] in _TRADE_EVENTS
})
# TokenPurchase2/TokenSale2 only carry an `origin` field. They are not trade
# records and must never be routed to trade handlers.
AUXILIARY_TRADE_EVENTS = frozenset(("TokenPurchase2", "TokenSale2"))
TOKEN_CREATE_TOPIC = next(topic for topic, event in EVENT_ABIS_BY_TOPIC.items() if event["name"] == "TokenCreate")
TOPIC_CREATE = TOKEN_CREATE_TOPIC
TOPIC_STOP = next(topic for topic, event in EVENT_ABIS_BY_TOPIC.items() if event["name"] == "TradeStop")
LIQUIDITY_ADDED_TOPIC = next(
    topic for topic, event in EVENT_ABIS_BY_TOPIC.items() if event["name"] == "LiquidityAdded"
)


def _topic_hex(value: Any) -> str | None:
    """Normalize a topic value to a lowercase 64-character hex string."""
    try:
        raw = _log_bytes(value)
    except (TypeError, ValueError):
        return None
    return raw.hex() if len(raw) == 32 else None


def canonical_trade_name(event_name: str) -> str | None:
    """Return TokenPurchase/TokenSale for the two real trade ABIs, else None."""
    if event_name in _TRADE_EVENTS:
        return event_name
    return None


def is_known_topic(topic: Any) -> bool:
    normalized = _topic_hex(topic)
    return normalized is not None and normalized in EVENT_ABIS_BY_TOPIC


def _log_bytes(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        raw = value[2:] if value.startswith(("0x", "0X")) else value
        return bytes.fromhex(raw)
    raise TypeError("Log topics and data must be bytes or hexadecimal strings")


def decode_fourmeme_log(log: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Return a typed event and lossless ABI arguments, or None if unsupported.

    Legacy trades retain tokenAmount/etherAmount and gain amount/cost aliases.
    Liquidity and origin events retain their own names and are never trades.
    Integer fields remain in raw contract units; no price floor is applied.
    """
    if not isinstance(log, Mapping):
        return None
    topics = log.get("topics")
    if not isinstance(topics, (list, tuple)) or len(topics) != 1:
        return None
    try:
        topic = _log_bytes(topics[0])
        if len(topic) != 32:
            return None
        event = EVENT_ABIS_BY_TOPIC.get(topic.hex())
        if event is None:
            return None

        data = _log_bytes(log.get("data"))
        inputs = event["inputs"]
        types = tuple(item["type"] for item in inputs)
        static_words = all(kind in ("address", "uint256") for kind in types)
        if static_words and len(data) != 32 * len(types):
            return None
        values = abi_decode(types, data, strict=True)
        # Dynamic strings have offsets and padding. Re-encoding rejects
        # trailing data and noncanonical layouts that decode() can accept.
        if not static_words and abi_encode(types, values) != data:
            return None

        args = {}
        for item, value in zip(inputs, values):
            name = item["name"]
            if item["type"] == "address":
                value = value.lower() if name in ("token", "base", "quote") else Web3.to_checksum_address(value)
            args[name] = value
        if event["name"] in _TRADE_EVENTS and "tokenAmount" in args:
            args["amount"] = args["tokenAmount"]
            args["cost"] = args["etherAmount"]
        return event["name"], args
    except (DecodingError, EncodingError, TypeError, ValueError, OverflowError):
        return None

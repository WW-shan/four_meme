"""Live EVM verification: chain health, verified contracts, and real launch events."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Iterable, Mapping, Sequence

from eth_abi import decode as abi_decode
from eth_utils import keccak

from src.radar.liveverify.rpc import JsonRpcClient, RpcCallError, hex_int
from src.radar.liveverify.sourcify import SourcifyClient

ZERO_ADDRESS = "0x" + "00" * 20


def event_topic0(signature: str) -> str:
    """topic0 for an event signature, computed locally so nothing is hard-coded by memory."""
    return "0x" + keccak(text=signature).hex()


def canonical_type(item: Mapping[str, Any]) -> str:
    """Flatten ABI tuple components into the canonical type used in event signatures."""
    type_name = str(item.get("type") or "")
    if type_name.startswith("tuple"):
        components = item.get("components") or []
        inner = ",".join(canonical_type(component) for component in components)
        suffix = type_name[len("tuple"):]
        return f"({inner}){suffix}"
    return type_name


def event_signature(entry: Mapping[str, Any]) -> str | None:
    if entry.get("type") != "event" or not entry.get("name"):
        return None
    inputs = entry.get("inputs") or []
    return f"{entry['name']}({','.join(canonical_type(item) for item in inputs)})"


def decode_log(abi: Sequence[Mapping[str, Any]], log: Mapping[str, Any]) -> dict | None:
    topics = [str(topic) for topic in (log.get("topics") or [])]
    if not topics:
        return None
    data = str(log.get("data") or "0x").removeprefix("0x")
    raw = bytes.fromhex(data) if data else b""
    for entry in abi:
        signature = event_signature(entry)
        if not signature or event_topic0(signature) != topics[0]:
            continue
        inputs = list(entry.get("inputs") or [])
        indexed_values = [bytes.fromhex(topic.removeprefix("0x")) for topic in topics[1:]]
        non_indexed_types = [canonical_type(item) for item in inputs if not item.get("indexed")]
        try:
            values = list(abi_decode(non_indexed_types, raw)) if non_indexed_types else []
        except Exception:
            continue
        names = [item.get("name") or f"arg{index}" for index, item in enumerate(inputs)]
        decoded: dict[str, Any] = {"event": entry["name"], "signature": signature}
        index = 0
        for position, item in enumerate(inputs):
            if item.get("indexed"):
                raw_value = indexed_values[index]
                index += 1
                if item["type"] == "address":
                    value = "0x" + raw_value[-20:].hex()
                elif item["type"].startswith(("uint", "int")):
                    value = int.from_bytes(raw_value, "big")
                elif item["type"] == "bool":
                    value = bool(int.from_bytes(raw_value, "big"))
                else:
                    value = "0x" + raw_value.hex()
            else:
                value = values.pop(0) if values else None
            decoded[names[position]] = value
        return decoded
    return None


@dataclass
class ChainHealth:
    chain: str
    expected_chain_id: int | None
    ok: bool = False
    reported_chain_id: int | None = None
    latest_block: int | None = None
    latest_block_timestamp: int | None = None
    block_age_seconds: float | None = None
    endpoint: str | None = None
    log_span: dict | None = None
    error: str | None = None
    attempts: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chain": self.chain,
            "expected_chain_id": self.expected_chain_id,
            "ok": self.ok,
            "reported_chain_id": self.reported_chain_id,
            "chain_id_matches": (self.expected_chain_id is None or self.reported_chain_id == self.expected_chain_id),
            "latest_block": self.latest_block,
            "latest_block_timestamp": self.latest_block_timestamp,
            "block_age_seconds": self.block_age_seconds,
            "endpoint": self.endpoint,
            "log_span": self.log_span,
            "error": self.error,
        }


class EvmVerifier:
    def __init__(self, client: JsonRpcClient, sourcify: SourcifyClient | None = None):
        self.client = client
        self.sourcify = sourcify or SourcifyClient()
        self._block_timestamps: dict[int, int] = {}

    # ---- chain level -----------------------------------------------------------------
    def chain_health(self, chain: str, expected_chain_id: int | None) -> ChainHealth:
        health = ChainHealth(chain=chain, expected_chain_id=expected_chain_id)
        try:
            raw_chain_id = self.client.call("eth_chainId")
            health.reported_chain_id = hex_int(raw_chain_id)
            latest = hex_int(self.client.call("eth_blockNumber"))
            health.latest_block = latest
            block = self.client.call("eth_getBlockByNumber", [hex(latest), False]) or {}
            health.latest_block_timestamp = hex_int(block.get("timestamp"))
            if health.latest_block_timestamp:
                health.block_age_seconds = round(time.time() - health.latest_block_timestamp, 1)
            health.ok = expected_chain_id is None or health.reported_chain_id == expected_chain_id
            if not health.ok:
                health.error = f"chain_id mismatch: expected {expected_chain_id}, got {health.reported_chain_id}"
        except RpcCallError as exc:
            health.error = str(exc)
            health.attempts = list(self.client.attempts)
        health.endpoint = self.client.last_endpoint
        health.attempts = list(self.client.attempts)
        return health

    # ---- contract level --------------------------------------------------------------
    def code_size(self, address: str) -> int:
        code = self.client.call("eth_getCode", [address, "latest"])
        return len(str(code or "0x").removeprefix("0x")) // 2

    def block_timestamp(self, block_number: int) -> int | None:
        if block_number in self._block_timestamps:
            return self._block_timestamps[block_number]
        block = self.client.call("eth_getBlockByNumber", [hex(block_number), False]) or {}
        timestamp = hex_int(block.get("timestamp"))
        if timestamp:
            self._block_timestamps[block_number] = timestamp
        return timestamp or None

    RETRYABLE_LOG_ERRORS = ("exceeds max results", "response is too big", "invalid block range",
                            "exceeds limit", "limit exceeded", "range")

    def recent_logs(self, address: str, *, span: int, topic0s: Iterable[str] | None = None,
                    latest_block: int | None = None, min_span: int = 20) -> tuple[list[dict], int, list[dict]]:
        """Fetch address-filtered logs, shrinking the window when the RPC refuses it.

        Public RPCs cap either the block range or the number of returned results; the effective
        window is part of the evidence so a report never claims a wider window than was scanned.
        """
        latest = latest_block if latest_block is not None else hex_int(self.client.call("eth_blockNumber"))
        topics = list(topic0s or [])
        attempts: list[dict] = []
        current = max(int(span), min_span)
        while current >= min_span:
            params: dict[str, Any] = {"address": address, "fromBlock": hex(max(0, latest - current)),
                                      "toBlock": "latest"}
            if topics:
                params["topics"] = [topics]
            try:
                logs = self.client.call("eth_getLogs", [params]) or []
                attempts.append({"span": current, "ok": True, "logs": len(logs)})
                return logs, current, attempts
            except RpcCallError as exc:
                message = str(exc)
                attempts.append({"span": current, "ok": False, "error": message[:200]})
                retryable = any(token in message.lower() for token in self.RETRYABLE_LOG_ERRORS)
                if not retryable or current <= min_span:
                    raise
                current = max(min_span, current // 2)
        raise RpcCallError("eth_getLogs", attempts)

    def verify_contract(self, *, chain: str, chain_id: int, address: str, span: int,
                        expected_names: Iterable[str] = (), topic_signatures: Iterable[str] = (),
                        abi_override: list | None = None, latest_block: int | None = None,
                        sample_limit: int = 3) -> dict:
        """Verify a contract is live and is emitting the expected launch events right now."""
        result: dict[str, Any] = {
            "chain": chain,
            "address": address.lower(),
            "expected_names": list(expected_names),
            "checked_at": time.time(),
        }
        try:
            result["code_size_bytes"] = self.code_size(address)
        except RpcCallError as exc:
            result["ok"] = False
            result["error"] = f"eth_getCode failed: {exc}"
            result["attempts"] = list(self.client.attempts)
            return result
        if result["code_size_bytes"] == 0:
            result["ok"] = False
            result["error"] = "no contract code at address"
            return result

        sourcify = self.sourcify.lookup(chain_id, address)
        abi = abi_override or sourcify.abi
        result["sourcify"] = sourcify.to_dict()
        if expected_names and sourcify.name:
            result["name_matches"] = sourcify.name in set(expected_names)
        elif expected_names:
            result["name_matches"] = False

        topics = {signature: event_topic0(signature) for signature in topic_signatures}
        try:
            logs, effective_span, window_attempts = self.recent_logs(
                address, span=span, topic0s=topics.values() or None, latest_block=latest_block)
        except RpcCallError as exc:
            result["ok"] = False
            result["error"] = f"eth_getLogs failed: {exc}"
            result["attempts"] = list(self.client.attempts)
            return result

        by_topic: dict[str, int] = {}
        decoded_counts: dict[str, int] = {}
        for log in logs:
            topic0 = (log.get("topics") or ["<none>"])[0]
            by_topic[topic0] = by_topic.get(topic0, 0) + 1
            if abi and len(decoded_counts) < 100_000:
                decoded = decode_log(abi, log)
                if decoded:
                    name = str(decoded.get("event"))
                    decoded_counts[name] = decoded_counts.get(name, 0) + 1
        if decoded_counts:
            result["decoded_event_counts"] = dict(sorted(decoded_counts.items(), key=lambda item: -item[1]))
        result["live_window"] = {"span_blocks": effective_span, "requested_span": span,
                                 "logs": len(logs), "attempts": window_attempts}
        result["logs_by_topic0"] = by_topic
        result["expected_topic0"] = topics
        result["topic_hits"] = {signature: by_topic.get(topic, 0) for signature, topic in topics.items()}

        samples = []
        for log in logs[:sample_limit]:
            block_number = hex_int(log.get("blockNumber"))
            decoded = decode_log(abi, log) if abi else None
            samples.append({
                "block": block_number,
                "block_timestamp": self.block_timestamp(block_number) if block_number else None,
                "transaction_hash": log.get("transactionHash"),
                "address": log.get("address"),
                "topic0": (log.get("topics") or [None])[0],
                "decoded": decoded,
            })
        result["samples"] = samples
        notes: list[str] = []
        if topic_signatures:
            hit_any = any(count > 0 for count in result["topic_hits"].values())
            missing = [sig for sig, count in result["topic_hits"].items() if count == 0]
            result["ok"] = bool(logs) and hit_any
            if not logs:
                notes.append("窗口内没有任何事件；地址可能是休眠合约、错误地址，或窗口过小")
            elif missing:
                notes.append("以下事件在窗口内为 0（低频或未发生）：" + "；".join(missing))
        else:
            result["ok"] = bool(logs)
            if not logs:
                notes.append("窗口内没有任何事件")
        if result.get("expected_names") and result.get("name_matches") is False:
            notes.append(f"Sourcify 记录名为 {sourcify.name!r}，与期望 {result['expected_names']} 不一致（字节码匹配优先，名称仅作参考）")
        if notes:
            result["verification_notes"] = notes
        return result

    # ---- contract reads ---------------------------------------------------------------
    @staticmethod
    def signature_arg_types(signature: str) -> list[str]:
        """Parse the argument types straight out of e.g. ``getPool(address,address,uint24)``."""
        inside = signature[signature.find("(") + 1: signature.rfind(")")].strip()
        if not inside:
            return []
        types: list[str] = []
        depth = 0
        current = ""
        for char in inside:
            if char == "," and depth == 0:
                types.append(current.strip())
                current = ""
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            current += char
        types.append(current.strip())
        return [item for item in types if item]

    def call_function(self, to: str, signature: str, arg_types: Sequence[str] | None = None,
                      args: Sequence[Any] | None = None) -> str:
        from eth_abi import encode as abi_encode

        selector = keccak(text=signature)[:4]
        types = list(arg_types) if arg_types is not None else self.signature_arg_types(signature)
        values = list(args or [])
        if types and not values:
            raise ValueError(f"missing call args for {signature}")
        if not types:
            values = []
        payload = selector + (abi_encode(types, values) if types else b"")
        return self.client.call("eth_call", [{"to": to, "data": "0x" + payload.hex()}, "latest"])

    def call_uint(self, to: str, signature: str, arg_types: Sequence[str] | None = None,
                  args: Sequence[Any] | None = None) -> int:
        raw = str(self.call_function(to, signature, arg_types, args) or "0x").removeprefix("0x")
        return int(raw or "0", 16)

    def call_address(self, to: str, signature: str, arg_types: Sequence[str] | None = None,
                     args: Sequence[Any] | None = None) -> str:
        raw = str(self.call_function(to, signature, arg_types, args) or "0x").removeprefix("0x")
        return "0x" + raw[-40:] if len(raw) >= 40 else ZERO_ADDRESS

    # ---- discovery --------------------------------------------------------------------
    def discover_active_contracts(self, *, span: int, latest_block: int | None = None,
                                  chain_id: int | None = None, top_n: int = 25,
                                  min_logs: int = 5) -> dict:
        """Group recent unfiltered logs by emitting contract to find launchpad candidates."""
        latest = latest_block if latest_block is not None else hex_int(self.client.call("eth_blockNumber"))
        logs = self.client.call("eth_getLogs", [{"fromBlock": hex(max(0, latest - span)),
                                                 "toBlock": "latest"}]) or []
        grouped: dict[str, dict] = {}
        for log in logs:
            address = str(log.get("address") or "").lower()
            if not address:
                continue
            entry = grouped.setdefault(address, {"address": address, "logs": 0, "topics": {}})
            entry["logs"] += 1
            topic0 = (log.get("topics") or ["<none>"])[0]
            entry["topics"][topic0] = entry["topics"].get(topic0, 0) + 1
        ranked = sorted(grouped.values(), key=lambda item: -item["logs"])[:top_n]
        for entry in ranked:
            entry["topics"] = dict(sorted(entry["topics"].items(), key=lambda item: -item[1])[:5])
            if chain_id is not None:
                try:
                    entry["code_size_bytes"] = self.code_size(entry["address"])
                except RpcCallError as exc:
                    entry["code_size_bytes"] = None
                    entry["code_error"] = str(exc)
                sourcify = self.sourcify.lookup(chain_id, entry["address"])
                entry["sourcify"] = sourcify.to_dict()
        return {
            "chain_id": chain_id,
            "span_blocks": span,
            "from_block": latest - span,
            "to_block": latest,
            "total_logs": len(logs),
            "distinct_contracts": len(grouped),
            "candidates": [entry for entry in ranked if entry["logs"] >= min_logs],
        }

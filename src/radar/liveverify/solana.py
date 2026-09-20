"""Live Solana verification: program existence, real activity, and decoded launch instructions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import time
from typing import Any, Mapping, Sequence

import requests

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


def anchor_discriminator(name: str) -> bytes:
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


@dataclass
class SolanaCallError(Exception):
    method: str
    error: str

    def __str__(self) -> str:  # pragma: no cover - formatting helper
        return f"{self.method} failed: {self.error}"


class SolanaVerifier:
    def __init__(self, rpc_url: str, *, session=None, timeout: float = 30.0):
        self.rpc_url = rpc_url
        self.session = session or requests.Session()
        self.timeout = timeout
        self.last_error: str | None = None

    def call(self, method: str, params: list | None = None, *, max_retries: int = 2) -> Any:
        last_error = None
        for attempt in range(max_retries + 1):
            response = self.session.post(self.rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                                             "params": params or []}, timeout=self.timeout)
            body = response.json()
            if "result" in body:
                return body["result"]
            last_error = str(body.get("error"))
            if ("429" in last_error or "too many requests" in last_error.lower()) and attempt < max_retries:
                import time as _time
                _time.sleep(3 * (attempt + 1))
                continue
            break
        self.last_error = last_error
        raise SolanaCallError(method, self.last_error or "unknown error")

    def health(self) -> dict:
        started = time.time()
        result: dict[str, Any] = {"rpc": self.rpc_url}
        try:
            result["health"] = self.call("getHealth")
            result["version"] = (self.call("getVersion") or {}).get("solana-core")
            result["slot"] = self.call("getSlot")
            result["ok"] = True
        except Exception as exc:
            result["ok"] = False
            result["error"] = f"{type(exc).__name__}: {exc}"
        result["seconds"] = round(time.time() - started, 3)
        return result

    def program_account(self, program_id: str) -> dict:
        result: dict[str, Any] = {"program_id": program_id}
        try:
            account = self.call("getAccountInfo", [program_id, {"encoding": "base64"}])
        except SolanaCallError as exc:
            result["ok"] = False
            result["error"] = str(exc)
            return result
        value = (account or {}).get("value")
        if not value:
            result["ok"] = False
            result["error"] = "account not found"
            return result
        data = (value.get("data") or [""])[0]
        result.update({
            "ok": True,
            "owner": value.get("owner"),
            "executable": value.get("executable"),
            "lamports": value.get("lamports"),
            "data_len": len(data) * 3 // 4 if data else 0,
            "rent_epoch": value.get("rentEpoch"),
            "space": value.get("space"),
        })
        return result

    def recent_signatures(self, address: str, *, limit: int = 50) -> list[dict]:
        return self.call("getSignaturesForAddress", [address, {"limit": limit}]) or []

    def activity(self, address: str, *, limit: int = 50) -> dict:
        signatures = self.recent_signatures(address, limit=limit)
        times = [item.get("blockTime") for item in signatures if item.get("blockTime")]
        return {
            "address": address,
            "requested": limit,
            "returned": len(signatures),
            "with_block_time": len(times),
            "newest_block_time": max(times) if times else None,
            "oldest_block_time": min(times) if times else None,
            "age_seconds": round(time.time() - max(times), 1) if times else None,
            "recent_signature_samples": [item.get("signature") for item in signatures[:3]],
            "errors": [item.get("err") for item in signatures[:5]],
        }

    def find_instruction(self, *, program_id: str, discriminator: bytes, signatures: Sequence[Mapping],
                         max_transactions: int = 40, instruction_name: str | None = None) -> dict:
        """Scan recent transactions for a program instruction.

        Two independent signals are collected: the Anchor discriminator on the instruction data and
        the program's own ``Program log: Instruction: <Name>`` log lines. The log histogram is the
        fallback evidence when a create instruction simply is not in the sampled window.
        """
        import re

        checked = []
        name_counts: dict[str, int] = {}
        discriminator_counts: dict[str, int] = {}
        for item in signatures[:max_transactions]:
            signature = item.get("signature")
            if not signature:
                continue
            try:
                tx = self.call("getTransaction", [signature, {"encoding": "json", "maxSupportedTransactionVersion": 1}])
            except SolanaCallError:
                tx = self.call("getTransaction", [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}])
            if not tx:
                continue
            for message in (((tx.get("meta") or {}).get("logMessages") or []),):
                for line in message:
                    match = re.search(r"Instruction:\s*([A-Za-z0-9_]+)", str(line))
                    if match:
                        name_counts[match.group(1)] = name_counts.get(match.group(1), 0) + 1
            message = (tx.get("transaction") or {}).get("message") or {}
            account_keys = list(message.get("accountKeys") or [])
            keys = [key.get("pubkey") if isinstance(key, dict) else key for key in account_keys]
            for instruction in message.get("instructions") or []:
                if instruction.get("programId") != program_id:
                    continue
                data = instruction.get("data") or ""
                try:
                    import base58  # type: ignore
                    raw = base58.b58decode(data)
                except Exception:
                    raw = bytes.fromhex(data) if data and all(char in "0123456789abcdef" for char in data.lower()) else b""
                if len(raw) >= 8:
                    key = raw[:8].hex()
                    discriminator_counts[key] = discriminator_counts.get(key, 0) + 1
                name_match = instruction_name and instruction_name.lower() in {
                    name.lower() for name in name_counts
                }
                if raw.startswith(discriminator) or name_match:
                    accounts = [keys[index] if isinstance(index, int) and index < len(keys) else index
                                for index in (instruction.get("accounts") or [])]
                    return {
                        "found": True,
                        "matched_by": "discriminator" if raw.startswith(discriminator) else "log_name",
                        "signature": signature,
                        "slot": tx.get("slot"),
                        "block_time": tx.get("blockTime"),
                        "discriminator": discriminator.hex(),
                        "accounts": accounts[:10],
                        "data_hex": raw.hex()[:200],
                        "account_keys_count": len(keys),
                        "instruction_name_counts": dict(sorted(name_counts.items(), key=lambda kv: -kv[1])[:10]),
                    }
            checked.append(signature)
        return {
            "found": False,
            "checked_signatures": checked,
            "discriminator": discriminator.hex(),
            "instruction_name_counts": dict(sorted(name_counts.items(), key=lambda kv: -kv[1])[:10]),
            "discriminator_counts": dict(sorted(discriminator_counts.items(), key=lambda kv: -kv[1])[:10]),
        }

    def token_mint_facts(self, mint: str) -> dict:
        try:
            info = self.call("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        except SolanaCallError as exc:
            return {"mint": mint, "ok": False, "error": str(exc)}
        value = (info or {}).get("value")
        if not value:
            return {"mint": mint, "ok": False, "error": "account not found"}
        parsed = (((value.get("data") or {}).get("parsed") or {}).get("info") or {})
        return {
            "mint": mint,
            "ok": value.get("owner") in {TOKEN_PROGRAM, TOKEN_2022_PROGRAM},
            "owner_program": value.get("owner"),
            "decimals": parsed.get("decimals"),
            "supply": parsed.get("supply"),
            "mint_authority": parsed.get("mintAuthority"),
            "freeze_authority": parsed.get("freezeAuthority"),
            "is_initialized": parsed.get("isInitialized"),
        }

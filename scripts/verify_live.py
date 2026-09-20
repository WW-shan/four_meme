#!/usr/bin/env python3
"""Live verification CLI: chain RPC health + launchpad/DEX contracts against real chain data.

Examples:
    python scripts/verify_live.py --all --out-dir docs/research/20260920-live-chain-verification
    python scripts/verify_live.py --chain bsc --chain sol
    python scripts/verify_live.py --chain robinhood --discovery-only
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.radar.liveverify.abi_source import (  # noqa: E402
    is_proxy_only_abi,
    load_github_abi,
    load_local_abi,
    read_proxy_implementation,
)
from src.radar.liveverify.evm import EvmVerifier, event_topic0  # noqa: E402
from src.radar.liveverify.report import write_evidence, write_report  # noqa: E402
from src.radar.liveverify.rpc import JsonRpcClient, RpcCallError, probe_log_span  # noqa: E402
from src.radar.liveverify.solana import SolanaVerifier, anchor_discriminator  # noqa: E402
from src.radar.liveverify.sourcify import SourcifyClient  # noqa: E402

DEFAULT_CONFIG = PROJECT_ROOT / "config" / "live_targets.json"


def resolve_abi(target: dict, session=None) -> list | None:
    """Local ABI file, then GitHub contents API ABIs, merged in order."""
    abi: list = []
    if target.get("abi_path"):
        abi.extend(load_local_abi(PROJECT_ROOT / target["abi_path"]))
    for url in target.get("abi_urls") or []:
        try:
            abi.extend(load_github_abi(url, session=session))
        except Exception as exc:
            print(f"  abi fetch failed for {url}: {type(exc).__name__}: {exc}", flush=True)
    return abi or None


def apply_proxy_abi(verifier, entry: dict, target: dict, abi: list | None) -> list | None:
    """If the address is a proxy, read the EIP-1967 implementation and merge its ABI."""
    if not target.get("proxy"):
        return abi
    implementation = read_proxy_implementation(verifier.client, target["address"])
    if not implementation:
        return abi
    entry["proxy_implementation"] = implementation
    implementation_sourcify = verifier.sourcify.lookup(int(target.get("chain_id") or 0), implementation)
    entry["proxy_implementation_contract"] = implementation_sourcify.to_dict()
    if implementation_sourcify.abi:
        merged = list(abi or [])
        known = {json.dumps(item, sort_keys=True) for item in merged}
        for item in implementation_sourcify.abi:
            key = json.dumps(item, sort_keys=True)
            if key not in known:
                merged.append(item)
        return merged
    return abi


def verify_evm_chain(chain: str, spec: dict, *, span_override: int | None, discovery: bool,
                     top_n: int) -> dict:
    client = JsonRpcClient(spec["rpc"])
    verifier = EvmVerifier(client, SourcifyClient())
    expected_chain_id = spec.get("chain_id")
    health = verifier.chain_health(chain, expected_chain_id)
    result: dict = {"health": health.to_dict(), "targets": [], "attempts": list(client.attempts)}

    if discovery:
        probe_spans = [span for span in (5, 20, 100) if span <= max(spec.get("discovery_span") or 0, 20)]
        health.log_span = probe_log_span(client, health.latest_block or 0, spans=probe_spans)
        result["health"] = health.to_dict()

    span = span_override or int(spec.get("target_span") or 100)
    for target in spec.get("targets", []):
        topic_signatures = list(target.get("topics") or [])
        abi_override = resolve_abi(target, session=verifier.client.session)
        entry_sourcify = verifier.sourcify.lookup(int(expected_chain_id or 0), target["address"])
        if not abi_override and entry_sourcify.abi and not is_proxy_only_abi(entry_sourcify.abi):
            abi_override = entry_sourcify.abi
        if target.get("proxy") and (not abi_override or is_proxy_only_abi(abi_override)):
            implementation = read_proxy_implementation(verifier.client, target["address"])
            if implementation:
                implementation_abi = verifier.sourcify.lookup(int(expected_chain_id or 0), implementation).abi
                abi_override = list(implementation_abi or abi_override or [])
        entry = verifier.verify_contract(
            chain=chain,
            chain_id=int(expected_chain_id or 0),
            address=target["address"],
            span=span,
            expected_names=target.get("expected_names") or (),
            topic_signatures=topic_signatures,
            abi_override=abi_override,
            latest_block=health.latest_block,
        )
        entry.update({"target_id": target["id"], "kind": "target", "platform": target.get("platform"),
                      "target_kind": target.get("kind"), "source": target.get("source"),
                      "expected_topics": {sig: event_topic0(sig) for sig in topic_signatures}})
        entry["abi_source"] = ("local:" + target["abi_path"]) if target.get("abi_path") else \
            ("github:" + ",".join(target.get("abi_urls") or [])) if target.get("abi_urls") else \
            ("sourcify:" + str((entry.get("sourcify") or {}).get("name")))

        reads = []
        read_values: dict[str, str] = {}
        for read in target.get("reads") or []:
            item = {"signature": read["signature"], "args": read.get("args") or []}
            try:
                if read.get("type") == "address":
                    value = verifier.call_address(target["address"], read["signature"], read.get("arg_types"), read.get("args"))
                else:
                    value = verifier.call_uint(target["address"], read["signature"], read.get("arg_types"), read.get("args"))
                item["value"] = value
                if read.get("type") == "address":
                    read_values[read["signature"]] = value
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {exc}"
            reads.append(item)
        if reads:
            entry["reads"] = reads
            read_failures = [item for item in reads if item.get("error")]
            if read_failures:
                entry["ok"] = False
                entry.setdefault("verification_notes", []).append(
                    "标准合约调用失败：" + "；".join(f"{item['signature']} -> {item['error']}" for item in read_failures))
            elif target.get("verify_by") == "reads" and entry.get("code_size_bytes"):
                entry["ok"] = True
                entry.setdefault("verification_notes", []).append(
                    "该合约不靠事件判定：代码存在且官方 ABI 的读取调用全部成功（read-verified）")

        linked_entries = []
        for linked in target.get("linked_contracts") or []:
            derived = read_values.get(linked.get("from_read") or "")
            if not derived:
                linked_entries.append({"id": linked.get("id"), "ok": False,
                                       "error": f"derived address unavailable for read {linked.get('from_read')!r}"})
                continue
            linked_abi = resolve_abi(linked, session=verifier.client.session)
            linked_entry = verifier.verify_contract(
                chain=chain, chain_id=int(expected_chain_id or 0), address=derived, span=span,
                topic_signatures=list(linked.get("topics") or ()), abi_override=linked_abi,
                latest_block=health.latest_block,
            )
            linked_entry.update({"id": linked.get("id"), "derived_from": linked.get("from_read"),
                                 "derived_address": derived, "kind": "linked"})
            linked_entries.append(linked_entry)
        if linked_entries:
            entry["linked_contracts"] = linked_entries
            if target.get("kind") == "registry" and not entry.get("ok"):
                live_linked = [item for item in linked_entries if item.get("ok")]
                if live_linked and entry.get("code_size_bytes"):
                    entry["ok"] = True
                    entry.setdefault("verification_notes", []).append(
                        "注册表自身低频；通过读取到的下游合约实时事件完成核实："
                        + ", ".join(f"{item.get('id')}@{item.get('derived_address')}" for item in live_linked))
        result["targets"].append(entry)

    discovery_span = span_override if span_override and (spec.get("discovery_span") or 0) == 0 else (spec.get("discovery_span") or 0)
    if discovery and discovery_span:
        try:
            result["discovery"] = verifier.discover_active_contracts(
                span=int(discovery_span), latest_block=health.latest_block,
                chain_id=int(expected_chain_id or 0), top_n=top_n)
        except RpcCallError as exc:
            result["discovery"] = {"error": str(exc), "span_blocks": discovery_span}
    return result


def verify_solana_chain(chain: str, spec: dict) -> dict:
    verifier = SolanaVerifier(spec["rpc"][0])
    result: dict = {"health": verifier.health(), "targets": []}
    for target in spec.get("targets", []):
        entry: dict = {"target_id": target["id"], "kind": "target", "platform": target.get("platform"),
                       "address": target["address"], "source": target.get("source"),
                       "target_kind": target.get("kind")}
        program = verifier.program_account(target["address"])
        entry["program_account"] = program
        if program.get("ok"):
            activity = verifier.activity(target["address"], limit=50)
            entry["activity"] = activity
            instruction_name = target.get("instruction")
            if instruction_name:
                discriminator = anchor_discriminator(instruction_name)
                found = verifier.find_instruction(program_id=target["address"], discriminator=discriminator,
                                                  signatures=verifier.recent_signatures(target["address"], limit=60),
                                                  instruction_name=instruction_name)
                entry["instruction_probe"] = found
                if found.get("found") and found.get("accounts"):
                    mint = found["accounts"][0]
                    entry["sample_mint"] = verifier.token_mint_facts(mint)
            entry["ok"] = program.get("executable") is True and bool(entry.get("activity", {}).get("returned"))
        else:
            entry["ok"] = False
        result["targets"].append(entry)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--chain", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "docs" / "research" / "20260920-live-chain-verification"))
    parser.add_argument("--span", type=int, default=None, help="override target log window (blocks)")
    parser.add_argument("--no-discovery", action="store_true")
    parser.add_argument("--discovery-only", action="store_true")
    parser.add_argument("--top-n", type=int, default=25)
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    chains = config["chains"]
    selected = list(chains) if (args.all or not args.chain) else args.chain
    unknown = [chain for chain in selected if chain not in chains]
    if unknown:
        parser.error(f"unknown chain(s): {', '.join(unknown)}")

    payload: dict = {"generated_at": time.time(), "config_path": str(args.config), "chains": {},
                     "blocked_targets": []}
    for chain in selected:
        spec = chains[chain]
        print(f"[{chain}] verifying...", flush=True)
        try:
            if spec.get("family") == "solana":
                result = verify_solana_chain(chain, spec)
            else:
                result = verify_evm_chain(chain, spec, span_override=args.span,
                                          discovery=not args.no_discovery, top_n=args.top_n)
        except Exception as exc:  # keep going: a broken chain must not hide the others
            result = {"error": f"{type(exc).__name__}: {exc}"}
        if args.discovery_only:
            result.pop("targets", None)
        payload["chains"][chain] = result
        for blocked in spec.get("blocked", []):
            payload["blocked_targets"].append({"chain": chain, **blocked})
        health = result.get("health") or {}
        if health.get("ok") is False:
            print(f"[{chain}] chain health FAILED: {health.get('error')}", flush=True)
        else:
            print(f"[{chain}] block={health.get('latest_block')} endpoint={health.get('endpoint')}", flush=True)

    evidence_path = write_evidence(args.out_dir, payload)
    report_path = write_report(args.out_dir, payload)
    verified = sum(1 for item in payload["chains"].values() for target in item.get("targets", []) if target.get("ok"))
    failed = sum(1 for item in payload["chains"].values() for target in item.get("targets", [])
                 if not target.get("ok"))
    print(f"verified_live={verified} failed_or_code_only={failed}")
    print(f"evidence: {evidence_path}")
    print(f"report:   {report_path}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

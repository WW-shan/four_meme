import json
import unittest

from eth_abi import encode as abi_encode
from eth_utils import keccak

from src.radar.liveverify.abi_source import is_proxy_only_abi
from src.radar.liveverify.evm import (
    EvmVerifier,
    decode_log,
    event_signature,
    event_topic0,
)
from src.radar.liveverify.report import render_markdown
from src.radar.liveverify.rpc import JsonRpcClient, RpcCallError, probe_log_span
from src.radar.liveverify.solana import anchor_discriminator
from src.radar.liveverify.sourcify import SourcifyResult

PAIR_CREATED = "PairCreated(address,address,address,uint256)"
PAIR_CREATED_TOPIC = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class ScriptedSession:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)

    def get(self, url, params=None, timeout=None):  # pragma: no cover - not used in these tests
        raise AssertionError("unexpected GET")


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.attempts = []

    def call(self, method, params=None):
        self.calls.append((method, params))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def last_endpoint(self):
        return "fake://rpc"


class FakeSourcify:
    def __init__(self, abi=None, name="PancakeFactory"):
        self.abi = abi or []
        self.name = name

    def lookup(self, chain_id, address):
        return SourcifyResult(chain_id=chain_id, address=address, verified=True, name=self.name,
                              compiler="0.8.26", abi=self.abi)


def pair_created_log(token0, token1, pair, block=100):
    topics = [PAIR_CREATED_TOPIC, "0x" + "0" * 24 + token0[2:], "0x" + "0" * 24 + token1[2:]]
    data = "0x" + abi_encode(["address", "uint256"], [pair, 1]).hex()
    return {"address": "0x" + "aa" * 20, "topics": topics, "data": data,
            "blockNumber": hex(block), "transactionHash": "0x" + "bb" * 32}


class TopicAndDecodeTests(unittest.TestCase):
    def test_pair_created_topic_matches_known_constant(self):
        self.assertEqual(PAIR_CREATED_TOPIC, event_topic0(PAIR_CREATED))

    def test_decode_log_decodes_real_event_layout(self):
        abi = [{"type": "event", "name": "PairCreated", "anonymous": False, "inputs": [
            {"name": "token0", "type": "address", "indexed": True},
            {"name": "token1", "type": "address", "indexed": True},
            {"name": "pair", "type": "address", "indexed": False},
            {"name": "allPairsLength", "type": "uint256", "indexed": False},
        ]}]
        decoded = decode_log(abi, pair_created_log("0x" + "11" * 20, "0x" + "22" * 20, "0x" + "33" * 20))
        self.assertEqual("PairCreated", decoded["event"])
        self.assertEqual("0x" + "11" * 20, decoded["token0"])
        self.assertEqual("0x" + "33" * 20, decoded["pair"])
        self.assertEqual(1, decoded["allPairsLength"])

    def test_tuple_event_signature_is_canonical(self):
        entry = {"type": "event", "name": "Create", "inputs": [
            {"name": "asset", "type": "address", "indexed": True},
            {"name": "config", "type": "tuple", "components": [{"name": "fee", "type": "uint24"}]},
        ]}
        self.assertEqual("Create(address,(uint24))", event_signature(entry))

    def test_anchor_discriminator_for_pump_fun_create(self):
        self.assertEqual("181ec828051c0777", anchor_discriminator("create").hex())

    def test_proxy_only_abi_detection(self):
        proxy_abi = [{"type": "event", "name": "AdminChanged"}, {"type": "event", "name": "Upgraded"}]
        real_abi = proxy_abi + [{"type": "event", "name": "TokenCreated"}, {"type": "function", "name": "create"}]
        self.assertTrue(is_proxy_only_abi(proxy_abi))
        self.assertFalse(is_proxy_only_abi(real_abi))


class RpcClientTests(unittest.TestCase):
    def test_endpoint_fallback_records_every_attempt(self):
        session = ScriptedSession([
            {"error": {"code": -32000, "message": "boom"}},
            {"result": "0x38"},
        ])
        client = JsonRpcClient(["https://a", "https://b"], session=session)
        self.assertEqual("0x38", client.call("eth_chainId"))
        self.assertEqual(2, len(client.attempts))
        self.assertFalse(client.attempts[0]["ok"])
        self.assertTrue(client.attempts[1]["ok"])
        self.assertEqual("https://b", client.last_endpoint)

    def test_rate_limit_is_retried(self):
        session = ScriptedSession([
            {"error": {"code": 429, "message": "Too Many Requests"}},
            {"result": "0x1"},
        ])
        client = JsonRpcClient(["https://a"], session=session)
        self.assertEqual("0x1", client.call("eth_blockNumber", max_retries=1))
        self.assertTrue(any(attempt.get("rate_limited") for attempt in client.attempts))

    def test_all_endpoints_failing_raises_with_attempts(self):
        session = ScriptedSession([{"error": {"message": "nope"}}, {"error": {"message": "nope"}}])
        client = JsonRpcClient(["https://a", "https://b"], session=session)
        with self.assertRaises(RpcCallError) as ctx:
            client.call("eth_chainId")
        self.assertEqual(2, len(ctx.exception.attempts))

    def test_probe_log_span_reports_address_filter_requirement(self):
        session = ScriptedSession([{"error": {"code": -32701, "message": "Please specify an address in your request"}}])
        client = JsonRpcClient(["https://a"], session=session)
        result = probe_log_span(client, 1000, spans=[5])
        self.assertEqual("requires_address_filter", result["mode"])
        self.assertIsNone(result["accepted_span"])


class EvmVerifierTests(unittest.TestCase):
    def test_chain_health_flags_chain_id_mismatch(self):
        client = FakeClient(["0x1", hex(1000), {"timestamp": hex(1_700_000_000)}])
        health = EvmVerifier(client, FakeSourcify()).chain_health("bsc", 56)
        self.assertFalse(health.ok)
        self.assertIn("chain_id mismatch", health.error)

    def test_verify_contract_requires_live_events(self):
        abi = [{"type": "event", "name": "PairCreated", "inputs": [
            {"name": "token0", "type": "address", "indexed": True},
            {"name": "token1", "type": "address", "indexed": True},
            {"name": "pair", "type": "address", "indexed": False},
            {"name": "allPairsLength", "type": "uint256", "indexed": False},
        ]}]
        client = FakeClient([
            "0x" + "60" * 100,                    # eth_getCode
            [pair_created_log("0x" + "11" * 20, "0x" + "22" * 20, "0x" + "33" * 20)],  # logs
            {"timestamp": hex(1_700_000_000)},    # block timestamp for the sample
        ])
        verifier = EvmVerifier(client, FakeSourcify(abi=abi))
        result = verifier.verify_contract(chain="bsc", chain_id=56, address="0x" + "aa" * 20,
                                          span=100, topic_signatures=[PAIR_CREATED], latest_block=1000)
        self.assertTrue(result["ok"])
        self.assertEqual(1, result["topic_hits"][PAIR_CREATED])
        self.assertEqual("PairCreated", result["samples"][0]["decoded"]["event"])

    def test_verify_contract_shrinks_window_on_rpc_limit(self):
        client = FakeClient([
            "0x" + "60" * 100,
            RpcCallError("eth_getLogs", [{"error": "query exceeds max results 20000"}]),
            [],
        ])
        verifier = EvmVerifier(client, FakeSourcify())
        result = verifier.verify_contract(chain="bsc", chain_id=56, address="0x" + "aa" * 20, span=400,
                                          latest_block=1000)
        self.assertEqual(200, result["live_window"]["span_blocks"])
        self.assertEqual(400, result["live_window"]["requested_span"])
        self.assertFalse(result["ok"])
        self.assertIn("窗口内没有任何事件", result["verification_notes"][0])


class ReportTests(unittest.TestCase):
    def test_markdown_marks_live_targets_and_blocked_entries(self):
        payload = {
            "generated_at": 1_700_000_000,
            "chains": {"bsc": {
                "health": {"reported_chain_id": 56, "expected_chain_id": 56, "latest_block": 10,
                           "latest_block_timestamp": 1_700_000_000, "block_age_seconds": 3.0,
                           "endpoint": "https://rpc", "log_span": {"mode": "requires_address_filter"}},
                "targets": [{"target_id": "fourmeme", "address": "0x" + "aa" * 20, "ok": True,
                             "code_size_bytes": 10, "sourcify": {"name": "TokenManager"},
                             "live_window": {"logs": 5}, "topic_hits": {PAIR_CREATED: 5}}],
            }},
            "blocked_targets": [{"chain": "bsc", "platform": "flap", "blocked_reason": "no source",
                                 "next_step": "find source"}],
        }
        text = render_markdown(payload)
        self.assertIn("VERIFIED-LIVE", text)
        self.assertIn("fourmeme", text)
        self.assertIn("no source", text)


if __name__ == "__main__":
    unittest.main()

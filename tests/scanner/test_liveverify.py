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
            [],  # from-genesis history probe
        ])
        verifier = EvmVerifier(client, FakeSourcify())
        result = verifier.verify_contract(chain="bsc", chain_id=56, address="0x" + "aa" * 20, span=400,
                                          latest_block=1000)
        self.assertEqual(200, result["live_window"]["span_blocks"])
        self.assertEqual(400, result["live_window"]["requested_span"])
        self.assertFalse(result["ok"])
        self.assertIn("窗口内没有任何事件", result["verification_notes"][0])
        self.assertFalse(result["history_probe"]["events_exist"])


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


class TargetSpanTests(unittest.TestCase):
    """Per-target log windows, so quiet launchpads are not reported as empty by a narrow window."""

    def test_target_span_overrides_chain_default(self):
        from scripts.verify_live import resolve_target_span

        self.assertEqual(2000, resolve_target_span({}, 2000))
        self.assertEqual(20000000, resolve_target_span({"span": 20000000}, 2000))
        self.assertEqual(2000, resolve_target_span({"span": None}, 2000))

    def test_invalid_span_falls_back_to_default(self):
        from scripts.verify_live import resolve_target_span

        self.assertEqual(5000, resolve_target_span({"span": "not-a-number"}, 5000))
        self.assertEqual(1, resolve_target_span({"span": 0}, 0))

    def test_live_config_uses_declared_spans(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        config = json.loads((root / "config" / "live_targets.json").read_text(encoding="utf-8"))
        spans = {target["id"]: target.get("span") for spec in config["chains"].values()
                 for target in spec.get("targets", [])}
        self.assertEqual(20000000, spans["hoodfun_launchpad"])
        self.assertEqual(40000000, spans["stoxes_factory"])
        self.assertEqual(1000, spans["pew_instant_factory_1_stable"])


class LatestEventTests(unittest.TestCase):
    def test_latest_event_age_is_reported(self):
        import json
        from src.radar.liveverify.report import render_markdown

        payload = {
            "generated_at": 1000.0,
            "chains": {"robinhood": {"health": {"reported_chain_id": 4663, "expected_chain_id": 4663},
                                     "targets": [{
                                         "target_id": "demo", "address": "0xabc", "ok": True,
                                         "code_size_bytes": 100, "sourcify": {"name": "Demo"},
                                         "live_window": {"logs": 3}, "topic_hits": {},
                                         "latest_event": {"block": 5, "age_seconds": 900000.0},
                                     }]}},
            "blocked_targets": [],
        }
        text = render_markdown(payload)
        self.assertIn("最近事件", text)
        self.assertIn("5 / 10.4 天前", text)


class HistoricalStatusTests(unittest.TestCase):
    """A quiet launchpad with proven historical events is HISTORICAL, not CODE-ONLY."""

    def test_history_probe_refusal_counts_as_events_existing(self):
        from src.radar.liveverify.evm import EvmVerifier

        client = FakeClient([RpcCallError("eth_getLogs", [{"error": "logs matched by query exceeds limit"}])])
        probe = EvmVerifier(client, FakeSourcify()).history_probe("0x" + "aa" * 20, latest_block=100)
        self.assertFalse(probe["ok"])
        self.assertTrue(probe["events_exist"])

    def test_history_probe_reports_oldest_and_newest_block(self):
        from src.radar.liveverify.evm import EvmVerifier

        logs = [{"blockNumber": hex(50), "topics": [], "data": "0x"},
                {"blockNumber": hex(90), "topics": [], "data": "0x"}]
        probe = EvmVerifier(FakeClient([logs]), FakeSourcify()).history_probe("0x" + "aa" * 20, latest_block=100)
        self.assertTrue(probe["events_exist"])
        self.assertEqual(50, probe["first_block"])
        self.assertEqual(90, probe["last_block"])
        self.assertEqual(10, probe["blocks_since_last"])

    def test_markdown_labels_historical_rows(self):
        import json
        from src.radar.liveverify.report import render_markdown

        payload = {
            "generated_at": 1.0,
            "chains": {"robinhood": {"health": {}, "targets": [{
                "target_id": "quiet", "address": "0xabc", "ok": False, "code_size_bytes": 100,
                "sourcify": {}, "live_window": {"logs": 0}, "topic_hits": {},
                "history_probe": {"mode": "from_genesis", "ok": False, "events_exist": True},
            }]}},
            "blocked_targets": [],
        }
        text = render_markdown(payload)
        self.assertIn("HISTORICAL", text)


class LiveTargetConfigTests(unittest.TestCase):
    """The target list must stay self-documenting: every address has a source, every
    blocked platform explains why it is blocked and what would unblock it."""

    @classmethod
    def setUpClass(cls):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        cls.config = json.loads((root / "config" / "live_targets.json").read_text(encoding="utf-8"))

    def test_every_target_has_an_address_and_a_source(self):
        for chain, spec in self.config["chains"].items():
            for target in spec.get("targets", []):
                with self.subTest(chain=chain, target=target.get("id")):
                    self.assertTrue(target.get("address"), "target without address")
                    self.assertTrue(target.get("source"), "target without source")

    def test_every_blocked_entry_has_reason_and_next_step(self):
        for chain, spec in self.config["chains"].items():
            for blocked in spec.get("blocked", []):
                with self.subTest(chain=chain, platform=blocked.get("platform")):
                    self.assertTrue(blocked.get("blocked_reason"), "blocked without reason")
                    self.assertTrue(blocked.get("next_step"), "blocked without next step")

    def test_platforms_checked_and_confirmed_not_to_exist_are_recorded_as_ignored(self):
        robinhood = self.config["chains"]["robinhood"]["blocked"]
        reasons = {item["platform"]: item["blocked_reason"] for item in robinhood}
        for platform in ("motion", "dyorswap", "mintfast", "launchhood", "leavehood"):
            with self.subTest(platform=platform):
                self.assertIn("不存在", reasons[platform])
        self.assertIn("不是发射台", reasons["oro"])

    def test_round3_platforms_are_present_with_verified_sources(self):
        robinhood = {target["id"]: target for target in self.config["chains"]["robinhood"]["targets"]}
        for target_id in ("hyper_meme_factory_v4", "pmav_hook", "circus_launchpad", "lunch_launcher",
                          "memecoin_fun_factory", "potato_pad", "lemon_v3_factory"):
            with self.subTest(target=target_id):
                self.assertIn(target_id, robinhood)
                self.assertTrue(robinhood[target_id]["source"])

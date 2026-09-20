import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import urlopen

from config.attention_config import AttentionConfig
from src.attention.board import build_board
from src.attention.collector import Collector
from src.attention.demo import seed_demo
from src.attention.identity import address, mentions
from src.attention.providers import ProviderError, gmgn_hot_searches, normalize_x, request_json, x_recent
from src.attention.server import make_server
from src.attention.store import Store

SOL = "So11111111111111111111111111111111111111112"
EVM = "0x" + "aB" * 20
NOW = 100000.0


def post(pid="123", topic="one", created=NOW-60, **extra):
    return {"id": pid, "topic_id": topic, "topic_name": topic, "text": EVM,
            "created": created, "author_id": "42", "username": "alice", "kind": "original",
            "is_kol": True, "metrics": {"retweet_count": 12}, "url": "https://x.com/i/web/status/123", **extra}


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "board.sqlite")

    def token(self, chain="bsc", observed=NOW-100):
        p = {"chain": chain, "address": EVM.lower(), "symbol": "TEST", "visiting_count": 0}
        self.store.append("gmgn", "token", chain+":"+EVM.lower(), p, observed)


class IdentityTests(unittest.TestCase):
    def test_chain_specific_casing_and_validation(self):
        self.assertEqual(address("sol", SOL), SOL)
        self.assertEqual(address("bsc", EVM), EVM.lower())
        for chain, value in [("sol", "a"*32), ("sol", "0"*44), ("bsc", EVM+"a"), ("unknown", EVM)]:
            with self.assertRaises(ValueError):
                address(chain, value)

    def test_ambiguous_evm_does_not_choose_chain(self):
        known = [{"chain": c, "address": EVM.lower()} for c in ("bsc", "base")]
        match = mentions("CA " + EVM, known)[0]
        self.assertIsNone(match["chain"])
        self.assertEqual(match["status"], "unresolved_chain")
        self.assertEqual(mentions("TEST is going up", known), [])

    def test_solana_unknown_is_unverified(self):
        self.assertEqual(mentions(SOL, [])[0]["status"], "unverified_address")

    def test_repeated_casing_is_one_mention(self):
        known = [{"chain": "bsc", "address": EVM.lower()}]
        self.assertEqual(len(mentions(EVM + " / " + EVM.lower(), known)), 1)


class BoardTests(StoreCase):
    def test_overlapping_queries_and_repoll_do_not_inflate_mentions(self):
        self.token()
        self.store.append("x", "post", "123", post(), NOW-50)
        self.store.append("x", "post", "123", post(topic="two"), NOW-40)
        self.store.append("x", "post", "123", post(topic="two", metrics={"retweet_count": 20}), NOW-30)
        board = build_board(self.store, NOW)
        social = board["tokens"][0]["social"]
        self.assertEqual(social["windows"]["1h"]["mentions"], 1)
        self.assertEqual(social["windows"]["1h"]["reported_reposts_of_originals"], 20)
        self.assertEqual(len(board["topics"]), 2)
        self.assertEqual(len(board["kol_events"]), 1)
        self.assertFalse(board["tokens"][0]["trading_eligible"])

    def test_asof_does_not_see_future_observation_or_mapping(self):
        self.store.append("x", "post", "123", post(), NOW-50)
        self.token(observed=NOW+10)
        self.store.append("x", "post", "124", post(pid="124", created=NOW-40), NOW+20)
        earlier = build_board(self.store, NOW)
        self.assertEqual(earlier["tokens"], [])
        self.assertEqual(earlier["topics"][0]["windows"]["1h"]["mentions"], 1)
        self.assertEqual(earlier["kol_events"][0]["contracts"][0]["status"], "unresolved_chain")

    def test_missing_social_is_not_zero_and_provider_can_have_zero(self):
        self.token()
        token = build_board(self.store, NOW)["tokens"][0]
        self.assertIsNone(token["social"])
        self.assertEqual(token["platform_visits_1h"], 0)
        self.assertIsNone(token["liquidity_usd"])

    def test_ambiguous_contract_remains_topic_only(self):
        self.token("bsc")
        self.token("base")
        self.store.append("x", "post", "123", post(), NOW-50)
        self.assertTrue(all(t["social"] is None for t in build_board(self.store, NOW)["tokens"]))

    def test_baseline_requires_continuous_successful_history(self):
        self.store.append("x", "post", "123", post(), NOW-50)
        for stamp in range(int(NOW-1860), int(NOW+1), 60):
            self.store.health("x:one", "ok", now=stamp)
        self.assertTrue(build_board(self.store, NOW)["topics"][0]["baseline_ready"])
        self.store.health("x:one", "error", now=NOW-100)
        self.store.health("x:one", "ok", now=NOW)
        self.assertFalse(build_board(self.store, NOW)["topics"][0]["baseline_ready"])

    def test_health_is_historical_and_stale(self):
        self.store.health("gmgn", "ok", now=NOW-10)
        self.store.health("gmgn", "error", now=NOW+10)
        self.assertEqual(build_board(self.store, NOW)["sources"][0]["status"], "ok")
        self.assertEqual(build_board(self.store, NOW+300)["sources"][0]["status"], "stale")

    def test_windows_and_repost_metrics_separate(self):
        self.store.append("x", "post", "123", post(created=NOW-600, kind="repost"), NOW-590)
        topic = build_board(self.store, NOW)["topics"][0]
        self.assertEqual(topic["windows"]["5m"]["mentions"], 0)
        self.assertEqual(topic["windows"]["1h"]["repost"], 1)
        self.assertEqual(topic["windows"]["1h"]["reported_reposts_of_originals"], 0)

    def test_budget_is_persistent_and_day_specific(self):
        self.assertTrue(self.store.reserve_x_request("2026-09-16", 1))
        self.assertFalse(Store(self.store.path).reserve_x_request("2026-09-16", 1))
        self.assertTrue(self.store.reserve_x_request("2026-09-17", 1))

    def test_outage_remains_stale_beyond_a_day(self):
        self.store.health("gmgn", "ok", now=NOW-90000)
        board = build_board(self.store, NOW)
        self.assertEqual(board["sources"][0]["status"], "stale")

    def test_empty_topic_zero_requires_successful_query(self):
        scope = {"topics": [{"id": "one", "name": "One"}], "chains": ["bsc"], "stale_seconds": 180}
        self.store.append("attention", "config", "scope", scope, NOW-100)
        board = build_board(self.store, NOW)
        self.assertIsNone(board["topics"][0]["windows"]["1h"]["mentions"])
        self.store.health("x:one", "ok", now=NOW-20)
        board = build_board(self.store, NOW)
        self.assertEqual(board["topics"][0]["windows"]["1h"]["mentions"], 0)
        self.assertFalse(board["topics"][0]["window_complete"]["1h"])

    def test_historical_config_and_latest_snapshot_are_causal(self):
        scope = {"topics": [], "chains": ["bsc"], "stale_seconds": 180}
        self.store.append("attention", "config", "scope", scope, NOW-400)
        self.store.append("attention", "config", "scope", {**scope, "stale_seconds": 999}, NOW+10)
        self.token(observed=NOW-300)
        self.token(observed=NOW+10)
        board = build_board(self.store, NOW)
        self.assertTrue(board["tokens"][0]["stale"])
        self.assertEqual(board["tokens"][0]["observed"], NOW-300)

    def test_health_backfill_cannot_inherit_future_success(self):
        self.store.health("gmgn", "ok", now=NOW+10)
        self.store.health("gmgn", "error", now=NOW-10)
        self.assertIsNone(build_board(self.store, NOW)["sources"][0]["succeeded"])
        self.assertEqual(self.store.health_rows()[0]["status"], "ok")

    def test_atomic_batch_rejects_nonfinite_without_partial_write(self):
        with self.assertRaises(ValueError):
            self.store.append_batch([("x", "post", "1", {}), ("x", "post", "2", {"bad": float("nan")})], NOW)
        self.assertEqual(self.store.rows(until=NOW), [])

    def test_unknown_engagement_is_not_zero(self):
        self.store.append("x", "post", "123", post(metrics={}), NOW-50)
        metric = build_board(self.store, NOW)["topics"][0]["windows"]["1h"]
        self.assertIsNone(metric["reported_reposts_of_originals"])

    def test_same_post_mixed_case_contract_does_not_double_count(self):
        self.token()
        self.store.append("x", "post", "123", post(text=EVM + " " + EVM.lower()), NOW-50)
        self.assertEqual(build_board(self.store, NOW)["tokens"][0]["social"]["windows"]["1h"]["mentions"], 1)


class CollectorTests(StoreCase):
    def config(self, **kwargs):
        return AttentionConfig(topics=[{"id": "one", "name": "One", "query": "meme"}], **kwargs)

    def test_unconfigured_is_explicit(self):
        Collector(self.store, self.config()).tick()
        self.assertEqual({r["status"] for r in self.store.health_rows()}, {"unconfigured"})

    @patch("src.attention.collector.x_recent")
    def test_restart_finishes_pagination_before_advancing_since(self, recent):
        recent.return_value = {"data": [], "meta": {"newest_id": "500", "next_token": "page2"}}
        first = Collector(self.store, self.config(x_token="private"))
        first.tick()
        self.assertEqual(recent.call_count, 3)
        self.assertEqual(self.store.health_rows()[-1]["status"], "catching_up")
        recent.reset_mock()
        recent.return_value = {"data": [], "meta": {}}
        restarted = Collector(Store(self.store.path), self.config(x_token="private"))
        restarted.tick()
        args = recent.call_args.args
        self.assertIsNone(args[2])
        self.assertEqual(args[3], "page2")
        restarted.tick()
        self.assertEqual(recent.call_args.args[2], "500")
        self.assertIsNone(recent.call_args.args[3])

    @patch("src.attention.collector.x_recent")
    def test_budget_prevents_network_request(self, recent):
        Collector(self.store, self.config(x_token="private", x_daily_requests=0)).tick()
        recent.assert_not_called()
        self.assertEqual(self.store.health_rows()[-1]["status"], "budget_exhausted")

    @patch("src.attention.collector.gmgn_hot_searches", side_effect=ProviderError("HTTP 429", 1e12))
    def test_retry_deadline_survives_restart(self, provider):
        Collector(self.store, self.config(gmgn_key="private")).tick()
        Collector(Store(self.store.path), self.config(gmgn_key="private")).tick()
        self.assertEqual(provider.call_count, 1)

    def test_live_rejects_demo_database(self):
        self.store.set("mode", "demo")
        with self.assertRaises(ValueError):
            Collector(self.store, self.config())

    def test_query_redefinition_requires_a_new_topic_id(self):
        Collector(self.store, self.config())
        changed = self.config()
        changed.topics[0]["query"] = "different"
        with self.assertRaisesRegex(ValueError, "use a new id"):
            Collector(self.store, changed)

    @patch("src.attention.collector.x_recent")
    def test_empty_queries_advance_time_watermark(self, recent):
        recent.return_value = {"meta": {}}
        collector = Collector(self.store, self.config(x_token="private"))
        collector.tick()
        end = recent.call_args.kwargs["end_time"]
        collector.tick()
        self.assertEqual(recent.call_args.args[4], end)

    @patch("src.attention.collector.x_recent")
    def test_rate_limit_is_shared_by_topics(self, recent):
        recent.side_effect = ProviderError("HTTP 429", 1e12, 429)
        config = self.config(x_token="private")
        config.topics.append({"id": "two", "name": "Two", "query": "second"})
        Collector(self.store, config).tick()
        self.assertEqual(recent.call_count, 1)

    @patch("src.attention.collector.gmgn_hot_searches")
    def test_gmgn_cycle_publishes_usable_snapshots_and_raw(self, provider):
        provider.return_value = ([{"chain": "sol", "address": SOL, "visiting_count": 5}], {"code": 0})
        Collector(self.store, self.config(gmgn_key="private")).tick()
        board = build_board(self.store)
        self.assertEqual(board["tokens"][0]["platform_visits_1h"], 5)
        self.assertEqual(len(self.store.rows(kinds=("raw",))), 1)


class ProviderTests(unittest.TestCase):
    def test_gmgn_multi_chain_contract(self):
        session = Mock()
        session.request.return_value.status_code = 200
        session.request.return_value.json.return_value = {"code": 0, "data": [
            {"chain": "sol", "interval": "1h", "tokens": [{"address": SOL}]},
            {"chain": "bsc", "interval": "1h", "tokens": [{"address": EVM}]}]}
        tokens, raw = gmgn_hot_searches("secret", ("sol", "bsc"), session)
        self.assertEqual(tokens[0]["address"], SOL)
        self.assertEqual(tokens[1]["address"], EVM.lower())
        self.assertNotIn("secret", json.dumps(raw))
        with self.assertRaises(ProviderError):
            gmgn_hot_searches("secret", ("sol", "base"), session)

    def test_errors_never_include_response_secrets(self):
        session = Mock()
        response = session.request.return_value
        response.status_code = 403
        response.text = "secret-key"
        with self.assertRaisesRegex(ProviderError, "^HTTP 403$"):
            request_json(session, "GET", "https://example.com")

    def test_x_type_and_curated_kol(self):
        payload = {"data": [{"id": "123", "author_id": "42", "created_at": "2026-09-16T00:00:00Z",
                             "referenced_tweets": [{"type": "quoted", "id": "99"}]}],
                   "includes": {"users": [{"id": "42", "username": "Alice"}]}}
        result = normalize_x(payload, {"id": "one", "name": "One"}, ("alice",))[0]
        self.assertTrue(result["is_kol"])
        self.assertEqual(result["kind"], "quote")

    def test_gmgn_invalid_record_fails_cleanly(self):
        session = Mock()
        session.request.return_value.status_code = 200
        session.request.return_value.json.return_value = {"code": 0, "data": [
            {"chain": "sol", "interval": "1h", "tokens": [None]}]}
        with self.assertRaises(ProviderError):
            gmgn_hot_searches("private", ("sol",), session)

    def test_x_rejects_malformed_nested_fields(self):
        topic = {"id": "one", "name": "One"}
        for payload in [{"includes": None}, {"data": [None]}, {"data": [{"referenced_tweets": None}]}]:
            with self.assertRaises(ProviderError):
                normalize_x(payload, topic, ())

    def test_x_request_pins_bounds_and_rejects_partial_response(self):
        session = Mock()
        response = session.request.return_value
        response.status_code = 200
        response.json.return_value = {"meta": {"result_count": 0}}
        x_recent("secret", "meme", since_id="123", next_token="page2", start_time=NOW-100,
                 end_time=NOW, session=session)
        params = session.request.call_args.kwargs["params"]
        self.assertNotIn("start_time", params)
        self.assertIn("end_time", params)
        self.assertEqual(params["next_token"], "page2")
        for payload in [{"meta": []}, {"meta": {}, "errors": [{"detail": "partial"}]},
                        {"meta": {"result_count": 1}, "data": []}]:
            response.json.return_value = payload
            with self.assertRaises(ProviderError):
                x_recent("secret", "meme", session=session)


class ApiTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.store, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_api_validation_and_cursor(self):
        self.store.append("test", "test", "a", {"ok": True})
        with urlopen(self.url + "/api/v1/events?after=0&limit=1") as response:
            payload = json.load(response)
        self.assertEqual(payload["next_cursor"], 1)
        self.assertFalse(payload["trading_enabled"])
        for query in ["/api/v1/board?as_of=nan", "/api/v1/board?as_of=1e100", "/api/v1/board?chain=foo", "/api/v1/events?limit=9999"]:
            with self.assertRaises(HTTPError) as error:
                urlopen(self.url + query)
            self.assertEqual(error.exception.code, 400)


class ConfigTests(unittest.TestCase):
    def test_env_template_and_no_credential_repr(self):
        template = Path(".env.example").read_text()
        self.assertIn("GMGN_API_KEY=", template)
        self.assertIn("X_BEARER_TOKEN=", template)
        self.assertNotIn("private-secret", repr(AttentionConfig(gmgn_key="private-secret")))

    def test_example_valid(self):
        config = AttentionConfig.load("config/attention_sources.example.json")
        self.assertEqual(len(config.chains), 4)
        self.assertEqual(config.topics, [])

    def test_rejects_invalid_config_shapes_and_values(self):
        for value in [[], {"topics": {}}, {"poll_seconds": True}, {"typo": 1},
                      {"topics": [None]}, {"chains": ["bsc", "bsc"]}, {"kol_accounts": ["bad/handle"]}]:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder)/"sources.json"
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    AttentionConfig.load(path)

    def test_demo_is_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            store = seed_demo(Path(folder)/"demo.sqlite")
            self.assertEqual(build_board(store)["mode"], "demo")
            with self.assertRaises(ValueError):
                seed_demo(store.path)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/attention"


class SavedSampleTests(unittest.TestCase):
    def session_with(self, name):
        session = Mock()
        session.request.return_value.status_code = 200
        session.request.return_value.json.return_value = json.loads(
            (FIXTURES / name).read_text(encoding="utf-8"))
        return session

    def test_gmgn_saved_sample_decodes_multichain_envelope(self):
        tokens, raw = gmgn_hot_searches(
            "private", ("sol", "bsc"), self.session_with("gmgn_hot_searches_sample.json"))
        self.assertEqual([token["chain"] for token in tokens], ["sol", "bsc"])
        self.assertEqual(tokens[0]["address"], SOL)
        self.assertEqual(tokens[0]["visiting_count"], 662)
        self.assertEqual(tokens[1]["visiting_count"], 0)
        self.assertEqual(raw["data"][1]["tokens"][0]["launchpad"], "fourmeme")

    def test_x_saved_sample_decodes_post_and_author(self):
        session = self.session_with("x_recent_sample.json")
        payload = x_recent("private", "sample", since_id="1", start_time=NOW - 100,
                           end_time=NOW, session=session)
        posts = normalize_x(payload, {"id": "sample", "name": "Sample"}, ("samplewatcher",))
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["kind"], "quote")
        self.assertTrue(posts[0]["is_kol"])
        self.assertEqual(posts[0]["metrics"]["retweet_count"], 12)
        self.assertEqual(posts[0]["author_followers"], 15000)
        self.assertEqual(posts[0]["topic_id"], "sample")


if __name__ == "__main__":
    unittest.main()

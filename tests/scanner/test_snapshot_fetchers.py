import unittest

from src.safety.fetchers import FetchResult, SourceCache, SnapshotFetcher
from src.safety.snapshot import build_snapshot

TOKEN = "0x" + "11" * 20


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        for key, response in self.responses.items():
            if key in url:
                return response
        return FakeResponse({}, 404)


class CacheTests(unittest.TestCase):
    def test_cache_expires(self):
        now = [0.0]
        cache = SourceCache(ttl_seconds=10, clock=lambda: now[0])
        cache.set("x", "k", FetchResult("x", True, payload={"a": 1}))
        self.assertIsNotNone(cache.get("x", "k"))
        now[0] = 11.0
        self.assertIsNone(cache.get("x", "k"))


class FetcherTests(unittest.TestCase):
    def test_fetch_all_uses_injectable_session_and_cache(self):
        session = FakeSession({
            "token_security": FakeResponse({"result": {}}),
            "IsHoneypot": FakeResponse({"honeypotResult": {"isHoneypot": False}}),
            "token-pairs": FakeResponse({"pairs": []}),
        })
        fetcher = SnapshotFetcher(session=session, min_intervals={"goplus": 0, "honeypot": 0, "dexscreener": 0})
        first = fetcher.goplus_token_security(TOKEN)
        self.assertTrue(first.ok)
        second = fetcher.goplus_token_security(TOKEN)
        self.assertTrue(second.ok)
        self.assertEqual(1, len(session.calls))
        self.assertFalse(fetcher.gmgn_token_info(TOKEN).ok)

    def test_http_error_is_data(self):
        session = FakeSession({"token_security": FakeResponse({}, 429)})
        fetcher = SnapshotFetcher(session=session, min_intervals={"goplus": 0})
        result = fetcher.goplus_token_security(TOKEN)
        self.assertFalse(result.ok)
        self.assertIn("429", result.error)


class SnapshotMappingTests(unittest.TestCase):
    def test_maps_goplus_honeypot_and_dexscreener(self):
        fetched = {
            "goplus": FetchResult("goplus", True, payload={"result": {TOKEN: {
                "is_honeypot": "0", "buy_tax": "0.01", "sell_tax": "0.02",
                "is_mintable": "0", "can_take_back_ownership": "0",
                "is_blacklisted": "0", "transfer_pausable": "0",
                "holders": [{"percent": "0.08"}, {"percent": "0.05"}],
            }}}),
            "honeypot": FetchResult("honeypot", True, payload={
                "honeypotResult": {"isHoneypot": False},
                "simulationResult": {"buyTax": 0.01, "sellTax": 0.02},
            }),
            "dexscreener": FetchResult("dexscreener", True, payload={"pairs": [
                {"liquidity": {"usd": 25000}, "marketCap": 45000, "info": {"socials": [{"url": "https://x.com/a"}]}},
            ]}),
            "gmgn": FetchResult("gmgn", False, error="gmgn_api_key_missing"),
        }
        snapshot = build_snapshot(TOKEN, fetched, onchain={"dev_holding_pct": 0.4, "trades_recent": 30})
        self.assertEqual(45000.0, snapshot["mcap_usd"])
        self.assertEqual(25000.0, snapshot["liquidity_usd"])
        self.assertTrue(snapshot["honeypot_sim"])
        self.assertEqual("error", snapshot["sources"]["gmgn"])
        self.assertEqual(0.4, snapshot["dev_holding_pct"])

    def test_missing_fields_stay_missing(self):
        snapshot = build_snapshot(TOKEN, {})
        self.assertNotIn("liquidity_usd", snapshot)
        self.assertEqual({}, snapshot["sources"])


class DexScreenerListPayloadTests(unittest.TestCase):
    def test_token_pairs_list_payload_maps_price_liquidity_and_mcap(self):
        fetched = {"dexscreener": FetchResult("dexscreener", True, payload=[
            {"liquidity": {"usd": 6915502.46}, "marketCap": 973503451.0, "priceUsd": "1.23"},
        ])}
        snapshot = build_snapshot(TOKEN, fetched)
        self.assertEqual(6915502.46, snapshot["liquidity_usd"])
        self.assertEqual(973503451.0, snapshot["mcap_usd"])
        self.assertEqual(1.23, snapshot["price_usd"])


if __name__ == "__main__":
    unittest.main()


class ChainRoutingTests(unittest.TestCase):
    """Safety sources are keyed by chain; a non-BSC token must never be scored with BSC data."""

    def test_goplus_puts_the_chain_in_the_path(self):
        session = FakeSession({"token_security": FakeResponse({"result": {}})})
        fetcher = SnapshotFetcher(session=session, min_intervals={"goplus": 0})
        fetcher.goplus_token_security(TOKEN, chain_id=4663)
        url, params = session.calls[0]
        self.assertTrue(url.endswith("/token_security/4663"), url)
        self.assertNotIn("chain_id", params)

    def test_dexscreener_uses_the_chain_slug(self):
        session = FakeSession({"token-pairs": FakeResponse({"pairs": []})})
        fetcher = SnapshotFetcher(session=session, min_intervals={"dexscreener": 0})
        fetcher.dexscreener_pairs(TOKEN, chain_id=8453)
        url, _ = session.calls[0]
        self.assertIn("/token-pairs/v1/base/", url)

    def test_unsupported_chain_fails_closed_without_a_request(self):
        session = FakeSession({})
        fetcher = SnapshotFetcher(session=session, min_intervals={"dexscreener": 0})
        result = fetcher.dexscreener_pairs(TOKEN, chain_id=4663)
        self.assertFalse(result.ok)
        self.assertEqual("unsupported_chain:4663", result.error)
        self.assertEqual([], session.calls)

    def test_fetch_all_forwards_the_chain(self):
        session = FakeSession({
            "token_security": FakeResponse({"result": {}}),
            "IsHoneypot": FakeResponse({"honeypotResult": {"isHoneypot": False}}),
        })
        fetcher = SnapshotFetcher(session=session, min_intervals={"goplus": 0, "honeypot": 0, "dexscreener": 0})
        results = fetcher.fetch_all(TOKEN, chain_id=4663, chain="robinhood")
        self.assertTrue(results["goplus"].ok)
        self.assertEqual("unsupported_chain:4663", results["dexscreener"].error)
        self.assertIn("/token_security/4663", session.calls[0][0])


class UnknownChainFailsClosedTests(unittest.TestCase):
    """An unresolved chain must never be silently treated as BSC."""

    def _fetcher(self):
        session = FakeSession({
            "token_security": FakeResponse({"result": {}}),
            "IsHoneypot": FakeResponse({"honeypotResult": {"isHoneypot": False}}),
            "token-pairs": FakeResponse({"pairs": []}),
        })
        return SnapshotFetcher(session=session, min_intervals={"goplus": 0, "honeypot": 0, "dexscreener": 0}), session

    def test_none_chain_id_makes_every_source_fail_without_a_request(self):
        fetcher, session = self._fetcher()
        results = fetcher.fetch_all(TOKEN, chain_id=None, chain="hyperevm")
        self.assertEqual([], session.calls)
        for source in ("goplus", "honeypot", "dexscreener"):
            self.assertFalse(results[source].ok, source)
            self.assertEqual("unsupported_chain:None", results[source].error)

    def test_zero_and_garbage_chain_ids_are_rejected(self):
        for bad in (0, -1, "", "abc", True):
            fetcher, session = self._fetcher()
            result = fetcher.goplus_token_security(TOKEN, chain_id=bad)
            self.assertFalse(result.ok, bad)
            self.assertEqual([], session.calls)

    def test_positive_chain_id_is_still_forwarded(self):
        fetcher, session = self._fetcher()
        fetcher.goplus_token_security(TOKEN, chain_id="4663")
        self.assertTrue(session.calls[0][0].endswith("/token_security/4663"))

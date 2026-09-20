import unittest

from src.radar.adapters.gmgn import GmgnDiscoveryAdapter


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, params=None, headers=None, json=None, timeout=None):
        self.calls.append((url, params, headers, json))
        return _Response(self.payload)


class GmgnAdapterTests(unittest.IsolatedAsyncioTestCase):
    def payload(self, chain="robinhood"):
        return {"code": 0, "data": [{"chain": chain, "interval": "1h", "tokens": [
            {"address": "0x" + "11" * 20, "launchpad_platform": "pons", "visiting_count": 120,
             "rank": 1, "smart_degen_count": 5, "renowned_count": 2, "creation_timestamp": 100},
        ]}]}

    async def test_discovers_all_supported_chains(self):
        for chain in ("sol", "bsc", "base", "eth", "arbitrum", "robinhood", "arc", "stable", "hyperevm"):
            session = _Session(self.payload(chain))
            adapter = GmgnDiscoveryAdapter(chain, "key", session=session, enabled=True)
            events = await adapter.discover()
            self.assertEqual(1, len(events), chain)
            self.assertEqual(chain, events[0].chain)
            self.assertEqual("pons", events[0].platform)

    async def test_without_key_returns_nothing(self):
        adapter = GmgnDiscoveryAdapter("robinhood", "", session=_Session(self.payload()))
        self.assertEqual([], await adapter.discover())

    def test_rejects_unsupported_chain(self):
        with self.assertRaises(ValueError):
            GmgnDiscoveryAdapter("monad", "key")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from src.radar.collector import RadarCollector
from src.radar.events import launch_from_event
from src.radar.store import ScannerStore

TOKEN = "0x" + "11" * 20
CREATOR = "0x" + "22" * 20
USDT = "0x55d398326f99059ff775485246999027b3197955"


class RadarEventTests(unittest.TestCase):
    def test_token_create_builds_launch(self):
        launch = launch_from_event("TokenCreate", {
            "args": {"token": TOKEN, "creator": CREATOR},
            "timestamp": 1000, "received_at": 1001, "blockNumber": 5,
            "logIndex": 2, "transactionHash": "0x" + "ab" * 32,
        })
        self.assertEqual(TOKEN, launch.token)
        self.assertEqual("unknown", launch.quote_symbol)
        self.assertEqual(1001.0, launch.received_at)

    def test_liquidity_added_records_quote(self):
        launch = launch_from_event("LiquidityAdded", {
            "args": {"base": TOKEN, "offers": 1, "quote": USDT, "funds": 3},
            "timestamp": 2000, "received_at": 2001,
        })
        self.assertEqual("LiquidityAdded", launch.source_event)
        self.assertEqual("USDT", launch.quote_symbol)

    def test_unrelated_event_returns_none(self):
        self.assertIsNone(launch_from_event("TokenPurchase", {"args": {}}))


class RadarCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_persists_launch_and_graduation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(Path(tmp) / "scanner.sqlite")
            collector = RadarCollector(store)
            self.assertTrue(await collector.handle_event("TokenCreate", {
                "args": {"token": TOKEN, "creator": CREATOR},
                "timestamp": 1000, "received_at": 1001, "blockNumber": 5, "logIndex": 0,
                "transactionHash": "0x" + "ab" * 32,
            }))
            self.assertTrue(await collector.handle_event("LiquidityAdded", {
                "args": {"base": TOKEN, "offers": 1, "quote": USDT, "funds": 3},
                "timestamp": 2000, "received_at": 2001, "blockNumber": 9, "logIndex": 1,
                "transactionHash": "0x" + "cd" * 32,
            }))
            self.assertEqual(1, collector.launches)
            self.assertEqual(1, collector.graduations)
            launches = store.rows("launch", f"bsc:{TOKEN}")
            self.assertEqual(1, len(launches))
            self.assertEqual("USDT", store.latest("graduation", f"bsc:{TOKEN}")["payload"]["quote_symbol"])


if __name__ == "__main__":
    unittest.main()

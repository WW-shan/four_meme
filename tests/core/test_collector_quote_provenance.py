import tempfile
import unittest
from pathlib import Path

from src.data.collector import DataCollector
from src.data.dataset_builder import DatasetBuilder

USDT = "0x55d398326f99059ff775485246999027b3197955"


class CollectorProvenanceTests(unittest.TestCase):
    def test_event_provenance_keeps_local_receive_time(self):
        provenance = DataCollector._event_provenance({
            "blockNumber": 7,
            "logIndex": 3,
            "transactionHash": "0x" + "ab" * 32,
            "received_at": 123.5,
        })
        self.assertEqual(123.5, provenance["received_at"])
        self.assertEqual("ab" * 32, provenance["transaction_hash"])

    def test_liquidity_added_marks_graduation_and_records_quote(self):
        with tempfile.TemporaryDirectory() as tmp:
            collector = DataCollector(output_dir=tmp)
            token = "0x" + "11" * 20
            collector.token_lifecycle[token] = {
                "symbol": "TEST",
                "last_update": 0,
                "buys": [],
                "sells": [],
                "price_history": [],
            }
            event = {
                "args": {"base": token, "offers": 1, "quote": USDT, "funds": 100},
                "timestamp": 1000,
                "received_at": 1001.5,
                "blockNumber": 5,
                "logIndex": 0,
                "transactionHash": "0x" + "cd" * 32,
            }
            self.assertTrue(collector.on_trade_stop(event))
            lifecycle = collector.token_lifecycle[token]
            self.assertTrue(lifecycle["graduated"])
            self.assertEqual(USDT, lifecycle["quote_asset"])
            self.assertEqual("USDT", lifecycle["quote_symbol"])

    def test_dataset_builder_skips_known_non_bnb_quote(self):
        builder = DatasetBuilder()
        samples = builder._generate_samples_from_lifecycle({
            "token_address": "0x" + "11" * 20,
            "symbol": "TEST",
            "created_at": 1000,
            "quote_symbol": "USDT",
            "buys": [],
            "sells": [],
        })
        self.assertEqual([], samples)

    def test_graduation_handlers_are_registered(self):
        root = Path(__file__).resolve().parents[2]
        tools_source = (root / "tools" / "collect_continuous.py").read_text(encoding="utf-8")
        bot_source = (root / "src" / "trader" / "bot.py").read_text(encoding="utf-8")
        self.assertIn("register_handler('LiquidityAdded'", tools_source)
        self.assertIn("register_handler('LiquidityAdded'", bot_source)


if __name__ == "__main__":
    unittest.main()

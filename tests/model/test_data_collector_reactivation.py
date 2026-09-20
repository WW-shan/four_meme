import tempfile
import unittest
from unittest.mock import patch

from src.data.collector import DataCollector


def _create_event(token: str, timestamp: int, symbol: str = "TKN") -> dict:
    return {
        "timestamp": timestamp,
        "blockNumber": 1,
        "args": {
            "token": token,
            "creator": "0xcreator",
            "name": "Token",
            "symbol": symbol,
            "totalSupply": int(1_000_000 * 1e18),
            "launchFee": int(0.01 * 1e18),
            "launchTime": timestamp,
        },
    }


def _buy_event(token: str, timestamp: int, account: str = "0xbuyer") -> dict:
    return {
        "timestamp": timestamp,
        "blockNumber": 2,
        "args": {
            "token": token,
            "account": account,
            "amount": int(100 * 1e18),
            "cost": int(1 * 1e18),
        },
    }


class TestDataCollectorReactivation(unittest.TestCase):
    def test_generate_training_sample_returns_none_when_current_price_is_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = DataCollector(output_dir=tmpdir, incremental_run_id="20260227_010000")

            token = "0xZERO"
            collector.token_lifecycle[token] = {
                "token_address": token,
                "symbol": "ZERO",
                "create_timestamp": 1000,
                "last_update": 1100,
                "buys": [{"timestamp": 1010, "price": 0.0, "bnb_amount": 1.0, "token_amount": 100.0, "account": "0x1"}],
                "sells": [],
                "price_history": [{"timestamp": 1020, "price": 2.0, "type": "buy"}],
                "unique_buyers": set(),
                "unique_sellers": set(),
            }

            sample = collector.generate_training_sample(token, sample_time=1010, future_window_seconds=60)

        self.assertIsNone(sample)

    def test_generate_training_sample_can_request_flow_features(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = DataCollector(output_dir=tmpdir, incremental_run_id="20260227_010000")

            token = "0xFLOW"
            collector.token_lifecycle[token] = {
                "token_address": token,
                "symbol": "FLOW",
                "create_timestamp": 1000,
                "last_update": 1020,
                "buys": [{"timestamp": 1005, "price": 1.0, "bnb_amount": 1.0, "token_amount": 100.0, "account": "0x1"}],
                "sells": [{"timestamp": 1008, "price": 1.1, "bnb_amount": 0.2, "token_amount": 10.0, "account": "0x2"}],
                "price_history": [{"timestamp": 1015, "price": 2.0, "type": "buy"}],
                "unique_buyers": set(),
                "unique_sellers": set(),
            }

            with patch("src.data.collector.extract_features", return_value={"current_price": 1.0, "sell_pressure_10s": 0.2}) as mock_extract:
                sample = collector.generate_training_sample(
                    token,
                    sample_time=1010,
                    future_window_seconds=60,
                    include_flow_features=True,
                )

        self.assertEqual(sample["features"]["sell_pressure_10s"], 0.2)
        self.assertEqual(sample["meta"]["flow_event_count_10s"], 2)
        self.assertEqual(sample["meta"]["flow_event_count_30s"], 2)
        self.assertEqual(sample["meta"]["flow_event_count_60s"], 2)
        self.assertTrue(mock_extract.call_args.kwargs["include_flow_features"])

    def test_token_reactivates_after_flush(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = DataCollector(output_dir=tmpdir, incremental_run_id="20260227_010000")

            token = "0xAAA"
            collector.on_token_create(_create_event(token, 1000, symbol="AAA"))
            collector.on_token_purchase(_buy_event(token, 1010, account="0x1"))

            flushed = collector.flush_eligible_tokens(
                current_time=1900,
                min_age_seconds=300,
                inactivity_seconds=300,
            )
            self.assertEqual(flushed, 1)
            self.assertNotIn(token, collector.token_lifecycle)

            # Same token becomes active again later: should rehydrate from metadata
            collector.on_token_purchase(_buy_event(token, 2000, account="0x2"))

            self.assertIn(token, collector.token_lifecycle)
            lifecycle = collector.token_lifecycle[token]
            self.assertEqual(lifecycle["symbol"], "AAA")
            self.assertEqual(len(lifecycle["buys"]), 1)
            self.assertEqual(lifecycle["buys"][0]["account"], "0x2")

    def test_token_reactivation_preserves_original_create_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = DataCollector(output_dir=tmpdir, incremental_run_id="20260227_010000")

            token = "0xAAA"
            collector.on_token_create(_create_event(token, 1000, symbol="AAA"))
            collector.on_token_purchase(_buy_event(token, 1010, account="0x1"))

            flushed = collector.flush_eligible_tokens(
                current_time=1900,
                min_age_seconds=300,
                inactivity_seconds=300,
            )
            self.assertEqual(flushed, 1)

            collector.on_token_purchase(_buy_event(token, 2000, account="0x2"))

            lifecycle = collector.token_lifecycle[token]
            self.assertEqual(lifecycle["create_timestamp"], 1000)
            self.assertEqual(lifecycle["create_block"], 1)
            self.assertEqual(lifecycle["last_update"], 2000)

    def test_metadata_index_roundtrip_preserves_create_fields_for_reactivation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = DataCollector(output_dir=tmpdir, incremental_run_id="20260227_010000")

            token = "0xAAA"
            collector.on_token_create(_create_event(token, 1000, symbol="AAA"))
            collector.on_token_purchase(_buy_event(token, 1010, account="0x1"))
            collector.flush_eligible_tokens(
                current_time=1900,
                min_age_seconds=300,
                inactivity_seconds=300,
            )
            collector.save_token_metadata_index()

            restored = DataCollector(output_dir=tmpdir, incremental_run_id="20260227_020000")
            self.assertEqual(restored.load_token_metadata_index(), 1)

            restored.on_token_purchase(_buy_event(token, 2000, account="0x2"))

            lifecycle = restored.token_lifecycle[token]
            self.assertEqual(lifecycle["create_timestamp"], 1000)
            self.assertEqual(lifecycle["create_block"], 1)
            self.assertEqual(lifecycle["last_update"], 2000)

    def test_legacy_metadata_reactivation_uses_launch_time_for_create_timestamp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = DataCollector(output_dir=tmpdir, incremental_run_id="20260227_010000")

            token = "0xAAA"
            collector.token_metadata[token] = {
                "token": token,
                "creator": "0xcreator",
                "name": "Token",
                "symbol": "AAA",
                "totalSupply": int(1_000_000 * 1e18),
                "launchFee": int(0.01 * 1e18),
                "launchTime": 1000,
            }

            collector.on_token_purchase(_buy_event(token, 2000, account="0x2"))

            lifecycle = collector.token_lifecycle[token]
            self.assertEqual(lifecycle["create_timestamp"], 1000)
            self.assertEqual(lifecycle["last_update"], 2000)

    def test_out_of_order_events_are_inserted_by_chain_time_and_keep_provenance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = DataCollector(output_dir=tmpdir, incremental_run_id="20260227_030000")
            token = "0xORDER"
            collector.on_token_create(_create_event(token, 1000, symbol="ORDER"))

            later = _buy_event(token, 1020, account="0xlater")
            later.update({"blockNumber": 12, "logIndex": 2, "transactionHash": b"\x02" * 32})
            earlier = _buy_event(token, 1010, account="0xearlier")
            earlier.update({"blockNumber": 11, "logIndex": 1, "transactionHash": b"\x01" * 32})

            collector.on_token_purchase(later)
            collector.on_token_purchase(earlier)

            lifecycle = collector.token_lifecycle[token]
            self.assertEqual([row["timestamp"] for row in lifecycle["buys"]], [1010, 1020])
            self.assertEqual([row["timestamp"] for row in lifecycle["price_history"]], [1010, 1020])
            self.assertEqual(lifecycle["last_update"], 1020)
            self.assertEqual(lifecycle["buys"][0]["block_number"], 11)
            self.assertEqual(lifecycle["buys"][1]["log_index"], 2)
            self.assertEqual(lifecycle["price_current"], lifecycle["buys"][1]["price"])


if __name__ == "__main__":
    unittest.main()

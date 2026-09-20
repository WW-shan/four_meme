import json
import tempfile
import unittest
from pathlib import Path

from scripts.attribute_market_cap_heat_failures import main
from src.pipeline.market_cap_heat_failure import FailureAttributionConfig, attribute_candidate
from src.pipeline.market_cap_heat_gate import MarketCapHeatConfig


def _lifecycle():
    create = 1_700_000_000
    buys = [
        {"timestamp": create, "account": "0xa", "bnb_amount": 10.0, "token_amount": 1.0, "price": 1e-8},
        {"timestamp": create + 10, "account": "0xb", "bnb_amount": 10.0, "token_amount": 1.0, "price": 2e-8},
        {"timestamp": create + 20, "account": "0xc", "bnb_amount": 10.0, "token_amount": 1.0, "price": 3e-8},
        {"timestamp": create + 30, "account": "0xd", "bnb_amount": 10.0, "token_amount": 1.0, "price": 4e-8},
        {"timestamp": create + 40, "account": "0xe", "bnb_amount": 10.0, "token_amount": 1.0, "price": 5e-8},
        {"timestamp": create + 45, "account": "0xf", "bnb_amount": 10.0, "token_amount": 1.0, "price": 5.5e-8},
        {"timestamp": create + 50, "account": "0xg", "bnb_amount": 10.0, "token_amount": 1.0, "price": 6e-8},
    ]
    return {
        "token_address": "0xTOKEN",
        "symbol": "TEST",
        "total_supply": 1e27,
        "create_timestamp": create,
        "buys": buys,
        "sells": [],
        "price_history": [
            {"timestamp": row["timestamp"], "price": row["price"], "type": "buy"}
            for row in buys
        ]
        + [{"timestamp": create + 3_700, "price": 4e-8, "type": "sell"}],
    }


class MarketCapHeatFailureTests(unittest.TestCase):
    def test_causal_snapshot_does_not_use_a_future_event_as_one_hour_exit(self):
        row = attribute_candidate(
            _lifecycle(),
            config=FailureAttributionConfig(
                threshold_usd=30_000,
                heat=MarketCapHeatConfig(bnb_usd=1_000, min_buy_count=3),
            ),
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["h3600_snapshot_kind"], "missing_before_entry")
        self.assertIsNone(row["h3600_snapshot_age_seconds"])
        self.assertIsNone(row["h3600_near_target_price_return"])
        self.assertIn("no_near_target_1h_observation", row["failure_tags"])
        self.assertEqual(row["actual_execution_status"], "unknown")

    def test_cli_writes_per_candidate_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lifecycle = root / "lifecycle_1.jsonl"
            lifecycle.write_text(json.dumps(_lifecycle()) + "\n", encoding="utf-8")
            output = root / "report.json"
            self.assertEqual(
                main(
                    [
                        "--lifecycle-file",
                        str(lifecycle),
                        "--threshold-usd",
                        "30000",
                        "--bnb-usd",
                        "1000",
                        "--output",
                        str(output),
                    ]
                ),
                0,
            )
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(report["rows"]), 1)
            self.assertFalse(report["safe_for_live_switch"])
            self.assertFalse(report["model_selection_eligible"])

    def test_event_after_horizon_is_not_an_asof_snapshot_or_fill(self):
        lifecycle = _lifecycle()
        target = 1_700_000_000 + 53 + 3600
        lifecycle["price_history"].extend([
            {"timestamp": target - 1, "price": 6e-8},
            {"timestamp": target + 1, "price": 12e-8},
        ])
        row = attribute_candidate(lifecycle, config=FailureAttributionConfig(
            threshold_usd=30_000, heat=MarketCapHeatConfig(bnb_usd=1000, fee_rate=0, slippage_rate=0)))
        self.assertEqual(row["h3600_snapshot_return"], 0)
        self.assertEqual(row["h3600_near_target_price_return"], 1)
        self.assertEqual(row["actual_execution_status"], "unknown")
        self.assertIsNone(row["actual_pnl_bnb"])

    def test_entry_mark_includes_price_change_during_declared_delay(self):
        lifecycle = _lifecycle()
        lifecycle["price_history"].extend([
            {"timestamp": 1_700_000_052, "price": 12e-8},
            {"timestamp": 1_700_003_653, "price": 12e-8},
        ])
        row = attribute_candidate(lifecycle, config=FailureAttributionConfig(
            threshold_usd=30_000, heat=MarketCapHeatConfig(bnb_usd=1000, fee_rate=0, slippage_rate=0)))
        self.assertEqual(row["entry_mark_price"], 12e-8)
        self.assertEqual(row["h3600_snapshot_return"], 0)


if __name__ == "__main__":
    unittest.main()

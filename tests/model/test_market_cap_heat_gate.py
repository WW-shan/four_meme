import json
import tempfile
import unittest
from pathlib import Path

from scripts.profile_market_cap_heat_gate import main
from src.pipeline.market_cap_heat_gate import (
    MarketCapHeatConfig,
    implied_market_cap_bnb,
    profile_market_cap_heat_gate,
    _price_path,
)


def _lifecycle():
    create = 1_700_000_000
    buys = [
        {"timestamp": create, "account": "0xa", "bnb_amount": 10.0, "token_amount": 1.0, "price": 1e-8},
        {"timestamp": create + 10, "account": "0xb", "bnb_amount": 10.0, "token_amount": 1.0, "price": 2e-8},
        {"timestamp": create + 20, "account": "0xc", "bnb_amount": 10.0, "token_amount": 1.0, "price": 3e-8},
        {"timestamp": create + 30, "account": "0xd", "bnb_amount": 10.0, "token_amount": 1.0, "price": 4e-8},
        {"timestamp": create + 40, "account": "0xe", "bnb_amount": 10.0, "token_amount": 1.0, "price": 5e-8},
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
        + [
            {"timestamp": create + 3_700, "price": 6e-8, "type": "buy"},
            {"timestamp": create + 7_300, "price": 7e-8, "type": "buy"},
        ],
    }


class MarketCapHeatGateTests(unittest.TestCase):
    def test_implied_market_cap_uses_raw_supply_units(self):
        self.assertAlmostEqual(implied_market_cap_bnb(5e-8, 1e27), 50.0)

    def test_price_magnitude_cannot_identify_a_liquidity_event(self):
        times, prices = _price_path(
            {
                "price_history": [
                    {"timestamp": 100, "price": 1e-8},
                    {"timestamp": 110, "price": 1e-27, "type": "sell"},
                    {"timestamp": 120, "price": 2e-8},
                ]
            }
        )
        self.assertEqual(times, [100.0, 110.0, 120.0])
        self.assertEqual(prices, [1e-8, 1e-27, 2e-8])

    def test_same_second_prices_are_not_sorted_by_magnitude(self):
        times, prices = _price_path({"price_history": [
            {"timestamp": 100, "price": 2e-8},
            {"timestamp": 100, "price": 1e-8},
        ]})
        self.assertEqual(times, [100.0, 100.0])
        self.assertEqual(prices, [2e-8, 1e-8])

    def test_profile_uses_confirmation_and_reports_complete_horizon(self):
        report = profile_market_cap_heat_gate(
            [_lifecycle()],
            thresholds_usd=(30_000.0,),
            config=MarketCapHeatConfig(bnb_usd=1_000.0, min_buy_count=3),
        )
        threshold = report["thresholds"]["30000.0"]
        self.assertEqual(threshold["crossing_and_confirmed_count"], 1)
        self.assertEqual(threshold["modes"]["activity"]["selected_count"], 1)
        self.assertEqual(threshold["modes"]["activity"]["3600"]["complete"], 1)
        self.assertFalse(report["safe_for_live_switch"])
        self.assertFalse(report["model_selection_eligible"])
        self.assertFalse(report["execution_verified"])

    def test_cli_writes_offline_report(self):
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
                        "--thresholds-usd",
                        "30000",
                        "--bnb-usd",
                        "1000",
                        "--output",
                        str(output),
                    ]
                ),
                0,
            )
            self.assertFalse(json.loads(output.read_text(encoding="utf-8"))["safe_for_live_switch"])


if __name__ == "__main__":
    unittest.main()

import math
import unittest

from src.data.feature_extractor import extract_features


OPTIONAL_FLOW_KEYS = [
    "sell_volume_10s",
    "sell_volume_30s",
    "sell_volume_60s",
    "total_flow_volume_10s",
    "total_flow_volume_30s",
    "total_flow_volume_60s",
    "sell_pressure_10s",
    "sell_pressure_30s",
    "sell_pressure_60s",
    "signed_imbalance_10s",
    "signed_imbalance_30s",
    "signed_imbalance_60s",
]


class TestFeatureExtractorBehaviorDynamics(unittest.TestCase):
    def test_extract_features_includes_behavior_dynamics_keys(self):
        lifecycle = {
            "create_timestamp": 100,
            "total_supply": 1_000_000 * 10**18,
            "launch_fee": int(0.5 * 10**18),
            "name": "TokenX",
            "symbol": "TKX",
            "creator": "creator",
        }

        past_buys = [
            {"timestamp": 110, "price": 1.0, "account": "a", "bnb_amount": 1.0, "token_amount": 100.0},
            {"timestamp": 120, "price": 1.1, "account": "b", "bnb_amount": 0.2, "token_amount": 20.0},
            {"timestamp": 125, "price": 1.2, "account": "c", "bnb_amount": 0.1, "token_amount": 10.0},
            {"timestamp": 128, "price": 1.3, "account": "a", "bnb_amount": 0.3, "token_amount": 25.0},
        ]
        past_sells = [
            {"timestamp": 127, "price": 1.25, "account": "b", "bnb_amount": 0.05, "token_amount": 5.0},
        ]

        features = extract_features(
            lifecycle=lifecycle,
            past_buys=past_buys,
            past_sells=past_sells,
            sample_time=130,
        )

        required_keys = [
            "top10_holder_share_10s",
            "top10_holder_share_30s",
            "concentration_decay_10_30",
            "retail_entry_rate_ratio_30s",
            "lp_resistance_ratio_10s",
            "creator_buy_share",
            "creator_sell_share",
            "round_trip_buy_volume_ratio",
            "buy_volume_per_unique_buyer",
            "volume_price_divergence",
            "sell_pressure_change_10_60",
        ]

        for key in required_keys:
            self.assertIn(key, features)
            self.assertIsInstance(features[key], float)

        for key in OPTIONAL_FLOW_KEYS:
            self.assertNotIn(key, features)

        self.assertAlmostEqual(features["creator_buy_share"], 0.0, places=12)
        self.assertAlmostEqual(features["creator_sell_share"], 0.0, places=12)
        self.assertAlmostEqual(features["round_trip_buy_volume_ratio"], 0.2 / 1.6, places=12)
        self.assertAlmostEqual(features["buy_volume_per_unique_buyer"], 1.6 / 3.0, places=12)
        self.assertAlmostEqual(
            features["volume_price_divergence"],
            math.log1p(1.65) / (1.0 + abs((1.3 - 1.0) / 1.0 * 100.0) / 100.0),
            places=12,
        )
        self.assertAlmostEqual(
            features["sell_pressure_change_10_60"],
            (0.05 / (0.4 + 0.05)) - (0.05 / (1.6 + 0.05)),
            places=12,
        )

    def test_extract_features_omits_optional_flow_features_by_default_for_schema_compatibility(self):
        lifecycle = {
            "create_timestamp": 0,
            "total_supply": 1_000_000 * 10**18,
            "launch_fee": int(1.0 * 10**18),
            "name": "TokenDefault",
            "symbol": "TKD",
            "creator": "creator",
        }
        features = extract_features(
            lifecycle=lifecycle,
            past_buys=[
                {"timestamp": 95, "price": 1.1, "account": "b_new", "bnb_amount": 1.0, "token_amount": 10.0},
            ],
            past_sells=[
                {"timestamp": 96, "price": 0.9, "account": "s_new", "bnb_amount": 4.0, "token_amount": 40.0},
            ],
            sample_time=100,
        )

        for key in OPTIONAL_FLOW_KEYS:
            self.assertNotIn(key, features)

    def test_extract_features_behavior_dynamics_formula_contract(self):
        lifecycle = {
            "create_timestamp": 0,
            "total_supply": 1_000_000 * 10**18,
            "launch_fee": int(8.0 * 10**18),
            "name": "TokenY",
            "symbol": "TKY",
            "creator": "creator",
        }

        recent_buys = [
            {
                "timestamp": 95,
                "price": 1.0,
                "account": f"r{i}",
                "bnb_amount": 1.0,
                "token_amount": 10.0,
            }
            for i in range(12)
        ]
        older_buys = [
            {
                "timestamp": 80,
                "price": 1.0,
                "account": f"o{i}",
                "bnb_amount": 1.0,
                "token_amount": 10.0,
            }
            for i in range(18)
        ]
        past_buys = recent_buys + older_buys
        past_sells = [
            {"timestamp": 96, "price": 1.0, "account": "s1", "bnb_amount": 2.0, "token_amount": 1.0},
            {"timestamp": 97, "price": 1.0, "account": "s2", "bnb_amount": 2.0, "token_amount": 1.0},
        ]

        features = extract_features(
            lifecycle=lifecycle,
            past_buys=past_buys,
            past_sells=past_sells,
            sample_time=100,
        )

        expected_top10_10s = 100.0 / 120.0
        expected_top10_30s = 100.0 / 300.0
        expected_concentration_decay = (expected_top10_10s - expected_top10_30s) / 20.0

        expected_unique_buyer_slope = (30.0 - 12.0) / 20.0
        expected_volume_slope = (30.0 - 12.0) / 20.0
        expected_retail_entry_rate_ratio = expected_unique_buyer_slope / max(expected_volume_slope, 1e-9)

        expected_lp_depth_proxy = 8.0 + 12.0
        expected_recent_sell_pressure = 4.0
        expected_lp_resistance_ratio = expected_lp_depth_proxy / max(expected_recent_sell_pressure, 1e-9)

        self.assertAlmostEqual(features["top10_holder_share_10s"], expected_top10_10s, places=12)
        self.assertAlmostEqual(features["top10_holder_share_30s"], expected_top10_30s, places=12)
        self.assertAlmostEqual(features["concentration_decay_10_30"], expected_concentration_decay, places=12)
        self.assertAlmostEqual(features["retail_entry_rate_ratio_30s"], expected_retail_entry_rate_ratio, places=12)
        self.assertAlmostEqual(features["lp_resistance_ratio_10s"], expected_lp_resistance_ratio, places=12)

    def test_extract_features_includes_causal_short_window_sell_pressure(self):
        lifecycle = {
            "create_timestamp": 0,
            "total_supply": 1_000_000 * 10**18,
            "launch_fee": int(1.0 * 10**18),
            "name": "TokenZ",
            "symbol": "TKZ",
            "creator": "creator",
        }
        past_buys = [
            {"timestamp": 75, "price": 1.0, "account": "b_old", "bnb_amount": 3.0, "token_amount": 30.0},
            {"timestamp": 95, "price": 1.1, "account": "b_new", "bnb_amount": 1.0, "token_amount": 10.0},
        ]
        past_sells = [
            {"timestamp": 82, "price": 1.2, "account": "s_old", "bnb_amount": 2.0, "token_amount": 20.0},
            {"timestamp": 96, "price": 0.9, "account": "s_new", "bnb_amount": 4.0, "token_amount": 40.0},
        ]

        features = extract_features(
            lifecycle=lifecycle,
            past_buys=past_buys,
            past_sells=past_sells,
            sample_time=100,
            include_flow_features=True,
        )

        self.assertAlmostEqual(features["sell_volume_10s"], 4.0, places=12)
        self.assertAlmostEqual(features["sell_volume_30s"], 6.0, places=12)
        self.assertAlmostEqual(features["sell_volume_60s"], 6.0, places=12)
        self.assertAlmostEqual(features["total_flow_volume_10s"], 5.0, places=12)
        self.assertAlmostEqual(features["total_flow_volume_30s"], 10.0, places=12)
        self.assertAlmostEqual(features["sell_pressure_10s"], 4.0 / 5.0, places=12)
        self.assertAlmostEqual(features["sell_pressure_30s"], 6.0 / 10.0, places=12)
        self.assertAlmostEqual(features["signed_imbalance_10s"], (1.0 - 4.0) / 5.0, places=12)
        self.assertAlmostEqual(features["signed_imbalance_30s"], (4.0 - 6.0) / 10.0, places=12)

    def test_optional_flow_features_preserve_legacy_helper_window_semantics(self):
        lifecycle = {
            "create_timestamp": 0,
            "total_supply": 1_000_000 * 10**18,
            "launch_fee": int(1.0 * 10**18),
            "name": "TokenB",
            "symbol": "TKB",
            "creator": "creator",
        }
        past_buys = [
            {"timestamp": 75, "price": 1.0, "account": "b_old", "bnb_amount": 3.0, "token_amount": 30.0},
            {"timestamp": 90, "price": 1.0, "account": "b_edge", "bnb_amount": 2.0, "token_amount": 20.0},
            {"timestamp": 95, "price": 1.1, "account": "b_new", "bnb_amount": 1.0, "token_amount": 10.0},
        ]
        past_sells = [
            {"timestamp": 82, "price": 1.2, "account": "s_old", "bnb_amount": 2.0, "token_amount": 20.0},
            {"timestamp": 90, "price": 1.0, "account": "s_edge", "bnb_amount": 5.0, "token_amount": 50.0},
            {"timestamp": 96, "price": 0.9, "account": "s_new", "bnb_amount": 4.0, "token_amount": 40.0},
        ]

        features = extract_features(
            lifecycle=lifecycle,
            past_buys=past_buys,
            past_sells=past_sells,
            sample_time=100,
            include_flow_features=True,
        )

        self.assertAlmostEqual(features["volume_10s"], 3.0, places=12)
        self.assertAlmostEqual(features["sell_volume_10s"], 4.0, places=12)
        self.assertAlmostEqual(features["total_flow_volume_10s"], 5.0, places=12)
        self.assertAlmostEqual(features["sell_pressure_10s"], 4.0 / 5.0, places=12)
        self.assertAlmostEqual(features["signed_imbalance_10s"], (1.0 - 4.0) / 5.0, places=12)


if __name__ == "__main__":
    unittest.main()

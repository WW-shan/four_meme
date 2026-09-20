import unittest

from src.pipeline.cross_boundary import build_cross_boundary_lifecycle


class CrossBoundaryTests(unittest.TestCase):
    def test_scales_dex_prices_at_graduation_and_keeps_post_curve_path(self):
        lifecycle = {
            "token_address": "0xTOKEN",
            "graduate_time": 200,
            "last_update": 200,
            "price_history": [
                {"timestamp": 100, "price": 1.0, "type": "buy"},
                {"timestamp": 200, "price": 2.0, "type": "sell"},
            ],
        }
        dex = {
            "pool_address": "0xPOOL",
            "quote_side": "quote",
            "source_url": "https://example.invalid/ohlcv",
            "bars": [
                [200, 10.0, 11.0, 9.0, 10.0, 100.0],
                [3600, 20.0, 22.0, 18.0, 20.0, 200.0],
            ],
        }

        merged = build_cross_boundary_lifecycle(lifecycle, dex)

        self.assertIsNotNone(merged)
        self.assertEqual([point["timestamp"] for point in merged["price_history"]], [100.0, 200.0, 3600.0])
        self.assertEqual([point["price"] for point in merged["price_history"]], [1.0, 2.0, 4.0])
        self.assertEqual(merged["cross_boundary"]["pool_address"], "0xPOOL")
        self.assertTrue(merged["cross_boundary"]["approximate_price_bridge"])

    def test_requires_curve_and_dex_overlap(self):
        lifecycle = {
            "graduate_time": 200,
            "price_history": [{"timestamp": 100, "price": 1.0}],
        }
        dex = {"bars": [[100, 10.0, 10.0, 10.0, 10.0, 1.0]]}

        self.assertIsNone(build_cross_boundary_lifecycle(lifecycle, dex))

    def test_curve_quote_at_graduation_is_preserved(self):
        lifecycle = {
            "token_address": "0xabc",
            "graduate_time": 100,
            "last_update": 100,
            "price_history": [{"timestamp": 100, "price": 2.0, "type": "buy"}],
        }
        merged = build_cross_boundary_lifecycle(
            lifecycle,
            {"bars": [[120, 10.0, 12.0, 9.0, 11.0, 100.0]]},
        )
        self.assertIsNotNone(merged)
        self.assertEqual(merged["price_history"][0]["timestamp"], 100.0)
        self.assertEqual(merged["price_history"][0]["price"], 2.0)


if __name__ == "__main__":
    unittest.main()

import unittest
import json
import tempfile
from pathlib import Path

from scripts.repair_post_graduation_bridges import repair
from scripts.validate_post_graduation_ohlcv import validate
from scripts.run_tail_capture_training import _load_market_context


class PostGraduationRepairTests(unittest.TestCase):
    def _base(self):
        return [{
            "token_address": "0xabc",
            "symbol": "TEST",
            "name": "Test",
            "creator": "creator",
            "total_supply": 1e18,
            "launch_fee": 0.0,
            "create_timestamp": 100,
            "graduate_time": 200,
            "graduated": True,
            "buys": [{"timestamp": 200, "account": "a", "token_amount": 1.0, "bnb_amount": 1.0, "price": 2.0}],
            "sells": [],
            "price_history": [{"timestamp": 200, "price": 2.0, "type": "buy"}],
            "last_update": 200,
        }]

    def test_repair_removes_pre_graduation_bars_and_restores_anchor(self):
        payload = {
            "schema_version": 1,
            "as_of_timestamp": 500,
            "rows": [{
                "token": "0xabc",
                "status": "ok",
                "graduation_time": 200,
                "pool_address": "0xpool",
                "token_side": "base",
                "bars": [[100, 9, 10, 8, 9, 1], [200, 10, 12, 9, 11, 2], [300, 11, 12, 10, 11, 3]],
            }],
        }
        repaired, stats = repair(payload, self._base())
        self.assertEqual(stats["changed_ok_rows"], 1)
        row = repaired["rows"][0]
        self.assertEqual([bar[0] for bar in row["bars"]], [200.0, 300.0])
        self.assertEqual(row["lifecycle"]["price_history"][0]["timestamp"], 200.0)

    def test_validator_rejects_pre_graduation_bar(self):
        payload = {
            "as_of_timestamp": 500,
            "rows": [{
                "token": "0xabc",
                "status": "ok",
                "graduation_time": 200,
                "bars": [[100, 9, 10, 8, 9, 1]],
                "cross_boundary": {"dex_first_timestamp": 100, "dex_last_timestamp": 100, "approximate_price_bridge": True},
                "lifecycle": {"price_history": [{"timestamp": 200, "price": 2.0, "type": "buy"}]},
            }],
        }
        report = validate(payload, self._base())
        self.assertFalse(report["valid"])
        self.assertIn("bar_before_graduation", report["violation_codes"])

    def test_market_context_uses_previous_day_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            path.write_text(json.dumps({
                "daily_chart_tail": [
                    {"date_utc": "2026-09-01", "volume_usd": 100.0},
                    {"date_utc": "2026-09-02", "volume_usd": 200.0},
                ],
            }), encoding="utf-8")
            context, _ = _load_market_context(path)
        self.assertEqual(context["2026-09-02"]["volume_usd"], 100.0)


if __name__ == "__main__":
    unittest.main()

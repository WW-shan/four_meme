import unittest

from src.pipeline.dex_confirmation_strategy import DexConfirmationConfig, simulate_confirmed_entry
from src.pipeline.tail_capture_strategy import TailReplayConfig, build_tail_candidates


def _candidate():
    create = 1_700_000_000
    buys = [
        {
            "timestamp": create + index * 10,
            "account": account,
            "token_amount": 100.0,
            "bnb_amount": 0.01,
            "price": 1.0 + index * 0.02,
        }
        for index, account in enumerate(("a", "b", "c", "a", "b"))
    ]
    lifecycle = {
        "token_address": "0xconfirm",
        "name": "Confirm",
        "symbol": "CONFIRM",
        "creator": "creator",
        "total_supply": 1e18,
        "launch_fee": 0.0,
        "create_timestamp": create,
        "graduate_time": create + 300,
        "graduated": True,
        "buys": buys,
        "sells": [],
        "price_history": [
            {"timestamp": row["timestamp"], "price": row["price"], "type": "buy"}
            for row in buys
        ]
        + [
            {"timestamp": create + 3600, "price": 10.0, "type": "dex_close", "dex_volume": 100.0},
            {"timestamp": create + 7200, "price": 9.0, "type": "dex_close", "dex_volume": 60.0},
            {"timestamp": create + 10800, "price": 10.0, "type": "dex_close", "dex_volume": 70.0},
            {"timestamp": create + 14400, "price": 11.0, "type": "dex_close", "dex_volume": 80.0},
        ],
        "cross_boundary": {"graduation_timestamp": create + 300},
        "last_update": create + 14400,
    }
    config = TailReplayConfig(entry_delay_seconds=0, exit_delay_seconds=0, trailing_stop_pct=None)
    candidates = build_tail_candidates(
        [lifecycle],
        config,
        max_candidates_per_token=1,
        market_context={"2023-11-14": {"volume_usd": 1_000_000.0, "vs_7d_avg": 1.0}},
    )
    return candidates[0]


class DexConfirmationStrategyTests(unittest.TestCase):
    def test_confirmation_passes_and_enters_after_second_bar(self):
        outcome = simulate_confirmed_entry(
            _candidate(),
            horizon_seconds=3_600,
            config=DexConfirmationConfig(
                confirmation_bars=2,
                min_market_volume_ratio=0.8,
                min_price_retention_pct=-35.0,
                min_volume_retention_ratio=0.2,
                replay=TailReplayConfig(entry_delay_seconds=0, exit_delay_seconds=0),
            ),
        )
        self.assertTrue(outcome["confirmation_passed"])
        self.assertTrue(outcome["entry_available"])
        self.assertEqual(outcome["confirmation_time"], 1_700_007_200.0)

    def test_weak_market_regime_rejects_before_entry(self):
        candidate = _candidate()
        candidate["features"]["bsc_dex_volume_vs_7d_avg"] = 0.5
        outcome = simulate_confirmed_entry(candidate, horizon_seconds=3_600)
        self.assertEqual(outcome["status"], "regime_rejected")
        self.assertFalse(outcome["entry_available"])

    def test_volume_decay_rejects_confirmation(self):
        candidate = _candidate()
        candidate["dex_bars"] = [
            (1_700_003_600.0, 10.0, 100.0),
            (1_700_007_200.0, 9.0, 5.0),
        ]
        outcome = simulate_confirmed_entry(candidate, horizon_seconds=3_600)
        self.assertEqual(outcome["status"], "confirmation_rejected_volume")
        self.assertFalse(outcome["entry_available"])


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timedelta, timezone

from src.pipeline.reentry_probe import PricePoint
from src.pipeline.target_barrier_profile import (
    BarrierReplayConfig,
    chronological_splits,
    simulate_barrier,
    summarize_outcomes,
)


def _candidate(*rows):
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return {
        "token": "0xabc",
        "sample_time": base.timestamp(),
        "path": [
            PricePoint(base + timedelta(seconds=int(seconds)), float(price), kind)
            for seconds, price, kind in rows
        ],
    }


class TargetBarrierProfileTests(unittest.TestCase):
    def test_target_is_executable_and_graduation_is_not_considered(self):
        candidate = _candidate((0, 1.0, "buy"), (10, 1.6, "buy"), (30, 1.6, "buy"))
        result = simulate_barrier(
            candidate,
            horizon_seconds=30,
            target_return_pct=50,
            stop_loss_pct=-30,
            config=BarrierReplayConfig(entry_delay_seconds=0, exit_delay_seconds=0, fee_bps=0, slippage_bps=0),
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["reason"], "target")
        self.assertTrue(result["target_hit"])

    def test_same_second_target_and_stop_uses_conservative_stop(self):
        candidate = _candidate(
            (0, 1.0, "buy"),
            (10, 1.6, "buy"),
            (10, 0.7, "sell"),
            (20, 0.7, "sell"),
        )
        result = simulate_barrier(
            candidate,
            horizon_seconds=30,
            target_return_pct=50,
            stop_loss_pct=-30,
            config=BarrierReplayConfig(entry_delay_seconds=0, exit_delay_seconds=0, fee_bps=0, slippage_bps=0),
        )
        self.assertEqual(result["reason"], "ambiguous_stop")
        self.assertTrue(result["ambiguous_barrier"])

    def test_incomplete_path_is_visible_and_counted_in_conservative_metrics(self):
        candidate = _candidate((0, 1.0, "buy"), (5, 0.8, "sell"))
        result = simulate_barrier(
            candidate,
            horizon_seconds=60,
            target_return_pct=50,
            stop_loss_pct=-30,
            config=BarrierReplayConfig(entry_delay_seconds=0, exit_delay_seconds=0, fee_bps=0, slippage_bps=0),
        )
        self.assertEqual(result["status"], "censored")
        summary = summarize_outcomes([result], fixed_stake_bnb=0.2)
        self.assertEqual(summary["candidate_count"], 1)
        self.assertEqual(summary["complete_count"], 0)
        self.assertEqual(summary["censored_count"], 1)
        self.assertLess(summary["conservative_all_candidates"]["returns"]["net_profit_bnb"], 0)

    def test_entry_price_protection_marks_chased_signal_as_non_trade(self):
        candidate = _candidate((0, 1.0, "buy"), (5, 1.4, "buy"), (60, 1.4, "buy"))
        candidate["signal_price"] = 1.0
        result = simulate_barrier(
            candidate,
            horizon_seconds=60,
            target_return_pct=50,
            stop_loss_pct=-30,
            config=BarrierReplayConfig(
                entry_delay_seconds=3,
                exit_delay_seconds=0,
                fee_bps=0,
                slippage_bps=0,
                entry_max_fill_wait_seconds=10,
                entry_price_protection_pct=0.25,
            ),
        )
        self.assertEqual(result["status"], "entry_protection_skip")

    def test_chronological_splits_are_time_ordered(self):
        rows = [{"sample_time": value, "token": str(value)} for value in range(10)]
        splits = chronological_splits(rows)
        self.assertEqual(len(splits["train"]), 6)
        self.assertEqual(len(splits["validation"]), 2)
        self.assertEqual(len(splits["final"]), 2)
        self.assertLess(splits["train"][-1]["sample_time"], splits["validation"][0]["sample_time"])


if __name__ == "__main__":
    unittest.main()

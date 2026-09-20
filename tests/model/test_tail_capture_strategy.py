import unittest

import src.pipeline.tail_capture_strategy as strategy
from src.pipeline.tail_capture_strategy import (
    TailReplayConfig,
    build_tail_candidates,
    simulate_tail_path,
)


def _lifecycle():
    create = 1_700_000_000
    accounts = ["a", "b", "c", "a", "b", "d"]
    buys = [
        {
            "timestamp": create + index * 10,
            "account": account,
            "token_amount": 100.0,
            "bnb_amount": 0.01,
            "price": 1.0 + index * 0.02,
        }
        for index, account in enumerate(accounts)
    ]
    return {
        "token_address": "0xabc",
        "name": "Test Token",
        "symbol": "TEST",
        "creator": "creator",
        "total_supply": 1e18,
        "launch_fee": 0.0,
        "create_timestamp": create,
        "buys": buys,
        "sells": [],
        "price_history": [
            {"timestamp": row["timestamp"], "price": row["price"], "type": "buy"}
            for row in buys
        ]
        + [
            {"timestamp": create + 3_600, "price": 12.0, "type": "dex_close"},
            {"timestamp": create + 7_200, "price": 8.0, "type": "dex_close"},
        ],
        "last_update": create + 7_200,
    }


class TailCaptureStrategyTests(unittest.TestCase):
    def test_candidates_include_later_causal_events(self):
        config = TailReplayConfig(
            entry_delay_seconds=0,
            min_entry_unique_buyers=3,
            min_entry_buy_count=5,
            max_entry_age_seconds=300,
        )
        candidates = build_tail_candidates([_lifecycle()], config, max_candidates_per_token=0)
        self.assertGreaterEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["sample_time"], 1_700_000_040)
        self.assertEqual(candidates[0]["features"]["candidate_sequence"], 5.0)
        self.assertEqual({row["token"] for row in candidates}, {"0xabc"})

    def test_market_regime_is_joined_by_decision_date(self):
        config = TailReplayConfig(entry_delay_seconds=0)
        candidates = build_tail_candidates(
            [_lifecycle()],
            config,
            max_candidates_per_token=1,
            market_context={
                "2023-11-14": {"volume_usd": 1_000_000.0, "vs_7d_avg": 0.5},
            },
        )
        self.assertGreater(candidates[0]["features"]["bsc_dex_volume_24h_usd_log"], 0.0)
        self.assertEqual(candidates[0]["features"]["bsc_dex_volume_vs_7d_avg"], 0.5)

    def test_fixed_hold_keeps_multi_x_path(self):
        config = TailReplayConfig(entry_delay_seconds=0, exit_delay_seconds=0)
        candidate = build_tail_candidates([_lifecycle()], config, max_candidates_per_token=1)[0]
        outcome = simulate_tail_path(candidate, horizon_seconds=3_600, config=config)
        self.assertEqual(outcome["status"], "ok")
        self.assertGreater(outcome["return_pct"], 500.0)
        self.assertTrue(outcome["tail_10x"])

    def test_trailing_stop_is_optional_and_causal(self):
        base = build_tail_candidates([_lifecycle()], TailReplayConfig(entry_delay_seconds=0), max_candidates_per_token=1)[0]
        config = TailReplayConfig(entry_delay_seconds=0, exit_delay_seconds=0, trailing_stop_pct=20.0)
        outcome = simulate_tail_path(base, horizon_seconds=7_160, config=config)
        self.assertEqual(outcome["status"], "ok")
        self.assertEqual(outcome["exit_reason"], "trailing_stop")
        self.assertLess(outcome["return_pct"], 1_100.0)

    def test_rank_groups_compare_competing_tokens_in_same_hour(self):
        candidates = [
            {"token": "a", "sample_time": 3_600},
            {"token": "b", "sample_time": 3_900},
            {"token": "c", "sample_time": 7_200},
        ]
        groups = strategy._group_ids(candidates, [0, 1, 2], group_seconds=3_600)
        self.assertEqual(groups[0], groups[1])
        self.assertNotEqual(groups[1], groups[2])

    def test_missing_entry_is_not_counted_as_trade(self):
        candidates = [{"token": "a", "sample_time": 1}]
        outcomes = [{"status": "missing_entry", "conservative_return_pct": -100.0}]
        replay = strategy._portfolio_backtest(candidates, outcomes, [1.0], [0], TailReplayConfig(), 0.0)
        self.assertEqual(replay["signal_count"], 1)
        self.assertEqual(replay["trade_count"], 0)
        self.assertEqual(replay["entry_failures"], 1)

    def test_incomplete_policy_is_explicit(self):
        lifecycle = _lifecycle()
        lifecycle["price_history"] = lifecycle["price_history"][:6]
        lifecycle["last_update"] = lifecycle["price_history"][-1]["timestamp"]
        candidate = build_tail_candidates([lifecycle], TailReplayConfig(entry_delay_seconds=0), max_candidates_per_token=1)[0]
        outcome = simulate_tail_path(candidate, horizon_seconds=3_600, config=TailReplayConfig(entry_delay_seconds=0))
        self.assertEqual(outcome["status"], "incomplete_last_observation")
        self.assertFalse(outcome["complete"])


if __name__ == "__main__":
    unittest.main()

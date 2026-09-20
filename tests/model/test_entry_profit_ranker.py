import unittest

from src.pipeline.entry_profit_ranker import _portfolio_backtest, _split_indices, signed_log_return
from src.pipeline.target_barrier_profile import default_entry_gate_grid


class EntryProfitRankerTests(unittest.TestCase):
    def test_signed_log_return_preserves_sign_and_compresses_tail(self):
        self.assertGreater(signed_log_return(100), signed_log_return(20))
        self.assertLess(signed_log_return(-50), 0.0)
        self.assertAlmostEqual(signed_log_return(0), 0.0)

    def test_split_indices_are_chronological_and_disjoint(self):
        rows = [{"sample_time": value, "token": str(value)} for value in range(10)]
        splits = _split_indices(rows)
        self.assertEqual([len(splits[name]) for name in ("train", "validation", "final")], [6, 2, 2])
        self.assertEqual(set(splits["train"]) & set(splits["final"]), set())
        self.assertLess(rows[splits["train"][-1]]["sample_time"], rows[splits["final"][0]]["sample_time"])

    def test_split_indices_purge_cross_boundary_labels(self):
        rows = [{"sample_time": value * 10, "token": str(value)} for value in range(10)]
        splits = _split_indices(rows, purge_seconds=15)
        self.assertEqual(len(splits["train"]), 5)
        self.assertEqual(len(splits["validation"]), 1)
        self.assertEqual(len(splits["final"]), 2)

    def test_default_gate_grid_includes_legacy_and_earlier_entry_options(self):
        gates = default_entry_gate_grid()
        rules = {(row["min_entry_unique_buyers"], row["min_entry_buy_count"], row["max_entry_age_seconds"]) for row in gates}
        self.assertIn((3, 5, 300), rules)
        self.assertIn((2, 3, 120), rules)
        self.assertGreater(len(rules), 1)

    def test_portfolio_replay_enforces_cash_and_open_position_cap(self):
        candidates = [
            {"sample_time": 0, "token": "a"},
            {"sample_time": 1, "token": "b"},
            {"sample_time": 101, "token": "c"},
        ]
        outcomes = [
            {"status": "ok", "entry_time": 0, "exit_time": 100, "conservative_return_pct": 100},
            {"status": "ok", "entry_time": 1, "exit_time": 100, "conservative_return_pct": 100},
            {"status": "ok", "entry_time": 101, "exit_time": 200, "conservative_return_pct": -50},
        ]
        result = _portfolio_backtest(
            candidates,
            outcomes,
            [1.0, 0.9, 0.8],
            [0, 1, 2],
            threshold=0.5,
            initial_equity_bnb=0.2,
            fixed_stake_bnb=0.1,
            max_open_positions=1,
        )
        self.assertEqual(result["trade_count"], 2)
        self.assertEqual(result["capacity_skips"], 1)
        self.assertAlmostEqual(result["net_profit_bnb"], 0.05)


if __name__ == "__main__":
    unittest.main()

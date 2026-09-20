import unittest

from src.pipeline.leader_follow_strategy import (
    LeaderFollowConfig,
    build_leader_follow_candidates,
    simulate_leader_follow,
)


def _lifecycle(token, create, leader, *, price_end=2.0):
    buys = [
        {"timestamp": create, "account": leader, "token_amount": 100.0, "bnb_amount": 0.1, "price": 1.0},
        {"timestamp": create + 5, "account": "buyer", "token_amount": 50.0, "bnb_amount": 0.05, "price": 1.1},
    ]
    return {
        "token_address": token,
        "name": token,
        "symbol": token,
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
        + [{"timestamp": create + 3_600, "price": price_end, "type": "dex_close", "dex_volume": 10.0}],
        "last_update": create + 3_600,
    }


class LeaderFollowStrategyTests(unittest.TestCase):
    def test_prior_early_wallet_becomes_leader_without_future_labels(self):
        lifecycles = [
            _lifecycle("0xold1", 1_700_000_000, "0xleader"),
            _lifecycle("0xold2", 1_700_001_000, "0xleader"),
            _lifecycle("0xnew", 1_700_002_000, "0xleader"),
        ]
        candidates = build_leader_follow_candidates(
            lifecycles,
            LeaderFollowConfig(min_prior_tokens=2, early_window_seconds=60, min_early_buyers=2),
        )
        self.assertEqual([row["token"] for row in candidates], ["0xnew"])
        self.assertEqual(candidates[0]["leaders"][0]["prior_token_count"], 2)

    def test_follower_delay_is_applied_to_replay(self):
        lifecycle = _lifecycle("0xnew", 1_700_002_000, "0xleader")
        candidate = {
            "token": "0xnew",
            "sample_time": 1_700_002_000,
            "path": [(1_700_002_000.0, 1.0, "buy"), (1_700_005_600.0, 2.0, "dex_close")],
            "leaders": [{"prior_token_count": 2}],
        }
        outcome = simulate_leader_follow(
            candidate,
            horizon_seconds=3_600,
            config=LeaderFollowConfig(
                follower_delay_seconds=10,
                replay=LeaderFollowConfig.__dataclass_fields__["replay"].default,
            ),
        )
        self.assertEqual(outcome["follower_delay_seconds"], 10)
        self.assertEqual(outcome["leader_max_prior_tokens"], 2)


if __name__ == "__main__":
    unittest.main()

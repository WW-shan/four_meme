import unittest

from src.walletflow.pipeline import WalletEvent, WalletFlow
from src.walletflow.scoring import FirstTouchEvent, compute_scout_score, is_funding_confirmation

TOKEN = "0x" + "11" * 20


def events(wallet, n, swarm):
    return [FirstTouchEvent(wallet, f"{TOKEN}{i}", float(i), followers_60m=(3 if i < swarm else 0))
            for i in range(n)]


class ScoutScoreTests(unittest.TestCase):
    def test_insufficient_sample_is_never_confirmed(self):
        score = compute_scout_score("w", events("w", 10, 9))
        self.assertEqual("insufficient", score.tier)
        self.assertFalse(score.confirmed)

    def test_swarm_rate_tiers(self):
        score = compute_scout_score("w", events("w", 40, 24))
        self.assertEqual(40, score.n_first_touches)
        self.assertAlmostEqual(0.6, score.swarm_rate)
        self.assertEqual("S", score.tier)
        self.assertTrue(score.confirmed)

    def test_funding_confirmation_requires_swarm_and_sample(self):
        score = compute_scout_score("w", events("w", 40, 24))
        self.assertTrue(is_funding_confirmation(score, FirstTouchEvent("w", TOKEN, 1.0, followers_60m=3)))
        self.assertFalse(is_funding_confirmation(score, FirstTouchEvent("w", TOKEN, 1.0, followers_60m=1)))

    def test_stability_requires_two_windows(self):
        self.assertIsNone(compute_scout_score("w", events("w", 40, 24)).stability_delta)
        self.assertIsNotNone(compute_scout_score("w", events("w", 80, 48)).stability_delta)


class WalletFlowTests(unittest.TestCase):
    def test_net_inflow_and_ratio(self):
        flow = WalletFlow()
        flow.extend([
            WalletEvent("a", TOKEN, "buy", 2.0, 1.0),
            WalletEvent("b", TOKEN, "buy", 1.0, 2.0),
            WalletEvent("c", TOKEN, "sell", 0.5, 3.0),
        ])
        self.assertAlmostEqual(2.5, flow.net_inflow(TOKEN))
        self.assertAlmostEqual(6.0, flow.buy_sell_ratio(TOKEN))
        self.assertTrue(flow.funding_confirmed(TOKEN))
        self.assertFalse(flow.funding_confirmed(TOKEN, minimum_net_inflow=10))

    def test_deployer_reputation(self):
        flow = WalletFlow()
        flow.extend([
            WalletEvent("dev", "t1", "buy", 1, 1, label="deployer"),
            WalletEvent("dev", "t2", "buy", 1, 2, label="deployer"),
            WalletEvent("dev", "t3", "buy", 1, 3, label="deployer"),
            WalletEvent("dev", "t4", "buy", 1, 4, label="deployer"),
        ])
        reputation = flow.deployer_reputation("dev", rug_tokens=["t1"])
        self.assertEqual(4, reputation["launches"])
        self.assertAlmostEqual(0.25, reputation["rug_rate"])

    def test_bundle_cohort_counts_early_wallets_and_current_holdings(self):
        flow = WalletFlow()
        flow.extend([
            WalletEvent("a", TOKEN, "buy", 1, 100.0),
            WalletEvent("b", TOKEN, "buy", 1, 101.0),
            WalletEvent("c", TOKEN, "buy", 1, 120.0),
        ])
        cohort = flow.bundle_cohort(TOKEN, current_held_pct={"a": 10.0, "b": 5.0, "c": 1.0})
        self.assertEqual(2, cohort["wallet_count"])
        self.assertEqual(15.0, cohort["current_held_pct"])

    def test_invalid_side_rejected(self):
        with self.assertRaises(ValueError):
            WalletEvent("a", TOKEN, "hold", 1.0, 1.0)


if __name__ == "__main__":
    unittest.main()

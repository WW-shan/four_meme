import unittest

from config.scanner_config import FilterThresholds
from src.decision.engine import PortfolioState, RiskBudget, decide, risk_allows
from src.safety.orchestrator import build_report

GOOD = {
    "liquidity_usd": 25_000, "dev_holding_pct": 0.5, "buy_tax_pct": 1.0, "sell_tax_pct": 1.0,
    "trades_recent": 42, "mcap_usd": 45_000, "mint_authority": None, "freeze_authority": None,
    "owner_renounced": True, "blacklist": False, "pausable": False, "top1_pct": 8.0,
    "top10_pct": 22.0, "non_lp_max_pct": 5.0, "lp_burned": True, "lp_locked_pct": None,
    "honeypot_sim": True, "bundle_current_held_pct": 5.0, "bundle_wallet_count": 2,
    "bundle_total_pct": 8.0, "deployer_rug_rate": 0.1, "early_sniper_count": 3,
}
TOKEN = "0x" + "11" * 20


class DecisionEngineTests(unittest.TestCase):
    def setUp(self):
        self.report = build_report(TOKEN, GOOD, FilterThresholds(), mode="safe")
        self.budget = RiskBudget()

    def decide(self, **kwargs):
        defaults = dict(
            token=TOKEN, report=self.report, funding_confirmed=True, mcap_usd=45_000,
            mcap_min_usd=10_000, mcap_max_usd=500_000, budget=self.budget,
            state=PortfolioState(), mode="shadow", now=1000.0, ttl_seconds=45,
        )
        defaults.update(kwargs)
        return decide(**defaults)

    def test_buy_when_all_gates_pass(self):
        decision = self.decide()
        self.assertEqual("buy", decision.action)
        self.assertEqual("shadow", decision.mode)
        self.assertEqual(1.0, decision.size_quote)
        self.assertFalse(decision.expired(now=1040))

    def test_reject_reasons_are_explicit(self):
        self.assertEqual("watch", self.decide(funding_confirmed=False).action)
        self.assertEqual("reject", self.decide(mcap_usd=None).action)
        self.assertEqual("reject", self.decide(mcap_usd=900_000).action)
        self.assertEqual("reject", self.decide(state=PortfolioState(open_positions=10)).action)

    def test_expiry(self):
        decision = self.decide()
        self.assertTrue(decision.expired(now=1045))

    def test_risk_circuits(self):
        self.assertFalse(risk_allows(self.budget, PortfolioState(daily_loss_pct=0.2))[0])
        self.assertFalse(risk_allows(self.budget, PortfolioState(weekly_loss_pct=0.3))[0])
        self.assertFalse(risk_allows(self.budget, PortfolioState(consecutive_losses=20))[0])
        self.assertFalse(risk_allows(self.budget, PortfolioState(new_positions_last_minute=3))[0])

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            self.decide(mode="fast")


if __name__ == "__main__":
    unittest.main()


class LearningModeDecisionTests(unittest.TestCase):
    """A learning-mode report only blocks on honeypot_sim, so it must never authorise a buy."""

    def _report(self, mode):
        # Only honeypot_sim is known; every other critical field is unknown.
        return build_report(TOKEN, {"honeypot_sim": True}, FilterThresholds(), mode=mode)

    def test_learning_report_cannot_produce_a_buy(self):
        report = self._report("learning")
        self.assertEqual("pass", report.verdict)  # learning mode is lax by design
        decision = decide(token=TOKEN, report=report, funding_confirmed=True, mcap_usd=45_000,
                          mcap_min_usd=20_000, mcap_max_usd=120_000,
                          budget=RiskBudget(), state=PortfolioState())
        self.assertEqual("reject", decision.action)
        self.assertIn("safety_mode_not_safe", decision.reason_codes)
        self.assertEqual(0.0, decision.size_quote)

    def test_safe_report_is_unaffected(self):
        report = self._report("safe")
        self.assertEqual("reject", report.verdict)  # unknown critical fields fail closed
        decision = decide(token=TOKEN, report=report, funding_confirmed=True, mcap_usd=45_000,
                          mcap_min_usd=20_000, mcap_max_usd=120_000,
                          budget=RiskBudget(), state=PortfolioState())
        self.assertEqual("reject", decision.action)
        self.assertIn("safety_reject", decision.reason_codes)
        self.assertNotIn("safety_mode_not_safe", decision.reason_codes)

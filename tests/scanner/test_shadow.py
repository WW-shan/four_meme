import unittest

from src.shadow.exits import DrawdownCircuit, ExitRuleConfig, ExitState, evaluate_exit
from src.shadow.executor import shadow_buy, shadow_sell


class ShadowExecutorTests(unittest.TestCase):
    def test_buy_applies_slippage_and_fee(self):
        fill = shadow_buy("0xToken", 1.0, 1.0, slippage_pct=2.0, fee_pct=1.0, at=100.0)
        self.assertAlmostEqual(1.02, fill.price)
        self.assertAlmostEqual(0.01, fill.fee_quote)
        self.assertAlmostEqual(0.99 / 1.02, fill.token_amount)

    def test_sell_applies_slippage_and_fee(self):
        fill = shadow_sell("0xToken", 1.0, 1.0, slippage_pct=2.0, fee_pct=1.0, at=200.0)
        self.assertAlmostEqual(0.98, fill.price)
        self.assertAlmostEqual(0.0098, fill.fee_quote)
        self.assertAlmostEqual(0.9702, fill.quote_amount)

    def test_invalid_inputs_rejected(self):
        with self.assertRaises(ValueError):
            shadow_buy("0xToken", 0.0, 1.0, at=1.0)
        with self.assertRaises(ValueError):
            shadow_sell("0xToken", 1.0, 0.0, at=1.0)
        with self.assertRaises(ValueError):
            shadow_sell("0xToken", 1.0, 1.0, slippage_pct=100.0, at=1.0)


class ExitEngineTests(unittest.TestCase):
    def state(self, **kwargs):
        defaults = dict(entry_price=1.0, opened_at=0.0, peak_price=1.0)
        defaults.update(kwargs)
        return ExitState(**defaults)

    def test_take_profit_ladder_partial(self):
        action = evaluate_exit(self.state(), 1.5, now=10.0)
        self.assertEqual("take_profit_1", action.reason)
        self.assertAlmostEqual(0.25, action.fraction)

    def test_stop_loss_full_exit(self):
        action = evaluate_exit(self.state(), 0.59, now=10.0)
        self.assertEqual("stop_loss", action.reason)
        self.assertAlmostEqual(1.0, action.fraction)

    def test_trailing_stop_after_peak(self):
        state = self.state(peak_price=3.2)
        action = evaluate_exit(state, 2.2, now=10.0)
        self.assertEqual("trailing_stop", action.reason)

    def test_time_exit(self):
        action = evaluate_exit(self.state(), 1.1, now=2000.0)
        self.assertEqual("time_exit", action.reason)

    def test_rug_liquidity_drop_wins(self):
        state = self.state(baseline_liquidity=100_000.0)
        action = evaluate_exit(state, 0.9, now=10.0, liquidity_usd=60_000.0)
        self.assertEqual("rug_liquidity_drop", action.reason)
        self.assertAlmostEqual(1.0, action.fraction)

    def test_disabled_after_full_exit(self):
        state = self.state()
        state.remaining_fraction = 0.0
        self.assertIsNone(evaluate_exit(state, 5.0, now=10.0))

    def test_drawdown_circuit(self):
        circuit = DrawdownCircuit(daily_loss_pct=0.50, weekly_loss_pct=0.90, consecutive_losses_halt=3)
        self.assertEqual((True, None), circuit.can_open())
        for _ in range(3):
            circuit.record(-0.01)
        self.assertEqual((False, "consecutive_losses"), circuit.can_open())
        circuit.record(0.01)
        self.assertEqual((True, None), circuit.can_open())
        circuit.record(-0.60)
        self.assertEqual((False, "daily_drawdown"), circuit.can_open())
        circuit.reset_day()
        self.assertEqual((True, None), circuit.can_open())


if __name__ == "__main__":
    unittest.main()

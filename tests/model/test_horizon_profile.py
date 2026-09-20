import unittest

from src.pipeline.horizon_profile import distribution, summarize_horizon


def _sample(token, sample_time, horizon, final_return, robust_return, *, target=0, stop=0):
    return {
        "features": {"current_price": 1.0},
        "label": {
            "future_window_seconds": horizon,
            "live_cost_adjusted_final_return_pct": final_return,
            "live_delay_robust_return_pct": robust_return,
            "live_executable_return_pct": max(final_return, robust_return),
            "live_risk_adjusted_return_pct": robust_return,
            "live_cost_adjusted_min_return_pct": min(final_return, robust_return),
            "live_cost_adjusted_max_return_pct": max(final_return, robust_return),
            "cost_adjusted_max_return_pct": max(final_return, robust_return),
            "live_target_hit_before_stop": target,
            "live_stop_hit_before_target": stop,
            "live_entry_available": 1,
            "live_entry_blocked_by_price_protection": 0,
            "live_time_to_target_seconds": 20 if target else 0,
            "live_time_to_stop_seconds": 20 if stop else 0,
        },
        "meta": {"token_address": token, "sample_time": sample_time},
    }


class HorizonProfileTests(unittest.TestCase):
    def test_distribution_is_finite_and_reports_positive_rate(self):
        result = distribution([1, 2, float("nan"), float("inf"), -1])
        self.assertEqual(result["count"], 5)
        self.assertAlmostEqual(result["positive_rate"], 0.4)
        self.assertTrue(result["mean"] < 1.0)

    def test_summary_uses_latest_sample_per_token_and_rejects_weak_horizon(self):
        rows = [
            _sample("0xa", 10, 300, 4, 2, target=1),
            _sample("0xa", 20, 300, -3, -4, stop=1),
            _sample("0xb", 10, 300, 2, -1),
            _sample("0xc", 10, 1800, 9, 8, target=1),
        ]
        result = summarize_horizon(rows, 300, baseline_tokens={"0xa", "0xb", "0xc"}, minimum_token_support=3)
        self.assertEqual(result["support"]["sample_count"], 3)
        self.assertEqual(result["support"]["token_count"], 2)
        self.assertEqual(result["support"]["latest_token_count"], 2)
        self.assertEqual(result["decision"]["research_candidate"], False)
        self.assertIn("insufficient_token_support", result["decision"]["rejection_reasons"])
        self.assertIn("nonpositive_delay_robust_median", result["decision"]["rejection_reasons"])
        self.assertAlmostEqual(result["path"]["target_hit_before_stop_rate"], 1 / 3)
        self.assertEqual(result["latest_token_returns"]["live_cost_adjusted_final_return_pct"]["count"], 2)

    def test_summary_marks_positive_horizon_when_support_and_returns_pass(self):
        rows = [
            _sample("0xa", 10, 300, 5, 4, target=1),
            _sample("0xb", 10, 300, 6, 5, target=1),
            _sample("0xc", 10, 300, 7, 6, target=1),
        ]
        result = summarize_horizon(rows, 300, baseline_tokens={"0xa", "0xb", "0xc"}, minimum_token_support=3)
        self.assertTrue(result["decision"]["research_candidate"])
        self.assertEqual(result["decision"]["rejection_reasons"], [])
        self.assertGreater(result["returns"]["live_delay_robust_return_pct"]["median"], 0)

    def test_empty_baseline_reports_zero_survival_for_empty_horizon(self):
        result = summarize_horizon([], 86400, baseline_tokens=set(), minimum_token_support=1)
        self.assertEqual(result["support"]["token_survival_vs_shortest"], 0.0)


if __name__ == "__main__":
    unittest.main()

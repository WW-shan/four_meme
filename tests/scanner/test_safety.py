import unittest

from config.scanner_config import FilterThresholds, ScannerConfig
from src.safety.orchestrator import build_report

GOOD = {
    "liquidity_usd": 25_000,
    "dev_holding_pct": 0.5,
    "buy_tax_pct": 1.0,
    "sell_tax_pct": 1.0,
    "trades_recent": 42,
    "mcap_usd": 45_000,
    "mint_authority": None,
    "freeze_authority": None,
    "owner_renounced": True,
    "blacklist": False,
    "pausable": False,
    "top1_pct": 8.0,
    "top10_pct": 22.0,
    "non_lp_max_pct": 5.0,
    "lp_burned": True,
    "lp_locked_pct": None,
    "honeypot_sim": True,
    "bundle_current_held_pct": 5.0,
    "bundle_wallet_count": 2,
    "bundle_total_pct": 8.0,
    "deployer_rug_rate": 0.1,
    "early_sniper_count": 3,
}


class SafetyFilterTests(unittest.TestCase):
    def setUp(self):
        self.thresholds = FilterThresholds()

    def test_good_snapshot_passes_in_safe_mode(self):
        report = build_report("0xToken", GOOD, self.thresholds, mode="safe")
        self.assertEqual("pass", report.verdict)
        self.assertGreater(report.score, 0)

    def test_unknown_honeypot_is_fail_closed_in_safe_mode(self):
        snapshot = dict(GOOD)
        snapshot["honeypot_sim"] = None
        report = build_report("0xToken", snapshot, self.thresholds, mode="safe")
        self.assertEqual("reject", report.verdict)
        self.assertIn("honeypot_sim:error", report.reason_codes)

    def test_learning_mode_only_blocks_on_honeypot(self):
        snapshot = dict(GOOD)
        snapshot["liquidity_usd"] = None
        report = build_report("0xToken", snapshot, self.thresholds, mode="learning")
        self.assertEqual("pass", report.verdict)
        snapshot["honeypot_sim"] = None
        report = build_report("0xToken", snapshot, self.thresholds, mode="learning")
        self.assertEqual("reject", report.verdict)

    def test_hard_failures_reject(self):
        critical_cases = {
            "liquidity_usd": 1_000,
            "dev_holding_pct": 5.0,
            "buy_tax_pct": 12.0,
            "trades_recent": 0,
            "top10_pct": 60.0,
            "bundle_current_held_pct": 80.0,
            "honeypot_sim": False,
        }
        for field, value in critical_cases.items():
            snapshot = dict(GOOD)
            snapshot[field] = value
            report = build_report("0xToken", snapshot, self.thresholds, mode="safe")
            self.assertEqual("reject", report.verdict, field)

    def test_non_critical_filters_fail_without_blocking_the_report(self):
        for field, value, filter_id in (
            ("mcap_usd", 900_000, "mcap_band"),
            ("deployer_rug_rate", 0.9, "dev_history"),
            ("early_sniper_count", 50, "early_snipers"),
        ):
            snapshot = dict(GOOD)
            snapshot[field] = value
            report = build_report("0xToken", snapshot, self.thresholds, mode="safe")
            self.assertEqual("pass", report.verdict, field)
            statuses = {result.filter_id: result.status for result in report.results}
            self.assertEqual("fail", statuses[filter_id], field)

    def test_unknown_critical_field_is_error(self):
        for field in ("liquidity_usd", "dev_holding_pct", "taxes"):
            snapshot = dict(GOOD)
            if field == "taxes":
                snapshot["buy_tax_pct"] = None
            else:
                snapshot[field] = None
            report = build_report("0xToken", snapshot, self.thresholds, mode="safe")
            self.assertEqual("reject", report.verdict, field)

    def test_social_never_blocks(self):
        snapshot = dict(GOOD)
        snapshot["x_mentions_1h"] = None
        report = build_report("0xToken", snapshot, self.thresholds, mode="safe")
        self.assertEqual("pass", report.verdict)


class ScannerConfigTests(unittest.TestCase):
    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            ScannerConfig(mode="fast")

    def test_round_trip_dict(self):
        config = ScannerConfig(mode="safe", deep_audit_per_round=3)
        payload = config.to_dict()
        self.assertEqual("safe", payload["mode"])
        self.assertEqual(3, payload["deep_audit_per_round"])
        self.assertIn("liquidity_min_usd", payload["thresholds"])


if __name__ == "__main__":
    unittest.main()

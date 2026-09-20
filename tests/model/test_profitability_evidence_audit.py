import unittest

from src.pipeline.profitability_evidence_audit import NATIVE_QUOTE, USDT, audit_profitability_inputs


TOKEN = "0x" + "12" * 20


def _inputs(quote=USDT, near=-0.5):
    report = {"rows": [{"token": TOKEN, "symbol": "test", "crossing_time": 100, "confirmation_time": 130,
                         "entry_time_causal": 133, "confirmation_price": 0.00003,
                         "confirmation_mcap_usd": 22_025_100, "h3600_strict_return": near}]}
    lifecycle = {"token_address": TOKEN, "total_supply": 10**27, "graduate_time": 132,
                 "buys": [{"timestamp": 100, "account": TOKEN, "bnb_amount": 0.0001}], "sells": []}
    metadata = {"block_number": 999, "rows": [{"token": TOKEN, "info": {"base": TOKEN, "quote": quote, "totalSupply": 10**27}}]}
    assets = {"rows": [{"token": TOKEN, "method": "decimals()", "value": 18},
                       {"token": quote, "method": "decimals()", "value": 18}]}
    return report, [lifecycle], metadata, assets


class ProfitabilityEvidenceAuditTests(unittest.TestCase):
    def test_usdt_amount_never_becomes_bnb_or_exact_usd(self):
        audited = audit_profitability_inputs(*_inputs())
        row = audited["rows"][0]
        self.assertEqual(row["fdv_proxy_quote_units"], 30_000)
        self.assertFalse(row["native_quote_supported"])
        self.assertIsNone(row["true_usd_fdv"])
        self.assertIn("usdt_fdv_proxy_below_50000_quote_units", row["audit_flags"])
        self.assertEqual(row["sub_one_usdt_buy_count"], 1)

    def test_nearby_trade_does_not_prove_execution_and_missing_trade_does_not_remove_row(self):
        for near in [None, -0.5, 2.0]:
            audited = audit_profitability_inputs(*_inputs(NATIVE_QUOTE, near))
            self.assertEqual(audited["summary"]["candidate_count"], 1)
            self.assertEqual(audited["rows"][0]["actual_execution_status"], "unknown")
            self.assertIsNone(audited["rows"][0]["actual_pnl_bnb"])
            self.assertFalse(audited["model_selection_eligible"])

    def test_missing_or_mismatched_metadata_is_unknown_not_assumed_native(self):
        for info in [{}, {"base": "0x" + "34" * 20, "quote": NATIVE_QUOTE}]:
            inputs = _inputs()
            inputs[2]["rows"][0]["info"] = info
            row = audit_profitability_inputs(*inputs)["rows"][0]
            self.assertIsNone(row["native_quote_supported"])
            self.assertIn("unknown_quote", row["audit_flags"])
            self.assertIsNone(row["fdv_proxy_quote_units"])

    def test_later_snapshot_and_ended_venue_cannot_be_historical_execution_evidence(self):
        row = audit_profitability_inputs(*_inputs(NATIVE_QUOTE))["rows"][0]
        self.assertFalse(row["historical_quote_verified"])
        self.assertTrue(row["curve_venue_ended_at_entry"])
        self.assertIn("requires_dex_state_for_1h_outcome", row["audit_flags"])

    def test_six_decimal_quote_corrects_raw_division_without_changing_base_units(self):
        inputs = _inputs()
        inputs[0]["rows"][0]["confirmation_price"] = 3e-17
        inputs[3]["rows"][1]["value"] = 6
        row = audit_profitability_inputs(*inputs)["rows"][0]
        self.assertEqual(row["fdv_proxy_quote_units"], 30_000)

    def test_invalid_decimals_do_not_get_silently_rounded(self):
        inputs = _inputs()
        inputs[3]["rows"][1]["value"] = 6.5
        row = audit_profitability_inputs(*inputs)["rows"][0]
        self.assertIsNone(row["quote_decimals"])
        self.assertIsNone(row["fdv_proxy_quote_units"])

    def test_iso_graduation_is_recognized_and_missing_graduation_stays_unknown(self):
        inputs = _inputs(NATIVE_QUOTE)
        inputs[1][0]["graduate_time"] = "1970-01-01T00:02:12+00:00"
        self.assertTrue(audit_profitability_inputs(*inputs)["rows"][0]["curve_venue_ended_at_entry"])
        inputs[1][0]["graduate_time"] = None
        self.assertIsNone(audit_profitability_inputs(*inputs)["rows"][0]["curve_venue_ended_at_entry"])

    def test_unknown_buy_amounts_remain_in_tiny_buy_denominator(self):
        inputs = _inputs()
        inputs[1][0]["buys"] = [{"timestamp": 100, "bnb_amount": .0001} for _ in range(8)] + [{"timestamp": 100}] * 2
        row = audit_profitability_inputs(*inputs)["rows"][0]
        self.assertEqual(row["sub_one_usdt_buy_count"], 8)
        self.assertNotIn("at_least_90pct_buys_below_one_usdt", row["audit_flags"])

    def test_invalid_candidate_is_counted_and_flagged(self):
        inputs = _inputs()
        inputs[0]["rows"].append(None)
        audit = audit_profitability_inputs(*inputs)
        self.assertEqual(audit["summary"]["candidate_count"], 2)
        self.assertEqual(audit["summary"]["audit_flag_counts"]["invalid_candidate_row"], 1)


if __name__ == "__main__":
    unittest.main()

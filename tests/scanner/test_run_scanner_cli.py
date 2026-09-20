import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_scanner import main


def _run(argv):
    with contextlib.redirect_stdout(io.StringIO()):
        return main(argv)

GOOD = {
    "token": "0x" + "11" * 20, "liquidity_usd": 25_000, "dev_holding_pct": 0.5,
    "buy_tax_pct": 1.0, "sell_tax_pct": 1.0, "trades_recent": 42, "mcap_usd": 45_000,
    "mint_authority": None, "freeze_authority": None, "owner_renounced": True,
    "blacklist": False, "pausable": False, "top1_pct": 8.0, "top10_pct": 22.0,
    "non_lp_max_pct": 5.0, "lp_burned": True, "lp_locked_pct": None,
    "honeypot_sim": True, "bundle_current_held_pct": 5.0, "bundle_wallet_count": 2,
    "bundle_total_pct": 8.0, "deployer_rug_rate": 0.1, "early_sniper_count": 3,
}


class ScannerCliTests(unittest.TestCase):
    def test_audit_pass_and_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(json.dumps(GOOD), encoding="utf-8")
            self.assertEqual(0, _run(["audit", "--snapshot", str(path), "--mode", "safe"]))
            GOOD["honeypot_sim"] = False
            path.write_text(json.dumps(GOOD), encoding="utf-8")
            self.assertEqual(2, _run(["audit", "--snapshot", str(path), "--mode", "safe"]))
            GOOD["honeypot_sim"] = True

    def test_shadow_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenario.json"
            path.write_text(json.dumps({"quote_amount": 1.0, "buy_price": 1.0, "sell_price": 2.0}), encoding="utf-8")
            self.assertEqual(0, _run(["shadow", "--scenario", str(path)]))

    def test_gate_command(self):
        records = [{"pnl_quote": 1.0, "quote_amount": 100.0, "token": f"t{i}", "latency_seconds": 0.2}
                   for i in range(60)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.json"
            path.write_text(json.dumps(records), encoding="utf-8")
            self.assertEqual(0, _run(["gate", "--records", str(path)]))
            path.write_text(json.dumps(records[:5]), encoding="utf-8")
            self.assertEqual(2, _run(["gate", "--records", str(path)]))

    def test_radar_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            path.write_text(json.dumps({
                "event_name": "TokenCreate",
                "data": {"args": {"token": "0x" + "11" * 20, "creator": "0x" + "22" * 20},
                         "timestamp": 1000, "received_at": 1001},
            }), encoding="utf-8")
            self.assertEqual(0, _run(["radar", "--event", str(path)]))


if __name__ == "__main__":
    unittest.main()

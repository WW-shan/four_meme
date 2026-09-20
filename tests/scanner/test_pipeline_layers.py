import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen

from src.decision.live_gate import LiveGateConfig, LiveGateResult, evaluate_live_gate
from src.radar.api import make_scanner_server
from src.radar.solana import launch_from_solana_event
from src.radar.store import ScannerStore
from src.shadow.executor import shadow_buy
from src.shadow.report import ShadowGateConfig, build_gate_report
from src.shadow.tracker import ShadowTracker
from src.walletflow.gmgn import GmgnWalletClient, parse_trade_rows
from src.walletflow.registry import WalletRegistry

SOL_MINT = "So11111111111111111111111111111111111111112"


class WalletRegistryTests(unittest.TestCase):
    def test_upsert_and_label(self):
        registry = WalletRegistry()
        record = registry.upsert("bsc", "0x" + "AB" * 20, label="kol", source="gmgn")
        self.assertEqual("0x" + "ab" * 20, record.wallet)
        self.assertEqual(1, len(registry.by_label("kol")))
        with self.assertRaises(ValueError):
            registry.upsert("bsc", "0x" + "11" * 20, label="whale")


class GmgnAdapterTests(unittest.TestCase):
    def test_parse_and_client(self):
        rows = [
            {"maker": "w1", "base_address": "t1", "side": "buy", "amount_usd": 120, "timestamp": 10},
            {"maker": "w2", "base_address": "t2", "side": "sell", "amount_usd": 50, "timestamp": 11},
            {"maker": "", "base_address": "t3", "side": "buy", "amount_usd": 1, "timestamp": 12},
        ]
        events = parse_trade_rows(rows)
        self.assertEqual(2, len(events))
        self.assertEqual("buy", events[0].side)

        class FakeClient:
            def wallet_trades(self, address, limit=100):
                return {"data": {"list": rows}}

        client = GmgnWalletClient(FakeClient())
        self.assertEqual(2, len(client.wallet_trades("w1")))


class ShadowTrackerTests(unittest.TestCase):
    def test_take_profit_and_close(self):
        tracker = ShadowTracker()
        fill = shadow_buy("t", 100, 1.0, slippage_pct=0, fee_pct=0, at=0)
        tracker.open(fill, baseline_liquidity=100_000)
        action = tracker.update("t", 1.5, now=10)
        self.assertEqual("take_profit_1", action.reason)
        self.assertAlmostEqual(0.75, tracker.exit_states["t"].remaining_fraction)
        for price, now in ((2.0, 20), (4.0, 30), (10.0, 40)):
            tracker.update("t", price, now=now)
        self.assertLessEqual(tracker.exit_states["t"].remaining_fraction, 0.1)

    def test_stop_loss_closes(self):
        tracker = ShadowTracker()
        fill = shadow_buy("t", 100, 1.0, slippage_pct=0, fee_pct=0, at=0)
        tracker.open(fill)
        action = tracker.update("t", 0.5, now=10)
        self.assertEqual("stop_loss", action.reason)
        self.assertAlmostEqual(0.0, tracker.exit_states["t"].remaining_fraction)

    def test_rug_liquidity_drop(self):
        tracker = ShadowTracker()
        fill = shadow_buy("t", 100, 1.0, slippage_pct=0, fee_pct=0, at=0)
        tracker.open(fill, baseline_liquidity=100_000)
        action = tracker.update("t", 1.0, now=5, liquidity_usd=50_000)
        self.assertEqual("rug_liquidity_drop", action.reason)


class GateReportTests(unittest.TestCase):
    def records(self, n, pnl=1.0, token=None, latency=0.1):
        return [
            {"pnl_quote": pnl, "quote_amount": 100.0,
             "token": token if token is not None else f"t{index}",
             "latency_seconds": latency}
            for index in range(n)
        ]

    def test_pass_and_reject(self):
        report = build_gate_report(self.records(60, pnl=1.0, latency=0.2))
        self.assertEqual("pass", report.verdict)
        rejected = build_gate_report(self.records(10, pnl=-1.0))
        self.assertEqual("reject", rejected.verdict)
        self.assertIn("insufficient_trades", rejected.reason_codes)
        dependency = build_gate_report(self.records(60, pnl=1.0, token="same"))
        self.assertIn("top_token_dependency", dependency.reason_codes)

    def test_live_gate_blocks_by_default(self):
        report = build_gate_report(self.records(60, pnl=1.0, latency=0.2))
        result = evaluate_live_gate(LiveGateConfig(), report)
        self.assertFalse(result.allowed)
        self.assertIn("live_disabled", result.reason_codes)
        enabled = evaluate_live_gate(LiveGateConfig(enabled=True, operator_confirmed=True), report)
        self.assertTrue(enabled.allowed)


class SolanaAdapterTests(unittest.TestCase):
    def test_normalizes_mint_and_creator(self):
        launch = launch_from_solana_event({
            "mint": SOL_MINT, "creator": SOL_MINT, "quote_mint": SOL_MINT,
            "chain_time": 5, "received_at": 6, "signature": "sig", "source_event": "PumpFunCreate",
        })
        self.assertEqual(SOL_MINT, launch.mint)
        self.assertEqual("sol", launch.chain)

    def test_rejects_invalid_mint(self):
        self.assertIsNone(launch_from_solana_event({"mint": "not-a-mint"}))


class ScannerApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ScannerStore(Path(self.temp.name) / "scanner.sqlite")
        self.store.append("launch", "bsc:t", {"token": "t"}, 100.0, 101.0)
        self.server = make_scanner_server(self.store, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_summary_and_launches(self):
        import json
        with urlopen(self.url + "/api/v1/scanner/summary") as response:
            summary = json.load(response)
        self.assertEqual(1, summary["launches"])
        self.assertFalse(summary["trading_enabled"])
        with urlopen(self.url + "/api/v1/scanner/launches") as response:
            launches = json.load(response)
        self.assertEqual(1, launches["count"])

    def test_unknown_route(self):
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + "/api/v1/scanner/nope")
        self.assertEqual(404, error.exception.code)


if __name__ == "__main__":
    unittest.main()

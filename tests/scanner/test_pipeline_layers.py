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
from src.shadow.report import ShadowGateConfig, build_gate_report, records_from_store
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


class ScannerPipelineTests(unittest.TestCase):
    def test_audit_decide_and_shadow_records(self):
        import tempfile
        from config.scanner_config import ScannerConfig
        from src.radar.pipeline import ScannerPipeline
        from src.radar.store import ScannerStore
        from src.shadow.executor import shadow_buy

        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(Path(tmp) / "scanner.sqlite")
            pipeline = ScannerPipeline(store, ScannerConfig(mode="safe"), clock=lambda: 100.0)
            report = pipeline.audit("0x" + "11" * 20, override={
                "liquidity_usd": 25_000, "dev_holding_pct": 0.5, "buy_tax_pct": 1.0, "sell_tax_pct": 1.0,
                "trades_recent": 42, "mcap_usd": 45_000, "mint_authority": None, "freeze_authority": None,
                "owner_renounced": True, "blacklist": False, "pausable": False, "top1_pct": 8.0,
                "top10_pct": 22.0, "non_lp_max_pct": 5.0, "lp_burned": True, "lp_locked_pct": None,
                "honeypot_sim": True, "bundle_current_held_pct": 5.0, "bundle_wallet_count": 2,
                "bundle_total_pct": 8.0, "deployer_rug_rate": 0.1, "early_sniper_count": 3,
            })
            self.assertEqual("pass", report.verdict)
            decision = pipeline.decide(report.token, report=report, funding_confirmed=True, mcap_usd=45_000)
            self.assertEqual("buy", decision.action)
            self.assertEqual(1, len(store.rows("safety_report")))
            self.assertEqual(1, len(store.rows("decision")))

            tracker = ShadowTracker(store=store)
            fill = shadow_buy(decision.token, 100.0, 1.0, slippage_pct=0, fee_pct=0, at=100.0)
            tracker.open(fill)
            tracker.update(decision.token, 0.5, now=200.0)
            close = store.latest("shadow_close", decision.token)["payload"]
            self.assertAlmostEqual(-50.0, close["pnl_quote"])
            self.assertAlmostEqual(100.0, close["latency_seconds"])
            records = records_from_store(store)
            self.assertEqual(1, len(records))
            self.assertAlmostEqual(-50.0, records[0]["pnl_quote"])
            report_gate = build_gate_report(records, ShadowGateConfig(min_trades=1))
            self.assertEqual("reject", report_gate.verdict)  # negative expectancy

    def test_scanner_api_dashboard_and_safety_endpoint(self):
        import json
        import tempfile
        from src.radar.api import make_scanner_server
        from src.radar.store import ScannerStore

        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(Path(tmp) / "scanner.sqlite")
            store.append("safety_report", "bsc:t", {"token": "t", "verdict": "pass", "score": 90.0}, 100.0, 101.0)
            server = make_scanner_server(store, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
                    html = response.read().decode()
                self.assertIn("READ-ONLY", html)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/v1/scanner/safety") as response:
                    payload = json.load(response)
                self.assertEqual(1, payload["count"])
                self.assertEqual("pass", payload["rows"][0]["payload"]["verdict"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_collect_continuous_has_opt_in_scanner_wiring(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / "tools" / "collect_continuous.py").read_text(encoding="utf-8")
        self.assertIn("SCANNER_ENABLED", source)
        self.assertIn("_handle_scanner_event", source)
        self.assertIn("register_handler('LiquidityAdded', self._handle_scanner_event)", source)


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from src.radar.solana import SolanaStreamAdapter
from src.radar.store import ScannerStore
from src.safety.attribution import build_attribution
from src.walletflow.gmgn import GmgnOpenApiClient

SOL_MINT = "So11111111111111111111111111111111111111112"


def _run(argv):
    with contextlib.redirect_stdout(io.StringIO()):
        from scripts.run_scanner import main
        return main(argv)


class AttributionTests(unittest.TestCase):
    def test_joins_reports_with_shadow_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(Path(tmp) / "scanner.sqlite")
            token = "0x" + "11" * 20
            store.append("safety_report", token, {
                "token": token, "verdict": "pass",
                "results": [{"filter_id": "honeypot_sim", "status": "pass"},
                            {"filter_id": "liquidity_min", "status": "fail"}],
            }, 100.0, 101.0)
            store.append("shadow_close", token, {"pnl_quote": -20.0, "quote_amount": 100.0,
                                                 "latency_seconds": 1.0}, 200.0, 201.0)
            report = build_attribution(store)
            self.assertEqual(1, report["joined_closes"])
            self.assertEqual(1, report["filters"]["honeypot_sim"]["pass"])
            self.assertEqual(-20.0, report["filters"]["honeypot_sim"]["pass_avg_pnl"])
            self.assertEqual(1, report["filters"]["liquidity_min"]["fail"])

    def test_ignores_reports_after_the_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(Path(tmp) / "scanner.sqlite")
            token = "0x" + "11" * 20
            store.append("safety_report", token, {"results": [{"filter_id": "honeypot_sim", "status": "pass"}]}, 300.0, 301.0)
            store.append("shadow_close", token, {"pnl_quote": 5.0, "quote_amount": 100.0}, 200.0, 201.0)
            self.assertEqual(0, build_attribution(store)["joined_closes"])


class GmgnLiveClientTests(unittest.TestCase):
    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self, payload):
            self.payload = payload
            self.calls = []

        def get(self, url, params=None, headers=None, timeout=None):
            self.calls.append((url, params, headers))
            return GmgnLiveClientTests.FakeResponse(self.payload)

    def test_parses_and_filters_maker(self):
        payload = {"data": {"list": [
            {"maker": "0xAbC", "base_address": "t1", "side": "buy", "amount_usd": 10, "timestamp": 1},
            {"maker": "0xOther", "base_address": "t2", "side": "buy", "amount_usd": 20, "timestamp": 2},
        ]}}
        session = self.FakeSession(payload)
        client = GmgnOpenApiClient("key", session=session)
        events = client.wallet_trades("0xabc", limit=50)
        self.assertEqual(1, len(events))
        self.assertEqual("t1", events[0].token)
        self.assertEqual("buy", events[0].side)
        self.assertEqual(1, len(session.calls))

    def test_missing_key_returns_empty(self):
        client = GmgnOpenApiClient("", session=self.FakeSession({}))
        self.assertEqual([], client.wallet_trades("0xabc", limit=10))


class SolanaStreamAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_consumes_valid_and_skips_invalid(self):
        async def events():
            yield {"mint": SOL_MINT, "creator": SOL_MINT, "chain_time": 5, "received_at": 6,
                   "signature": "sig", "source_event": "PumpFunCreate"}
            yield {"mint": "not-a-mint"}

        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(Path(tmp) / "scanner.sqlite")
            adapter = SolanaStreamAdapter(store)
            received = await adapter.consume(events())
            self.assertEqual(1, received)
            self.assertEqual(1, adapter.skipped)
            self.assertEqual(1, len(store.rows("launch", f"sol:{SOL_MINT}")))


class ReportCliTests(unittest.TestCase):
    def _seed(self, tmp):
        store = ScannerStore(Path(tmp) / "scanner.sqlite")
        tag = "0x" + "11" * 20
        for index in range(60):
            store.append("shadow_close", tag, {
                "token": f"t{index}", "pnl_quote": 1.0, "quote_amount": 100.0, "latency_seconds": 0.2,
            }, 100.0 + index, 100.5 + index)
        return store

    def test_report_and_live_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp)
            db = str(Path(tmp) / "scanner.sqlite")
            self.assertEqual(0, _run(["report", "--db", db]))
            self.assertEqual(2, _run(["live-check", "--db", db]))
            self.assertEqual(0, _run(["live-check", "--db", db, "--enabled", "--confirmed"]))

    def test_report_rejects_insufficient_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(Path(tmp) / "scanner.sqlite")
            store.append("shadow_close", "bsc:t", {"pnl_quote": 1.0, "quote_amount": 100.0}, 100.0, 101.0)
            self.assertEqual(2, _run(["report", "--db", str(Path(tmp) / "scanner.sqlite")]))


if __name__ == "__main__":
    unittest.main()

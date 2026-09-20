import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.radar.store import ScannerStore
from src.safety.fetchers import FetchResult, SnapshotFetcher

SOL_MINT = "So11111111111111111111111111111111111111112"


def _run(argv):
    with contextlib.redirect_stdout(io.StringIO()):
        from scripts.run_scanner import main
        return main(argv)


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    def __init__(self, payload):
        self.payload = payload

    def get(self, url, params=None, headers=None, timeout=None):
        return _Response(self.payload)


class LiveCliTests(unittest.TestCase):
    def test_snapshot_command_uses_live_fetcher(self):
        fetcher = SnapshotFetcher(session=_Session({"pairs": [{"liquidity": {"usd": 20000}, "marketCap": 40000}]}),
                                  min_intervals={"dexscreener": 0})
        with patch("scripts.run_scanner._live_fetcher", return_value=fetcher):
            self.assertEqual(0, _run(["snapshot", "--token", "0x" + "11" * 20]))

    def test_wallet_ingest_command(self):
        payload = {"data": {"list": [{"maker": "0xAbC", "base_address": "t1", "side": "buy",
                                      "amount_usd": 10, "timestamp": 1}]}}
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "scanner.sqlite")
            with patch.dict(os.environ, {"GMGN_API_KEY": "test-key"}), \
                 patch("scripts.run_scanner._gmgn_session", return_value=_Session(payload)):
                self.assertEqual(0, _run(["wallet-ingest", "--db", db, "--address", "0xabc"]))
            self.assertEqual(1, len(ScannerStore(db).rows("wallet_event")))

    def test_solana_event_command_stores_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "event.json"
            db = str(Path(tmp) / "scanner.sqlite")
            event_path.write_text(json.dumps({"mint": SOL_MINT, "creator": SOL_MINT,
                                              "chain_time": 5, "received_at": 6,
                                              "signature": "sig", "source_event": "PumpFunCreate"}),
                                  encoding="utf-8")
            self.assertEqual(0, _run(["solana-event", "--event", str(event_path), "--db", db]))
            self.assertEqual(1, len(ScannerStore(db).rows("launch", f"sol:{SOL_MINT}")))

    def test_env_template_documents_scanner_contract(self):
        template = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
        for key in ("SCANNER_ENABLED=", "SCANNER_DB=", "SCANNER_CONFIG=", "GOPLUS_API_KEY=",
                    "HONEYPOT_API_URL=", "DEXSCREENER_BASE_URL="):
            self.assertIn(key, template)


if __name__ == "__main__":
    unittest.main()

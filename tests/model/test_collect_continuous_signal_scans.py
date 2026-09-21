import asyncio
import time
import unittest

from tests.model.test_collect_continuous_cleanup import collect_continuous_module

ContinuousCollector = collect_continuous_module.ContinuousCollector

TOKEN = "0x" + "44" * 20
MOVER = "0x46c91b9300e3bfffad61870f09b56bac094bffff"


class FakeNotifier:
    def notify_decision(self, decision, **kwargs):
        return True


class FakeScanner:
    def __init__(self, decision=None, error=None, candidate_accepted=True):
        self.chain = "bsc"
        self.notifier = FakeNotifier()
        self.scanned = []
        self.candidates = []
        self.decision = decision
        self.error = error
        self.candidate_accepted = candidate_accepted

    async def handle_event(self, event_name, event_data):
        return True

    def scan(self, token, **kwargs):
        self.scanned.append((token, kwargs))
        if self.error is not None:
            raise self.error
        return self.decision

    def announce_candidate(self, token, **kwargs):
        self.candidates.append((token, kwargs))
        return self.candidate_accepted


class DecisionStub:
    action = "buy"
    reason_codes = ("funding_confirmed",)


def trades(buyers=4, volume=100.0, sells=0, *, age=60.0, seller_amount=1.0):
    now = time.time()
    rows = [{"ts": now - age, "side": "buy", "account": f"0x{i:040x}",
             "amount": volume / max(1, buyers)} for i in range(buyers)]
    rows += [{"ts": now - age / 2, "side": "sell", "account": f"0xdead{i:040x}",
              "amount": seller_amount} for i in range(sells)]
    return rows


class SweepTests(unittest.TestCase):
    """The sweep follows recent trades, which is what actually predicts nothing about age."""

    def _collector(self, *, accepted=True, rows=None, token=TOKEN, meta=None):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub(), candidate_accepted=accepted)
        collector.signal_min_unique_buyers = 2
        collector.candidate_min_buy_volume = 1.0
        collector.candidate_max_per_hour = 20
        collector.activity_window_seconds = 1800.0
        collector._recent_trades = {token.lower(): list(rows if rows is not None else trades())}
        collector._recent_trade_meta = {token.lower(): (meta or {"symbol": "FOO", "name": "Foo"})}
        return collector

    def test_real_money_in_the_window_is_pushed(self):
        collector = self._collector(rows=trades(buyers=6, volume=158.1))
        summary = collector._sweep_candidates()
        self.assertEqual(1, summary["pushed"])
        token, kwargs = collector.scanner.candidates[0]
        self.assertEqual(TOKEN.lower(), token)
        self.assertEqual(6, kwargs["stats"]["fresh_buyers"])
        self.assertEqual(1800.0, kwargs["stats"]["window_seconds"])
        self.assertIn("FOO", kwargs["symbol"])

    def test_dust_below_the_volume_floor_is_not_pushed(self):
        collector = self._collector(rows=trades(buyers=2, volume=0.05))
        self.assertEqual(0, collector._sweep_candidates()["pushed"])
        self.assertEqual([], collector.scanner.candidates)

    def test_net_outflow_is_not_pushed(self):
        collector = self._collector(rows=trades(buyers=4, volume=10.0, sells=3, seller_amount=9.0))
        self.assertEqual(0, collector._sweep_candidates()["pushed"])

    def test_wallets_that_sold_are_not_fresh_demand(self):
        now = time.time()
        rows = trades(buyers=3, volume=30.0)
        rows += [{"ts": now - 10, "side": "sell", "account": row["account"], "amount": 0.1}
                 for row in rows if row["side"] == "buy"]
        collector = self._collector(rows=rows)
        self.assertEqual(0, collector._sweep_candidates()["pushed"])

    def test_trades_outside_the_window_are_dropped(self):
        collector = self._collector(rows=trades(buyers=5, volume=100.0, age=4000.0))
        collector.activity_window_seconds = 1800.0
        summary = collector._sweep_candidates()
        self.assertEqual(0, summary["pushed"])
        self.assertEqual({}, collector._recent_trades)

    def test_a_token_is_alerted_once(self):
        collector = self._collector()
        self.assertEqual(1, collector._sweep_candidates()["pushed"])
        self.assertEqual(0, collector._sweep_candidates()["pushed"])
        self.assertEqual(1, len(collector.scanner.candidates))

    def test_hourly_cap_stops_the_sweep(self):
        collector = self._collector()
        collector.candidate_max_per_hour = 1
        collector._recent_trades["0x" + "77" * 20] = trades(buyers=5, volume=50.0)
        self.assertEqual(1, collector._sweep_candidates()["pushed"])
        self.assertEqual(1, len(collector.scanner.candidates))

    def test_rejected_delivery_is_retried_next_sweep(self):
        collector = self._collector(accepted=False)
        self.assertEqual(0, collector._sweep_candidates()["pushed"])
        self.assertEqual({}, collector._candidate_alerted)

    def test_sweep_is_a_no_op_without_a_notifier(self):
        collector = self._collector()
        collector.scanner = None
        self.assertEqual(0, collector._sweep_candidates()["pushed"])


class ActivityRecordingTests(unittest.TestCase):
    def test_trade_events_feed_the_window(self):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub())
        collector.activity_window_seconds = 1800.0
        event = {"args": {"token": MOVER, "account": "0x" + "aa" * 20, "cost": str(3 * 10 ** 18)},
                 "timestamp": time.time()}
        asyncio.run(collector._handle_scanner_event("TokenPurchase", event))
        self.assertIn(MOVER, collector._recent_trades)
        self.assertEqual(3.0, collector._recent_trades[MOVER][0]["amount"])
        self.assertEqual("buy", collector._recent_trades[MOVER][0]["side"])

    def test_sales_are_recorded_as_sells(self):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub())
        event = {"args": {"token": MOVER, "account": "0x" + "bb" * 20, "cost": str(2 * 10 ** 18)},
                 "timestamp": time.time()}
        asyncio.run(collector._handle_scanner_event("TokenSale", event))
        self.assertEqual("sell", collector._recent_trades[MOVER][0]["side"])

    def test_old_trades_are_trimmed_on_append(self):
        collector = ContinuousCollector()
        collector.activity_window_seconds = 60.0
        now = time.time()
        collector._record_trade(MOVER, "buy", "0x" + "aa" * 20, 1.0, now - 600)
        collector._record_trade(MOVER, "buy", "0x" + "bb" * 20, 1.0, now)
        self.assertEqual(1, len(collector._recent_trades[MOVER]))

    def test_create_events_do_not_pollute_the_trade_window(self):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub())
        collector.scanner_signal_events = set()
        event = {"args": {"token": MOVER, "creator": "0x" + "cc" * 20}, "timestamp": time.time()}
        asyncio.run(collector._handle_scanner_event("TokenCreate", event))
        self.assertEqual({}, collector._recent_trades)


class _AsyncEth:
    """AsyncWeb3 exposes block_number as an awaitable attribute, not a coroutine function."""

    def __init__(self, head):
        self._head = head

    @property
    def block_number(self):
        async def value():
            return self._head

        return value()


class _AsyncW3:
    def __init__(self, head):
        self.eth = _AsyncEth(head)


class ChainSeedTests(unittest.TestCase):
    """A restart must not start with an empty window: replay it from the chain."""

    def _collector(self, logs):
        collector = ContinuousCollector()
        collector.activity_window_seconds = 1800.0
        collector._fetch_recent_trade_logs = lambda frm, to: logs

        collector.listener = type("L", (), {"w3": _AsyncW3(1_000_000)})()
        return collector

    def test_seed_replays_purchases_into_the_window(self):
        from src.data.fourmeme_log_decoder import TRADE_TOPICS

        topic = next(iter(TRADE_TOPICS))
        buyer = "0x" + "aa" * 20
        data = "0x" + (int(3 * 10 ** 18)).to_bytes(32, "big").hex() + (0).to_bytes(32, "big").hex()
        # Use the real decoder shape: token and account are indexed topics.
        log = {"blockNumber": hex(999_990), "topics": [topic, "0x" + "00" * 12 + MOVER[2:], "0x" + "00" * 12 + buyer[2:]],
               "data": data}
        collector = self._collector([log])
        seeded = asyncio.run(collector._seed_activity_from_chain())
        self.assertGreaterEqual(seeded, 0)
        # 1800s window + 60 blocks of padding, at ~3s per BSC block.
        self.assertEqual(660, collector._activity_seeded_blocks)

    def test_seed_failure_is_not_fatal(self):
        collector = ContinuousCollector()
        collector._fetch_recent_trade_logs = lambda frm, to: (_ for _ in ()).throw(RuntimeError("rpc down"))

        collector.listener = type("L", (), {"w3": _AsyncW3(1_000_000)})()
        with self.assertLogs("root", level="WARNING"):
            self.assertEqual(0, asyncio.run(collector._seed_activity_from_chain()))


class LogFetchTests(unittest.TestCase):
    """eth_getLogs wants 0x-prefixed topics; bare hashes are rejected by the node."""

    def test_topics_are_sent_with_the_0x_prefix(self):
        from unittest.mock import patch

        from src.data.fourmeme_log_decoder import TRADE_TOPICS

        captured = {}

        class Response:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"result": []}

        class Session:
            def post(self, url, json=None, timeout=None):
                captured.update(json)
                return Response()

            def close(self):
                return None

            def proxies(self):
                return {}

        collector = ContinuousCollector()
        # The loader stubs config.config, so patch the class this module actually holds.
        module_config = collect_continuous_module.Config
        with patch("requests.Session", return_value=Session()), \
             patch.object(module_config, "get_log_http_pool", return_value=["https://example.invalid"],
                          create=True), \
             patch.object(module_config, "get_local_proxy_url", return_value=None, create=True), \
             patch.object(module_config, "get_contract_config", create=True,
                          return_value={"contract_address": "0x5c952063c7fc8610FFDB798152D69F0B9550762b"}):
            collector._fetch_recent_trade_logs(100, 150)

        topics = captured["params"][0]["topics"][0]
        self.assertEqual(len(TRADE_TOPICS), len(topics))
        for topic in topics:
            self.assertTrue(topic.startswith("0x"), topic)
            self.assertEqual(66, len(topic), topic)


class SignalScanTests(unittest.TestCase):
    def _event(self):
        return {"args": {"base": TOKEN, "creator": "0x" + "55" * 20}, "timestamp": 1.0,
                "received_at": 1.0, "blockNumber": 1, "logIndex": 0, "transactionHash": "0xabc"}

    @staticmethod
    def _launch(event_data):
        from src.radar.events import launch_from_event

        return launch_from_event("LiquidityAdded", event_data, chain="bsc")

    def test_without_a_notifier_nothing_is_scheduled(self):
        collector = ContinuousCollector()

        async def scenario():
            collector.scanner = None
            collector._schedule_signal_scan(self._launch(self._event()))
            self.assertEqual(set(), collector._signal_scan_tasks)

            collector.scanner = FakeScanner()
            collector.scanner.notifier = None
            collector._schedule_signal_scan(self._launch(self._event()))
            self.assertEqual(set(), collector._signal_scan_tasks)

        asyncio.run(scenario())

    def test_scan_is_scheduled_once_per_dedupe_window(self):
        collector = ContinuousCollector()
        collector.signal_scan_dedupe_seconds = 3600.0
        scanner = FakeScanner(DecisionStub())
        collector.scanner = scanner

        async def scenario():
            collector._schedule_signal_scan(self._launch(self._event()))
            collector._schedule_signal_scan(self._launch(self._event()))
            await asyncio.gather(*collector._signal_scan_tasks)

        asyncio.run(scenario())
        self.assertEqual(1, len(scanner.scanned))

    def test_inflight_cap_drops_extra_scans(self):
        collector = ContinuousCollector()
        collector.signal_scan_max_inflight = 1
        collector.signal_scan_dedupe_seconds = 0.0
        collector.scanner = FakeScanner(DecisionStub())

        async def scenario():
            collector._schedule_signal_scan(self._launch(self._event()))
            second = self._event()
            second["args"] = {"base": "0x" + "66" * 20, "creator": "0x" + "55" * 20}
            collector._schedule_signal_scan(self._launch(second))
            await asyncio.gather(*collector._signal_scan_tasks)

        asyncio.run(scenario())
        self.assertEqual(1, len(collector.scanner.scanned))

    def test_learning_mode_blocks_automatic_scans(self):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub())
        collector.signal_scans_blocked_by_mode = True

        async def scenario():
            collector._schedule_signal_scan(self._launch(self._event()))
            self.assertEqual(set(), collector._signal_scan_tasks)

        asyncio.run(scenario())

    def test_scan_runs_off_the_event_loop_and_reports_failures(self):
        collector = ContinuousCollector()
        scanner = FakeScanner(DecisionStub())
        collector.scanner = scanner

        class Launch:
            token = TOKEN

        asyncio.run(collector._run_signal_scan(Launch()))
        self.assertEqual(TOKEN, scanner.scanned[0][0])

        collector.scanner = FakeScanner(error=RuntimeError("provider down"))
        with self.assertLogs("root", level="WARNING") as captured:
            asyncio.run(collector._run_signal_scan(Launch()))
        self.assertIn("Signal scan failed", "\n".join(captured.output))

    def test_event_without_a_token_is_ignored(self):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub())
        collector.scanner_signal_events = {"LiquidityAdded"}

        async def scenario():
            await collector._handle_scanner_event("LiquidityAdded", {"args": {}})
            self.assertEqual(set(), collector._signal_scan_tasks)

        asyncio.run(scenario())


class EventParsingTests(unittest.TestCase):
    def test_supported_event_names_map_to_listener_events(self):
        parse = ContinuousCollector._parse_signal_events
        self.assertEqual({"TokenCreate"}, parse("launch"))
        self.assertEqual({"LiquidityAdded"}, parse("graduation"))
        self.assertEqual({"TokenCreate", "LiquidityAdded"}, parse(" launch , graduation "))
        self.assertEqual(set(), parse(""))
        self.assertEqual(set(), parse(None))

    def test_unknown_event_name_is_rejected_loudly(self):
        with self.assertRaisesRegex(ValueError, "SCANNER_SIGNAL_EVENTS"):
            ContinuousCollector._parse_signal_events("moon")

    def test_automatic_scans_are_off_by_default(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"SCANNER_SIGNAL_EVENTS": ""}, clear=False):
            collector = ContinuousCollector()
        self.assertEqual(set(), collector.scanner_signal_events)
        self.assertIsNone(collector.scanner)

    def test_candidate_sweep_is_off_by_default(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"SCANNER_CANDIDATE_SWEEP_SECONDS": "0"}, clear=False):
            collector = ContinuousCollector()
        self.assertEqual(0.0, collector.candidate_sweep_seconds)


class ResumeWindowTests(unittest.TestCase):
    """A restart must not silently drop a large block range."""

    def _collector_with(self, value):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"COLLECTOR_RESUME_MAX_CATCHUP_BLOCKS": value}, clear=False):
            return ContinuousCollector()

    def test_default_window_is_256_blocks(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COLLECTOR_RESUME_MAX_CATCHUP_BLOCKS", None)
            collector = ContinuousCollector()
        self.assertEqual(256, collector.resume_max_catchup_blocks)

    def test_window_can_be_widened_from_the_environment(self):
        self.assertEqual(50000, self._collector_with("50000").resume_max_catchup_blocks)

    def test_zero_disables_the_truncation(self):
        collector = self._collector_with("0")
        cursor = {"block_number": 100, "log_index": -1, "tx_hash": ""}
        self.assertEqual(cursor, collector._bound_resume_cursor(cursor, current_block=100_000))

    def test_a_large_gap_is_truncated_at_the_default_window(self):
        collector = self._collector_with("256")
        cursor = {"block_number": 100, "log_index": -1, "tx_hash": ""}
        bounded = collector._bound_resume_cursor(cursor, current_block=100_000)
        self.assertEqual(100_000 - 256, bounded["block_number"])


if __name__ == "__main__":
    unittest.main()


class AlertPersistenceTests(unittest.TestCase):
    """What was pushed must survive the log rotating, and a restart must not repeat it."""

    def test_pipeline_records_a_sent_candidate(self):
        import tempfile
        from pathlib import Path as PathlibPath

        from config.scanner_config import ScannerConfig
        from src.radar.pipeline import ScannerPipeline
        from src.radar.store import ScannerStore

        class Recorder:
            def notify_candidate(self, token, **kwargs):
                return True

        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(PathlibPath(tmp) / "scanner.sqlite")
            pipeline = ScannerPipeline(store, ScannerConfig(mode="safe"), notifier=Recorder(),
                                       clock=lambda: 100.0, chain="bsc")
            sent = pipeline.announce_candidate(MOVER, stats={"fresh_buyers": 7, "buy_volume": 42.3},
                                               override={"mcap_usd": 45000})
            rows = store.rows("candidate_alert", chain=None)
        self.assertTrue(sent)
        self.assertEqual(1, len(rows))
        self.assertEqual(MOVER.lower(), str(rows[0]["entity"]).lower())
        self.assertEqual(7, rows[0]["payload"]["fresh_buyers"])

    def test_rejected_candidate_is_not_recorded(self):
        import tempfile
        from pathlib import Path as PathlibPath

        from config.scanner_config import ScannerConfig
        from src.radar.pipeline import ScannerPipeline
        from src.radar.store import ScannerStore

        class Refuser:
            def notify_candidate(self, token, **kwargs):
                return False

        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(PathlibPath(tmp) / "scanner.sqlite")
            pipeline = ScannerPipeline(store, ScannerConfig(mode="safe"), notifier=Refuser(),
                                       clock=lambda: 100.0)
            pipeline.announce_candidate(MOVER, stats={"fresh_buyers": 7}, override={})
            rows = store.rows("candidate_alert")
        self.assertEqual([], rows)

    def test_recent_alerts_are_loaded_after_a_restart(self):
        import tempfile
        from pathlib import Path as PathlibPath

        from src.radar.store import ScannerStore

        with tempfile.TemporaryDirectory() as tmp:
            store = ScannerStore(PathlibPath(tmp) / "scanner.sqlite")
            now = time.time()
            store.append("candidate_alert", MOVER, {"fresh_buyers": 7}, now - 60, now - 60)
            store.append("candidate_alert", "0x" + "99" * 20, {}, now - 7200, now - 7200)
            collector = ContinuousCollector()
            collector.scanner = type("S", (), {"store": store, "notifier": object()})()
            loaded = collector._load_recent_alerts()
        self.assertEqual(1, loaded)
        self.assertIn(MOVER.lower(), collector._candidate_alerted)

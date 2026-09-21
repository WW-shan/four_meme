import asyncio
import time
import unittest

from tests.model.test_collect_continuous_cleanup import collect_continuous_module

ContinuousCollector = collect_continuous_module.ContinuousCollector

TOKEN = "0x" + "44" * 20


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
        # Constructed with the switch blanked: a developer's own .env may enable scans, and
        # this test is about the shipped default, not about that machine.
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"SCANNER_SIGNAL_EVENTS": ""}, clear=False):
            collector = ContinuousCollector()
        self.assertEqual(set(), collector.scanner_signal_events)
        self.assertIsNone(collector.scanner)


class FundingConfirmationTests(unittest.TestCase):
    def _collector(self):
        collector = ContinuousCollector()
        collector.signal_min_unique_buyers = 3
        return collector

    def test_fresh_buyers_above_the_floor_with_net_inflow_confirm_funding(self):
        collector = self._collector()
        collector.collector.token_lifecycle = {TOKEN: {
            "buys": [{"account": f"0x{i:040x}", "bnb_amount": 2.0} for i in range(4)],
            "sells": [],
        }}
        self.assertTrue(collector._funding_confirmed_for(TOKEN))

    def test_wallets_that_already_sold_do_not_count_as_fresh_demand(self):
        collector = self._collector()
        collector.collector.token_lifecycle = {TOKEN: {
            "buys": [{"account": f"0x{i:040x}", "bnb_amount": 2.0} for i in range(4)],
            "sells": [{"account": f"0x{i:040x}", "bnb_amount": 1.0} for i in range(2)],
        }}
        self.assertFalse(collector._funding_confirmed_for(TOKEN))

    def test_sell_volume_above_buy_volume_is_not_confirmed(self):
        collector = self._collector()
        collector.collector.token_lifecycle = {TOKEN: {
            "buys": [{"account": f"0x{i:040x}", "bnb_amount": 1.0} for i in range(4)],
            "sells": [{"account": "0xdead", "bnb_amount": 9.0}],
        }}
        self.assertFalse(collector._funding_confirmed_for(TOKEN))

    def test_unknown_token_is_not_confirmed(self):
        self.assertFalse(self._collector()._funding_confirmed_for(TOKEN))


class SchedulingTests(unittest.TestCase):
    def _event(self, event_name="LiquidityAdded"):
        # TokenCreate carries the address as `token`, LiquidityAdded as `base`.
        args = {"base": TOKEN} if event_name == "LiquidityAdded" else {"token": TOKEN}
        args["creator"] = "0x" + "55" * 20
        return {"args": args, "timestamp": 1.0, "received_at": 1.0,
                "blockNumber": 1, "logIndex": 0, "transactionHash": "0xabc"}

    @staticmethod
    def _launch(event_data, event_name="LiquidityAdded"):
        from src.radar.events import launch_from_event

        return launch_from_event(event_name, event_data, chain="bsc")

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
        self.assertEqual(TOKEN, scanner.scanned[0][0])

    def test_inflight_cap_drops_extra_scans(self):
        collector = ContinuousCollector()
        collector.signal_scan_max_inflight = 1
        collector.signal_scan_dedupe_seconds = 0.0
        collector.scanner = FakeScanner(DecisionStub())

        async def scenario():
            collector._schedule_signal_scan(self._launch(self._event()))
            second = dict(self._event())
            second["args"] = {"base": "0x" + "66" * 20, "creator": "0x" + "55" * 20}
            collector._schedule_signal_scan(self._launch(second))
            await asyncio.gather(*collector._signal_scan_tasks)

        asyncio.run(scenario())
        self.assertEqual(1, len(collector.scanner.scanned))

    def test_scan_runs_off_the_event_loop_and_reports_failures(self):
        collector = ContinuousCollector()
        scanner = FakeScanner(DecisionStub())
        collector.scanner = scanner

        class Launch:
            token = TOKEN

        asyncio.run(collector._run_signal_scan(Launch()))
        self.assertEqual(TOKEN, scanner.scanned[0][0])
        self.assertIn("funding_confirmed", scanner.scanned[0][1])

        failing = FakeScanner(error=RuntimeError("provider down"))
        collector.scanner = failing
        with self.assertLogs("root", level="WARNING") as captured:
            asyncio.run(collector._run_signal_scan(Launch()))
        self.assertIn("Signal scan failed", "\n".join(captured.output))

    def test_learning_mode_blocks_automatic_scans(self):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub())
        collector.signal_scans_blocked_by_mode = True

        async def scenario():
            collector._schedule_signal_scan(self._launch(self._event()))
            self.assertEqual(set(), collector._signal_scan_tasks)

        asyncio.run(scenario())
        self.assertEqual([], collector.scanner.scanned)

    def test_event_without_a_token_is_ignored(self):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub())
        collector.candidate_sweep_seconds = 60.0
        collector.scanner_signal_events = {"LiquidityAdded"}

        async def scenario():
            await collector._handle_scanner_event("LiquidityAdded", {"args": {}})
            self.assertEqual(set(), collector._signal_scan_tasks)

        asyncio.run(scenario())

    def test_event_without_a_signal_subscription_is_only_recorded(self):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub())
        collector.scanner_signal_events = set()

        async def scenario():
            await collector._handle_scanner_event("TokenCreate", self._event("TokenCreate"))
            self.assertEqual(set(), collector._signal_scan_tasks)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()


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
        collector = self._collector_with("50000")
        self.assertEqual(50000, collector.resume_max_catchup_blocks)

    def test_zero_disables_the_truncation(self):
        collector = self._collector_with("0")
        self.assertEqual(0, collector.resume_max_catchup_blocks)
        cursor = {"block_number": 100, "log_index": -1, "tx_hash": ""}
        self.assertEqual(cursor, collector._bound_resume_cursor(cursor, current_block=100_000))

    def test_a_large_gap_is_truncated_at_the_default_window(self):
        collector = self._collector_with("256")
        cursor = {"block_number": 100, "log_index": -1, "tx_hash": ""}
        bounded = collector._bound_resume_cursor(cursor, current_block=100_000)
        self.assertEqual(100_000 - 256, bounded["block_number"])


class CandidateSweepTests(unittest.TestCase):
    """The sweep is what actually scans the chain: it pushes tokens with real buyer flow."""

    CHECKSUMMED = "0x" + "44" * 20  # the collector stores the address as the event carried it

    def _collector(self, accepted=True):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub(), candidate_accepted=accepted)
        collector.signal_min_unique_buyers = 3
        collector.candidate_max_per_hour = 20
        collector.candidate_max_age_seconds = 3600.0
        collector.collector.token_lifecycle = {TOKEN: {
            "symbol": "FOO",
            "name": "Foo Token",
            "create_timestamp": int(time.time()) - 300,
            "buys": [{"account": f"0x{i:040x}", "bnb_amount": 2.0} for i in range(4)],
            "sells": [],
        }}
        return collector

    def test_candidate_is_pushed_when_fresh_buyers_pass_the_bar(self):
        collector = self._collector()
        self.assertEqual(1, collector._sweep_candidates()["pushed"])
        token, kwargs = collector.scanner.candidates[0]
        self.assertEqual(TOKEN.lower(), token)
        self.assertEqual(4, kwargs["stats"]["fresh_buyers"])
        self.assertEqual("FOO", kwargs["symbol"])
        self.assertTrue(kwargs["stats"]["funding_confirmed"])

    def test_token_without_real_flow_is_not_pushed(self):
        collector = self._collector()
        collector.collector.token_lifecycle[TOKEN]["buys"] = [
            {"account": "0x" + "01" * 20, "bnb_amount": 2.0}
        ]
        self.assertEqual(0, collector._sweep_candidates()["pushed"])
        self.assertEqual([], collector.scanner.candidates)

    def test_sellers_do_not_count_as_fresh_demand(self):
        collector = self._collector()
        collector.collector.token_lifecycle[TOKEN]["sells"] = [
            {"account": f"0x{i:040x}", "bnb_amount": 1.0} for i in range(4)
        ]
        self.assertEqual(0, collector._sweep_candidates()["pushed"])

    def test_a_token_is_alerted_only_once(self):
        collector = self._collector()
        self.assertEqual(1, collector._sweep_candidates()["pushed"])
        self.assertEqual(0, collector._sweep_candidates()["pushed"])
        self.assertEqual(1, len(collector.scanner.candidates))

    def test_hourly_cap_stops_the_sweep(self):
        collector = self._collector()
        collector.candidate_max_per_hour = 1
        second = "0x" + "77" * 20
        collector.collector.token_lifecycle[second] = dict(
            collector.collector.token_lifecycle[TOKEN], buys=[
                {"account": f"0x{i:040x}", "bnb_amount": 2.0} for i in range(5)
            ]
        )
        self.assertEqual(1, collector._sweep_candidates()["pushed"])
        self.assertEqual(1, len(collector.scanner.candidates))

    def test_tokens_older_than_the_window_are_skipped(self):
        collector = self._collector()
        collector.candidate_max_age_seconds = 60.0
        collector.collector.token_lifecycle[TOKEN]["create_timestamp"] = int(time.time()) - 600
        self.assertEqual(0, collector._sweep_candidates()["pushed"])

    def test_rejected_delivery_is_not_marked_as_alerted(self):
        collector = self._collector(accepted=False)
        self.assertEqual(0, collector._sweep_candidates()["pushed"])
        self.assertEqual({}, collector._candidate_alerted)


class AddressKeyTests(unittest.TestCase):
    """The watch set is lowercase while the collector stores checksummed addresses."""

    def test_lowercase_watch_key_still_finds_a_checksummed_lifecycle(self):
        collector = ContinuousCollector()
        collector.signal_min_unique_buyers = 3
        checksummed = "0xAbCdEf0000000000000000000000000000000001"
        collector.collector.token_lifecycle = {checksummed: {
            "buys": [{"account": f"0x{i:040x}", "bnb_amount": 1.0} for i in range(3)],
            "sells": [],
            "create_timestamp": int(time.time()) - 60,
        }}
        stats = collector._flow_stats_for(checksummed.lower())
        self.assertIsNotNone(stats)
        self.assertEqual(3, stats["fresh_buyers"])
        self.assertTrue(collector._funding_confirmed_for(checksummed.lower()))

    def test_sweep_pushes_when_only_the_case_differs(self):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub())
        collector.signal_min_unique_buyers = 3
        checksummed = "0xAbCdEf0000000000000000000000000000000002"
        collector.collector.token_lifecycle = {checksummed: {
            "symbol": "CASE",
            "buys": [{"account": f"0x{i:040x}", "bnb_amount": 1.0} for i in range(3)],
            "sells": [],
            "create_timestamp": int(time.time()) - 60,
        }}
        self.assertEqual(1, collector._sweep_candidates()["pushed"])
        self.assertEqual("CASE", collector.scanner.candidates[0][1]["symbol"])


class SeedStatsTests(unittest.TestCase):
    """A restart must not forget the tokens the previous run had already collected."""

    def _collector_with_dir(self, rows, *, max_age=3600.0, tail_bytes=None):
        import json as jsonlib
        import tempfile
        from pathlib import Path as PathlibPath

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        output = PathlibPath(tmp.name)
        now = time.time()
        with (output / "lifecycle_incremental_20260921_231626.jsonl").open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(jsonlib.dumps(row) + "\n")
        collector = ContinuousCollector()
        collector.collector.output_dir = output
        collector.candidate_max_age_seconds = max_age
        return collector, now

    def test_recent_tokens_are_seeded_and_old_ones_are_not(self):
        now = time.time()
        rows = [
            {"token_address": "0xRecent1", "create_timestamp": int(now) - 60},
            {"token_address": "0xRecent2", "create_timestamp": int(now) - 1800},
            {"token_address": "0xAncient", "create_timestamp": int(now) - 7200},
            {"token_address": "", "create_timestamp": int(now) - 60},
        ]
        collector, _ = self._collector_with_dir(rows)
        self.assertEqual(2, collector._seed_candidate_stats())
        self.assertEqual({"0xrecent1", "0xrecent2"}, set(collector._candidate_seed_stats))

    def test_older_files_are_read_too(self):
        import json as jsonlib
        import tempfile
        from pathlib import Path as PathlibPath

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        output = PathlibPath(tmp.name)
        now = time.time()
        # The newest file only holds a fresh token; the qualifying one is in an older file.
        with (output / "lifecycle_incremental_20260921_233322.jsonl").open("w", encoding="utf-8") as f:
            f.write(jsonlib.dumps({"token_address": "0xNew", "create_timestamp": int(now) - 30}) + "\n")
        with (output / "lifecycle_incremental_20260921_231010.jsonl").open("w", encoding="utf-8") as f:
            f.write(jsonlib.dumps({
                "token_address": "0xOld", "symbol": "OLD", "create_timestamp": int(now) - 2400,
                "buys": [{"account": f"0x{i:040x}", "bnb_amount": 4.0} for i in range(4)], "sells": [],
            }) + "\n")

        collector = ContinuousCollector()
        collector.collector.output_dir = output
        collector.signal_min_unique_buyers = 3
        collector.candidate_max_age_seconds = 3600.0
        collector.scanner = FakeScanner(DecisionStub())
        collector.collector.token_lifecycle = {}

        self.assertEqual(2, collector._seed_candidate_stats())
        summary = collector._sweep_candidates()
        self.assertEqual(1, summary["pushed"])
        self.assertEqual("0xold", collector.scanner.candidates[0][0])
        self.assertEqual("OLD", collector.scanner.candidates[0][1]["symbol"])

    def test_missing_directory_is_not_fatal(self):
        collector = ContinuousCollector()
        import tempfile
        from pathlib import Path as PathlibPath

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        collector.collector.output_dir = PathlibPath(tmp.name)
        self.assertEqual(0, collector._seed_candidate_stats())

    def test_tail_read_skips_a_partial_first_line(self):
        now = time.time()
        rows = [{"token_address": f"0xTok{i}", "create_timestamp": int(now) - 60} for i in range(200)]
        collector, _ = self._collector_with_dir(rows)
        seeded = collector._seed_candidate_stats(max_bytes_per_file=200)
        self.assertGreater(seeded, 0)
        for token in collector._candidate_seed_stats:
            self.assertTrue(token.startswith("0xtok"))


class SeededStatsTests(unittest.TestCase):
    """A token flushed by the previous run can still qualify right after a restart."""

    def test_seeded_flow_stats_are_used_when_memory_has_nothing(self):
        import json as jsonlib
        import tempfile
        from pathlib import Path as PathlibPath

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        output = PathlibPath(tmp.name)
        now = time.time()
        row = {
            "token_address": "0xSeed000000000000000000000000000000000001",
            "symbol": "SEED",
            "create_timestamp": int(now) - 300,
            "buys": [{"account": f"0x{i:040x}", "bnb_amount": 5.0} for i in range(4)],
            "sells": [],
        }
        with (output / "lifecycle_incremental_20260921_231626.jsonl").open("w", encoding="utf-8") as f:
            f.write(jsonlib.dumps(row) + "\n")

        collector = ContinuousCollector()
        collector.collector.output_dir = output
        collector.signal_min_unique_buyers = 3
        collector.candidate_max_age_seconds = 3600.0
        collector.scanner = FakeScanner(DecisionStub())
        collector.collector.token_lifecycle = {}   # restart: memory is empty

        self.assertEqual(1, collector._seed_candidate_stats())
        summary = collector._sweep_candidates()
        self.assertEqual(1, summary["qualifying"])
        self.assertEqual(1, summary["pushed"])
        token, kwargs = collector.scanner.candidates[0]
        self.assertEqual(row["token_address"].lower(), token)
        self.assertEqual("SEED", kwargs["symbol"])
        self.assertEqual(4, kwargs["stats"]["fresh_buyers"])


class SeededVersusLiveTests(unittest.TestCase):
    """After a restart the in-memory record only holds post-restart buys."""

    def test_richer_flushed_record_wins_over_a_thin_live_record(self):
        import json as jsonlib
        import tempfile
        from pathlib import Path as PathlibPath

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        output = PathlibPath(tmp.name)
        now = time.time()
        token = "0xFeed000000000000000000000000000000000009"
        row = {
            "token_address": token, "symbol": "RICH", "create_timestamp": int(now) - 1800,
            "buys": [{"account": f"0x{i:040x}", "bnb_amount": 3.0} for i in range(6)], "sells": [],
        }
        with (output / "lifecycle_incremental_20260921_231626.jsonl").open("w", encoding="utf-8") as f:
            f.write(jsonlib.dumps(row) + "\n")

        collector = ContinuousCollector()
        collector.collector.output_dir = output
        collector.signal_min_unique_buyers = 3
        collector.candidate_max_age_seconds = 3600.0
        collector.scanner = FakeScanner(DecisionStub())
        # The live record only saw one buy since the restart.
        collector.collector.token_lifecycle = {token: {
            "symbol": "RICH", "create_timestamp": int(now) - 1800,
            "buys": [{"account": "0x" + "aa" * 20, "bnb_amount": 3.0}], "sells": [],
        }}
        collector._seed_candidate_stats()
        summary = collector._sweep_candidates()
        self.assertEqual(1, summary["pushed"])
        self.assertEqual(6, collector.scanner.candidates[0][1]["stats"]["fresh_buyers"])


class LiveMemorySweepTests(unittest.TestCase):
    """The sweep must scan the collector's own state, not just tokens it saw created."""

    def test_mover_held_in_memory_is_pushed_without_any_watch_entry(self):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub())
        collector.signal_min_unique_buyers = 2
        collector.candidate_max_per_hour = 20
        collector.candidate_max_age_seconds = 3600.0
        mover = "0x46c91b9300e3bfffad61870f09b56bac094bffff"
        collector.collector.token_lifecycle = {mover: {
            "symbol": "REAL", "name": "Real Mover", "create_timestamp": int(time.time()) - 1200,
            "buys": [{"account": f"0x{i:040x}", "bnb_amount": 12.0} for i in range(9)],
            "sells": [],
        }}
        summary = collector._sweep_candidates()
        self.assertEqual(1, summary["pushed"])
        token, kwargs = collector.scanner.candidates[0]
        self.assertEqual(mover.lower(), token)
        self.assertEqual(9, kwargs["stats"]["fresh_buyers"])

    def test_dust_token_below_the_bar_is_not_pushed(self):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub())
        collector.signal_min_unique_buyers = 2
        collector.collector.token_lifecycle = {"0x" + "11" * 20: {
            "symbol": "DUST", "create_timestamp": int(time.time()) - 300,
            "buys": [{"account": "0x" + "22" * 20, "bnb_amount": 0.05}], "sells": [],
        }}
        self.assertEqual(0, collector._sweep_candidates()["pushed"])

    def test_token_older_than_the_window_is_not_pushed(self):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub())
        collector.signal_min_unique_buyers = 2
        collector.candidate_max_age_seconds = 600.0
        collector.collector.token_lifecycle = {"0x" + "33" * 20: {
            "symbol": "OLD", "create_timestamp": int(time.time()) - 3600,
            "buys": [{"account": f"0x{i:040x}", "bnb_amount": 9.0} for i in range(5)], "sells": [],
        }}
        self.assertEqual(0, collector._sweep_candidates()["pushed"])


class VolumeFloorTests(unittest.TestCase):
    """Wallet count alone cannot tell a real mover from dust."""

    def _collector(self, volume, buyers=2):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub())
        collector.signal_min_unique_buyers = 2
        collector.candidate_min_buy_volume = 1.0
        collector.candidate_max_age_seconds = 3600.0
        collector.collector.token_lifecycle = {"0x" + "55" * 20: {
            "symbol": "X", "create_timestamp": int(time.time()) - 300,
            "buys": [{"account": f"0x{i:040x}", "bnb_amount": volume / buyers} for i in range(buyers)],
            "sells": [],
        }}
        return collector

    def test_dust_below_the_floor_is_not_pushed(self):
        self.assertEqual(0, self._collector(0.05)._sweep_candidates()["pushed"])

    def test_real_volume_is_pushed(self):
        collector = self._collector(352.1, buyers=4)
        self.assertEqual(1, collector._sweep_candidates()["pushed"])

    def test_floor_can_be_disabled(self):
        collector = self._collector(0.05)
        collector.candidate_min_buy_volume = 0.0
        self.assertEqual(1, collector._sweep_candidates()["pushed"])

import asyncio
import unittest

from tests.model.test_collect_continuous_cleanup import collect_continuous_module

ContinuousCollector = collect_continuous_module.ContinuousCollector

TOKEN = "0x" + "44" * 20


class FakeNotifier:
    def notify_decision(self, decision, **kwargs):
        return True


class FakeScanner:
    def __init__(self, decision=None, error=None):
        self.chain = "bsc"
        self.notifier = FakeNotifier()
        self.scanned = []
        self.decision = decision
        self.error = error

    def scan(self, token, **kwargs):
        self.scanned.append((token, kwargs))
        if self.error is not None:
            raise self.error
        return self.decision


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
        return {"args": {"base": TOKEN, "creator": "0x" + "55" * 20}, "timestamp": 1.0,
                "received_at": 1.0, "blockNumber": 1, "logIndex": 0, "transactionHash": "0xabc"}

    def test_without_a_notifier_nothing_is_scheduled(self):
        collector = ContinuousCollector()

        async def scenario():
            collector.scanner = None
            collector._schedule_signal_scan("LiquidityAdded", self._event())
            self.assertEqual(set(), collector._signal_scan_tasks)

            collector.scanner = FakeScanner()
            collector.scanner.notifier = None
            collector._schedule_signal_scan("LiquidityAdded", self._event())
            self.assertEqual(set(), collector._signal_scan_tasks)

        asyncio.run(scenario())

    def test_scan_is_scheduled_once_per_dedupe_window(self):
        collector = ContinuousCollector()
        collector.signal_scan_dedupe_seconds = 3600.0
        scanner = FakeScanner(DecisionStub())
        collector.scanner = scanner

        async def scenario():
            collector._schedule_signal_scan("LiquidityAdded", self._event())
            collector._schedule_signal_scan("LiquidityAdded", self._event())
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
            collector._schedule_signal_scan("LiquidityAdded", self._event())
            second = dict(self._event())
            second["args"] = {"base": "0x" + "66" * 20, "creator": "0x" + "55" * 20}
            collector._schedule_signal_scan("LiquidityAdded", second)
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
            collector._schedule_signal_scan("LiquidityAdded", self._event())
            self.assertEqual(set(), collector._signal_scan_tasks)

        asyncio.run(scenario())
        self.assertEqual([], collector.scanner.scanned)

    def test_launch_event_without_a_token_is_ignored(self):
        collector = ContinuousCollector()
        collector.scanner = FakeScanner(DecisionStub())

        async def scenario():
            collector._schedule_signal_scan("LiquidityAdded", {"args": {}})
            self.assertEqual(set(), collector._signal_scan_tasks)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

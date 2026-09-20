import unittest
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from src.pipeline.reentry_probe import PricePoint
from src.pipeline.runner_reserve_profile import (
    RunnerReplayConfig,
    load_lifecycles_from_paths,
    simulate_runner_path,
)
from src.trader.runner_reserve import RunnerReserveConfig, RunnerReservePolicy
from src.trader.bot import MemeBot


class RunnerReservePolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 11, tzinfo=timezone.utc)
        self.position = {
            "entry_price": 1.0,
            "entry_time": self.now,
            "runner_state": "armed",
            "peak_price": 1.0,
        }
        self.policy = RunnerReservePolicy(RunnerReserveConfig(enabled=True))

    def test_disabled_policy_is_strict_noop(self):
        decision = RunnerReservePolicy().evaluate(self.position, current_price=3.0, now=self.now, market={})
        self.assertEqual(decision.action, "disabled")

    def test_activation_requests_partial_exit_and_state_transition(self):
        decision = self.policy.evaluate(self.position, current_price=2.0, now=self.now, market={})
        self.assertEqual(decision.action, "partial_exit")
        self.assertAlmostEqual(decision.partial_exit_ratio, 0.70)
        self.assertEqual(decision.updates["runner_state"], "active")

    def test_active_runner_updates_peak_and_holds_when_flow_is_healthy(self):
        self.position.update({"runner_state": "active", "runner_peak_price": 2.0})
        decision = self.policy.evaluate(
            self.position,
            current_price=2.5,
            now=self.now,
            market={"sell_pressure_30s": 0.20},
        )
        self.assertEqual(decision.action, "hold")
        self.assertEqual(decision.updates["runner_state"], "active")
        self.assertAlmostEqual(decision.updates["runner_peak_price"], 2.5)

    def test_active_runner_exits_on_peak_drawdown_or_toxic_flow(self):
        self.position.update({"runner_state": "active", "runner_peak_price": 2.5})
        drawdown = self.policy.evaluate(self.position, current_price=1.7, now=self.now, market={})
        self.assertEqual(drawdown.action, "close")
        self.assertEqual(drawdown.reason, "runner_peak_drawdown")

        toxic = self.policy.evaluate(
            self.position,
            current_price=2.4,
            now=self.now,
            market={"sell_pressure_30s": 0.90},
        )
        self.assertEqual(toxic.action, "close")
        self.assertEqual(toxic.reason, "runner_flow_health_exit")

        no_flow = self.policy.evaluate(
            self.position,
            current_price=2.4,
            now=self.now,
            market={"flow_event_count_30s": 0},
        )
        self.assertEqual(no_flow.action, "close")
        self.assertEqual(no_flow.reason, "runner_flow_health_exit")

    def test_graduation_and_short_timeout_only_survive_for_active_runner(self):
        self.assertFalse(self.policy.should_survive_graduation(self.position))
        self.position["runner_state"] = "active"
        self.assertTrue(self.policy.should_survive_graduation(self.position))
        self.assertTrue(self.policy.defers_short_hold_timeout(self.position, 560))


class RunnerReserveReplayTests(unittest.TestCase):
    def test_loader_merges_disjoint_lifecycle_fragments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "lifecycle_incremental_20260913_000000.jsonl"
            rows = [
                {
                    "token_address": "0xFRAGMENT",
                    "create_timestamp": 100,
                    "buys": [{"timestamp": 101, "price": 1.0}],
                    "sells": [],
                    "price_history": [{"timestamp": 101, "price": 1.0, "type": "buy"}],
                },
                {
                    "token_address": "0xFRAGMENT",
                    "create_timestamp": 100,
                    "buys": [{"timestamp": 201, "price": 2.0}],
                    "sells": [],
                    "price_history": [{"timestamp": 201, "price": 2.0, "type": "buy"}],
                },
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            loaded = load_lifecycles_from_paths([path])

            self.assertEqual(len(loaded), 1)
            self.assertEqual([row["timestamp"] for row in loaded[0]["buys"]], [101, 201])

    def test_bot_snapshot_builds_point_in_time_flow_heartbeat(self):
        snapshot = MemeBot._runner_market_snapshot(
            {
                "last_update": 130,
                "buys": [
                    {"timestamp": 120, "bnb_amount": 1.0},
                    {"timestamp": 90, "bnb_amount": 99.0},
                ],
                "sells": [{"timestamp": 125, "bnb_amount": 0.5}],
            },
            {"feature": 1},
        )
        self.assertEqual(snapshot["flow_event_count_30s"], 2)
        self.assertAlmostEqual(snapshot["sell_pressure_30s"], 1.0 / 3.0)
        self.assertEqual(snapshot["feature"], 1)

    def test_replay_activates_then_exits_reserve_after_peak_drawdown(self):
        base = datetime(2026, 9, 11, tzinfo=timezone.utc)
        path = [
            PricePoint(base, 1.0, "buy"),
            PricePoint(base.replace(second=3), 2.1, "buy"),
            PricePoint(base.replace(second=8), 3.0, "buy"),
            PricePoint(base.replace(second=13), 1.9, "sell"),
            PricePoint(base.replace(second=20), 1.8, "sell"),
            PricePoint(base.replace(second=30), 1.8, "sell"),
        ]
        result = simulate_runner_path(
            path,
            sample_time=base.timestamp(),
            anchor_price=1.0,
            horizon_seconds=30,
            config=RunnerReplayConfig(
                entry_delay_seconds=0,
                exit_delay_seconds=0,
                fee_bps=0,
                slippage_bps=0,
            ),
        )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["activated"])
        self.assertEqual(result["runner_reason"], "reserve_stop")
        self.assertGreater(result["runner_return_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from unittest.mock import patch

from config.trading_config import TradingConfig
from src.trader.post_graduation_retention import (
    PostGraduationRetentionPolicy,
    RetentionHealthConfig,
)
from src.trader.bot import MemeBot


class PostGraduationRetentionConfigTests(unittest.TestCase):
    def test_config_is_default_off_with_conditional_horizon(self):
        self.assertFalse(TradingConfig.POST_GRADUATION_RETENTION_ENABLED)
        self.assertFalse(TradingConfig.POST_GRADUATION_RETENTION_SHADOW_ENABLED)
        self.assertEqual(TradingConfig.POST_GRADUATION_RETENTION_MAX_HOLD_SECONDS, 4 * 86_400)
        self.assertEqual(TradingConfig.POST_GRADUATION_RETENTION_MAX_DATA_AGE_SECONDS, 120.0)

    def test_config_rejects_invalid_retention_bounds(self):
        invalid = (
            ("POST_GRADUATION_RETENTION_MAX_HOLD_SECONDS", 0),
            ("POST_GRADUATION_RETENTION_MAX_DATA_AGE_SECONDS", 0.0),
            ("POST_GRADUATION_RETENTION_MIN_LIQUIDITY_USD", -1.0),
            ("POST_GRADUATION_RETENTION_MIN_VOLUME_LIQUIDITY_RATIO_5M", -1.0),
            ("POST_GRADUATION_RETENTION_MIN_NEW_TRADERS_1H", -1.0),
            ("POST_GRADUATION_RETENTION_MAX_SELL_PRESSURE_5M", 1.1),
            ("POST_GRADUATION_RETENTION_MAX_DRAWDOWN", 0.0),
        )
        for attr, value in invalid:
            with self.subTest(attr=attr), patch.object(TradingConfig, attr, value):
                with self.assertRaisesRegex(ValueError, attr):
                    TradingConfig.validate()


class PostGraduationRetentionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 13, tzinfo=timezone.utc)
        self.position = {
            "runner_state": "active",
            "runner_graduated": True,
            "runner_graduated_at": self.now.timestamp() - 60,
            "runner_peak_price": 2.0,
            "entry_time": self.now.timestamp() - 120,
        }
        self.market = {
            "graduated": True,
            "observed_at": self.now.timestamp() - 10,
            "data_source": "test-dex-adapter",
            "dex_liquidity_usd": 25_000.0,
            "volume_liquidity_ratio_5m": 0.40,
            "new_traders_1h": 8,
            "sell_pressure_5m": 0.25,
        }
        self.policy = PostGraduationRetentionPolicy(RetentionHealthConfig(enabled=True))

    def test_disabled_policy_is_strict_noop(self):
        decision = PostGraduationRetentionPolicy().evaluate(
            self.position,
            market={},
            current_price=2.5,
            now=self.now,
        )
        self.assertEqual(decision.action, "disabled")
        self.assertEqual(decision.reason, "post_graduation_retention_disabled")

    def test_waits_for_active_graduated_runner(self):
        inactive = dict(self.position, runner_state="armed")
        decision = self.policy.evaluate(inactive, market=self.market, current_price=2.5, now=self.now)
        self.assertEqual(decision.action, "none")
        self.assertEqual(decision.reason, "retention_runner_not_active")

        not_graduated = dict(self.position, runner_graduated=False)
        decision = self.policy.evaluate(not_graduated, market={}, current_price=2.5, now=self.now)
        self.assertEqual(decision.action, "none")
        self.assertEqual(decision.reason, "retention_waiting_for_graduation")

    def test_healthy_snapshot_holds_and_records_provenance(self):
        decision = self.policy.evaluate(self.position, market=self.market, current_price=2.5, now=self.now)
        self.assertEqual(decision.action, "hold")
        self.assertEqual(decision.reason, "retention_healthy_hold")
        self.assertEqual(decision.health_score, 1.0)
        self.assertEqual(decision.provenance["data_source"], "test-dex-adapter")
        self.assertEqual(decision.provenance["observed_at"], self.market["observed_at"])
        self.assertEqual(decision.provenance["metric_sources"]["liquidity_usd"], "dex_liquidity_usd")
        self.assertEqual(decision.updates["retention_data_source"], "test-dex-adapter")

    def test_derives_volume_ratio_and_sell_pressure_from_explicit_volumes(self):
        market = dict(self.market)
        market.pop("volume_liquidity_ratio_5m")
        market.pop("sell_pressure_5m")
        market.update({
            "dex_volume_5m_usd": 10_000.0,
            "dex_buy_volume_5m_usd": 8_000.0,
            "dex_sell_volume_5m_usd": 2_000.0,
        })
        decision = self.policy.evaluate(self.position, market=market, current_price=2.5, now=self.now)
        self.assertEqual(decision.action, "hold")
        self.assertTrue(decision.checks["volume"])
        self.assertTrue(decision.checks["sell_pressure"])
        self.assertIn("dex_volume_5m_usd/dex_liquidity_usd", decision.provenance["metric_sources"]["volume_liquidity_ratio_5m"])

    def test_exits_on_liquidity_decay(self):
        decision = self.policy.evaluate(
            self.position,
            market=dict(self.market, dex_liquidity_usd=9_999.0),
            current_price=2.5,
            now=self.now,
        )
        self.assertEqual(decision.action, "close")
        self.assertEqual(decision.reason, "retention_liquidity_decay")

    def test_exits_on_volume_decay(self):
        decision = self.policy.evaluate(
            self.position,
            market=dict(self.market, volume_liquidity_ratio_5m=0.19),
            current_price=2.5,
            now=self.now,
        )
        self.assertEqual(decision.action, "close")
        self.assertEqual(decision.reason, "retention_volume_decay")

    def test_exits_on_new_trader_decay(self):
        decision = self.policy.evaluate(
            self.position,
            market=dict(self.market, new_traders_1h=1),
            current_price=2.5,
            now=self.now,
        )
        self.assertEqual(decision.action, "close")
        self.assertEqual(decision.reason, "retention_new_trader_decay")

    def test_exits_on_sell_pressure_and_drawdown(self):
        sell_pressure = self.policy.evaluate(
            self.position,
            market=dict(self.market, sell_pressure_5m=0.61),
            current_price=2.5,
            now=self.now,
        )
        self.assertEqual(sell_pressure.reason, "retention_sell_pressure")

        drawdown = self.policy.evaluate(
            self.position,
            market=self.market,
            current_price=1.2,
            now=self.now,
        )
        self.assertEqual(drawdown.reason, "retention_peak_drawdown")

    def test_missing_or_stale_health_data_fails_closed(self):
        missing = dict(self.market)
        missing.pop("dex_liquidity_usd")
        decision = self.policy.evaluate(self.position, market=missing, current_price=2.5, now=self.now)
        self.assertEqual(decision.action, "close")
        self.assertEqual(decision.reason, "retention_missing_health_data")
        self.assertIn("liquidity_usd", decision.missing_fields)
        self.assertIn("observed_at", decision.provenance["metric_sources"])

        stale = dict(self.market, observed_at=self.now.timestamp() - 121)
        decision = self.policy.evaluate(self.position, market=stale, current_price=2.5, now=self.now)
        self.assertEqual(decision.action, "close")
        self.assertEqual(decision.reason, "retention_stale_health_data")

    def test_future_and_max_hold_data_fail_closed(self):
        future = dict(self.market, observed_at=self.now.timestamp() + 1)
        decision = self.policy.evaluate(self.position, market=future, current_price=2.5, now=self.now)
        self.assertEqual(decision.action, "close")
        self.assertEqual(decision.reason, "retention_invalid_health_data")

        old_position = dict(self.position, runner_graduated_at=self.now.timestamp() - 4 * 86_400)
        decision = self.policy.evaluate(old_position, market=self.market, current_price=2.5, now=self.now)
        self.assertEqual(decision.action, "close")
        self.assertEqual(decision.reason, "retention_max_hold")


class PostGraduationMarketSnapshotTests(unittest.TestCase):
    def test_snapshot_prefers_explicit_adapter_fields_and_marks_graduation(self):
        position = {
            "runner_graduated": True,
            "post_graduation_market": {
                "dex_liquidity_usd": 12_000,
                "observed_at": 123,
                "data_source": "adapter",
                "price": 0.42,
            },
        }
        lifecycle = {
            "graduated": False,
            "dex_liquidity_usd": 1,
            "post_graduation_market": {"volume_liquidity_ratio_5m": 0.3},
        }
        snapshot = MemeBot._post_graduation_market_snapshot(
            position,
            lifecycle,
            {
                "liquidity_usd": 1,
                "volume_5m": 999,
                "post_graduation_market": {"new_traders_1h": 3},
            },
        )
        self.assertTrue(snapshot["graduated"])
        self.assertEqual(snapshot["dex_liquidity_usd"], 12_000)
        self.assertEqual(snapshot["volume_liquidity_ratio_5m"], 0.3)
        self.assertEqual(snapshot["new_traders_1h"], 3)
        self.assertNotIn("liquidity_usd", snapshot)
        self.assertNotIn("volume_5m", snapshot)
        self.assertEqual(snapshot["data_source"], "adapter")
        self.assertEqual(snapshot["price"], 0.42)

    def test_market_update_is_persisted_only_for_open_positions(self):
        bot, token = self._bot_for_position(shadow=True, enabled=False)
        self.assertTrue(
            bot.update_post_graduation_market(
                token,
                {"dex_liquidity_usd": 15_000, "observed_at": 123},
            )
        )
        self.assertEqual(bot.positions[token]["post_graduation_market"]["dex_liquidity_usd"], 15_000)
        self.assertEqual(
            bot.positions[token]["post_graduation_snapshots"][0]["observed_at"],
            123,
        )
        self.assertEqual(
            bot.collector.token_lifecycle[token]["post_graduation_market"]["observed_at"],
            123,
        )
        self.assertEqual(bot._save_state.call_count, 1)
        self.assertFalse(bot.update_post_graduation_market("0xMissing", {"dex_liquidity_usd": 1}))

    def _bot_for_position(self, *, shadow: bool, enabled: bool, market=None):
        bot = MemeBot.__new__(MemeBot)
        now = datetime.now()
        token = "0xToken"
        lifecycle = {
            "price_current": 2.5,
            "last_update": now.timestamp(),
            "create_timestamp": now.timestamp() - 120,
            "buys": [],
            "sells": [],
        }
        if market:
            lifecycle["post_graduation_market"] = dict(market)
        bot.active = True
        bot.collector = Mock(token_lifecycle={token: lifecycle})
        bot.positions = {
            token: {
                "symbol": "TK",
                "entry_price": 1.0,
                "tp_base_price": 1.0,
                "peak_price": 2.5,
                "runner_peak_price": 2.5,
                "entry_time": now,
                "runner_state": "active",
                "runner_graduated": True,
                "runner_graduated_at": now,
                "size_bnb": 0.1,
                "initial_size_bnb": 0.1,
            }
        }
        bot.hold_time_seconds = 86_400
        bot.stop_loss = -0.50
        bot.first_take_profit = 2.0
        bot.first_exit_ratio = 0.6
        bot.drawdown_stop = 0.25
        bot.min_policy_hold_seconds = 0
        bot.trailing_start_pct = None
        bot.trailing_stop_pct = None
        bot.rug_sell_pressure = None
        bot.hybrid = None
        bot.runner_reserve = Mock(config=Mock(enabled=False))
        bot.runner_reserve.evaluate.return_value = Mock(action="disabled")
        bot.post_graduation_retention_enabled = enabled
        bot.post_graduation_retention_shadow_enabled = shadow
        bot.post_graduation_retention = PostGraduationRetentionPolicy(
            RetentionHealthConfig(enabled=True, max_hold_seconds=86_400)
        )
        bot._close_position = AsyncMock()
        bot._save_state = Mock()
        bot._log_signal_audit = Mock()
        return bot, token

    def test_shadow_integration_records_missing_feed_without_closing(self):
        import asyncio

        bot, token = self._bot_for_position(shadow=True, enabled=False)
        asyncio.run(bot._process_token_logic(token))
        bot._close_position.assert_not_awaited()
        events = [call.args[0] for call in bot._log_signal_audit.call_args_list]
        retention_events = [event for event in events if event.get("action") == "POST_GRADUATION_RETENTION_DECISION"]
        self.assertEqual(len(retention_events), 1)
        self.assertEqual(retention_events[0]["mode"], "shadow")
        self.assertEqual(retention_events[0]["reason"], "retention_missing_health_data")

    def test_enforced_integration_closes_on_missing_feed(self):
        import asyncio

        bot, token = self._bot_for_position(shadow=False, enabled=True)
        asyncio.run(bot._process_token_logic(token))
        bot._close_position.assert_awaited_once_with(token, reason="RETENTION_MISSING_HEALTH_DATA")

    def test_enforced_integration_holds_healthy_snapshot(self):
        import asyncio

        now = datetime.now().timestamp()
        market = {
            "graduated": True,
            "observed_at": now,
            "data_source": "adapter",
            "dex_price": 3.0,
            "dex_liquidity_usd": 20_000,
            "volume_liquidity_ratio_5m": 0.3,
            "new_traders_1h": 3,
            "sell_pressure_5m": 0.2,
        }
        bot, token = self._bot_for_position(shadow=False, enabled=True, market=market)
        asyncio.run(bot._process_token_logic(token))
        bot._close_position.assert_not_awaited()
        self.assertEqual(bot.positions[token]["retention_state"], "active")
        self.assertEqual(bot.positions[token]["retention_data_source"], "adapter")
        self.assertEqual(bot.positions[token]["runner_peak_price"], 3.0)

    def test_enforced_retention_defers_legacy_short_timeout(self):
        import asyncio

        now = datetime.now().timestamp()
        market = {
            "graduated": True,
            "observed_at": now,
            "data_source": "adapter",
            "dex_liquidity_usd": 20_000,
            "volume_liquidity_ratio_5m": 0.3,
            "new_traders_1h": 3,
            "sell_pressure_5m": 0.2,
        }
        bot, token = self._bot_for_position(shadow=False, enabled=True, market=market)
        bot.hold_time_seconds = 300
        bot.positions[token]["entry_time"] = datetime.now() - timedelta(seconds=600)
        asyncio.run(bot._process_token_logic(token))
        bot._close_position.assert_not_awaited()

    def test_trade_stop_does_not_force_close_non_runner_position(self):
        import asyncio

        bot, token = self._bot_for_position(shadow=False, enabled=False)
        bot.runner_reserve.should_survive_graduation.return_value = False
        bot.collector.on_trade_stop = Mock()
        bot._enqueue_analysis_token = AsyncMock()
        asyncio.run(bot._on_trade_stop("TradeStop", {"args": {"token": token}}))
        bot._close_position.assert_not_awaited()
        self.assertTrue(bot.positions[token]["graduation_observed"])

if __name__ == "__main__":
    unittest.main()

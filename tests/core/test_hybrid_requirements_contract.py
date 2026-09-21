import re
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
REQ_FILE = ROOT / "requirements.txt"


class TestHybridRequirementsContract(unittest.TestCase):
    def _requirement_names(self):
        names = set()
        for raw in REQ_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip().lower()
            if name:
                names.add(name)
        return names

    def test_hybrid_dependencies_are_declared(self):
        names = self._requirement_names()
        expected = {"catboost", "gymnasium", "stable-baselines3", "torch"}
        missing = sorted(expected - names)
        self.assertFalse(missing, f"Missing dependencies: {missing}")


class TestTradeExecutorQuoteStatusContract(unittest.TestCase):
    def test_check_token_quote_supported_handles_helper_exception_as_not_ready(self):
        from src.core.trader import TradeExecutor
        import asyncio

        executor = TradeExecutor.__new__(TradeExecutor)
        executor._get_token_info_from_helper = AsyncMock(side_effect=RuntimeError("rpc timeout"))

        status = asyncio.run(executor.check_token_quote_supported("0xToken"))

        self.assertFalse(status["ready"])
        self.assertIsNone(status["quote"])
        self.assertIn("Helper query failed for quote asset", status["reason"])

    def test_check_token_status_rejects_non_native_quote_asset(self):
        from src.core.trader import TradeExecutor
        import asyncio

        executor = TradeExecutor.__new__(TradeExecutor)
        executor._get_token_info_from_helper = AsyncMock(
            return_value={
                "version": 2,
                "tokenManager": "0x5c952063c7fc8610FFDB798152D69F0B9550762b",
                "quote": "0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d",
                "lastPrice": 123,
                "launchTime": 1,
                "offers": 1,
                "maxOffers": 2,
                "funds": 1,
                "maxFunds": 10,
                "liquidityAdded": False,
            }
        )

        status = asyncio.run(executor.check_token_status("0xToken"))

        self.assertFalse(status["ready"])
        self.assertEqual(status["quote"], "0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d")
        self.assertIn("Unsupported quote asset", status["reason"])


class TestPredReturnFilterStartupContract(unittest.TestCase):
    def setUp(self):
        self._runtime_tmp = tempfile.TemporaryDirectory()
        self.runtime_dir = Path(self._runtime_tmp.name)

    def tearDown(self):
        self._runtime_tmp.cleanup()

    def _base_config(self, model_dir: str, **overrides):
        config = {
            "w3": MagicMock(),
            "model_dir": model_dir,
            "initial_balance": 1.0,
            "min_entry_volume_30s": 0.0,
            "min_entry_price_volatility": 0.0,
            "signal_audit_file": str(self.runtime_dir / "signal_audit.jsonl"),
            "trade_file": str(self.runtime_dir / "paper_trades.jsonl"),
            "state_file": str(self.runtime_dir / "bot_state.json"),
        }
        config.update(overrides)
        return config

    def _patch_bot_deps(self, collector=None):
        return patch.multiple(
            "src.trader.bot",
            TradeExecutor=MagicMock(return_value=MagicMock()),
            DataCollector=MagicMock(return_value=collector or MagicMock()),
            FourMemeListener=MagicMock(return_value=MagicMock()),
            WSConnectionManager=MagicMock(return_value=MagicMock()),
        )

    def _create_model_dir(self):
        tmp = tempfile.TemporaryDirectory()
        Path(tmp.name, "buy_model.cbm").write_text("x", encoding="utf-8")
        return tmp

    def test_runtime_file_paths_are_configurable(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        state_path = self.runtime_dir / "custom_state.json"
        trade_path = self.runtime_dir / "custom_trades.jsonl"
        audit_path = self.runtime_dir / "custom_signals.jsonl"

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    state_file=str(state_path),
                    trade_file=str(trade_path),
                    signal_audit_file=str(audit_path),
                )
            )

        self.assertEqual(bot.state_file, state_path)
        self.assertEqual(bot.trade_file, trade_path)
        self.assertEqual(bot.signal_audit_file, audit_path)

    def test_runtime_model_dir_reads_env_override(self):
        from src.trader.bot import _runtime_model_dir

        with patch.dict("os.environ", {"MODEL_DIR": "data/models/pinned-live"}, clear=False):
            self.assertEqual(_runtime_model_dir(), "data/models/pinned-live")

        with patch.dict("os.environ", {"MODEL_DIR": "   "}, clear=False):
            self.assertEqual(_runtime_model_dir(), "data/models/20260519_v95_v84_selective_nearmiss_gate")

    def test_near_threshold_env_values_are_explicit_manifest_overrides(self):
        from src.trader.bot import _model_manifest_manual_override_keys

        with patch.dict(
            "os.environ",
            {
                "BUY_NEAR_THRESHOLD_MIN_PROB": "0.94",
                "BUY_NEAR_MIN_PRED_RETURN": "",
                "BUY_NEAR_MIN_ENTRY_VOLUME_30S": "1.25",
                "BUY_NEAR_MIN_ENTRY_PRICE_VOLATILITY": " ",
                "BUY_NEAR_MIN_AGE_SECONDS": "0",
                "BUY_PRIMARY_SCORE_RESCUE_MIN_PROB": "0.985",
                "BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN": "",
                "BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S": "3.0",
                "BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY": " ",
                "BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS": "0",
                "MAX_CONCURRENT_POSITIONS": "8",
            },
            clear=False,
        ):
            self.assertEqual(
                _model_manifest_manual_override_keys(),
                {
                    "buy_near_threshold_min_prob",
                    "buy_near_min_entry_volume_30s",
                    "buy_near_min_age_seconds",
                    "buy_primary_score_rescue_min_prob",
                    "buy_primary_score_rescue_min_entry_volume_30s",
                    "buy_primary_score_rescue_min_age_seconds",
                    "max_concurrent_positions",
                },
            )

    def test_live_startup_validates_near_threshold_env_bounds(self):
        from config.trading_config import TradingConfig
        from src.trader.bot import _validate_live_trading_config

        with patch("config.config.Config.validate_rpc_config", return_value=True), patch.object(
            TradingConfig,
            "BUY_NEAR_THRESHOLD_MIN_PROB",
            0.0,
        ):
            with self.assertRaisesRegex(ValueError, "BUY_NEAR_THRESHOLD_MIN_PROB"):
                _validate_live_trading_config()

    def test_near_threshold_manual_probability_must_be_positive(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.98
        supported_hybrid.sell_policy = None

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            with self.assertRaisesRegex(ValueError, "buy_near_threshold_min_prob"):
                MemeBot(
                    self._base_config(
                        model_dir,
                        buy_near_threshold_min_prob=0.0,
                    )
                )

    def test_model_parent_loader_skips_latest_no_trade_artifact(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        with tempfile.TemporaryDirectory() as tmpdir:
            model_root = Path(tmpdir)
            tradable = model_root / "20260513_v34_live"
            blocked = model_root / "20260513_v37_blocked"
            tradable.mkdir()
            blocked.mkdir()
            Path(tradable, "buy_model.cbm").write_text("x", encoding="utf-8")
            Path(tradable, "buy_threshold.json").write_text(json.dumps({"threshold": 0.98}), encoding="utf-8")
            Path(blocked, "buy_model.cbm").write_text("x", encoding="utf-8")
            Path(blocked, "buy_threshold.json").write_text(json.dumps({"threshold": 1.0}), encoding="utf-8")
            Path(blocked, "hybrid_manifest.json").write_text(
                json.dumps(
                    {
                        "artifacts": {
                            "buy_model": {
                                "risk_tuning": {"status": "infeasible"},
                            }
                        },
                        "evaluation": {
                            "runtime_replay": {"total_trades": 0},
                            "total_trades": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )

            loaded_paths = []

            def load_model(path):
                loaded_paths.append(Path(path))
                return supported_hybrid

            with self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", side_effect=load_model):
                bot = MemeBot(self._base_config(str(model_root)))

        self.assertEqual(loaded_paths[-1].name, "20260513_v34_live")
        self.assertEqual(bot.model_path.name, "20260513_v34_live")

    def test_model_parent_loader_prefers_best_replay_not_latest_directory(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        def write_model_artifact(path: Path, *, net_return: float, drawdown: float, worst_return: float):
            path.mkdir()
            Path(path, "buy_model.cbm").write_text("x", encoding="utf-8")
            Path(path, "buy_threshold.json").write_text(json.dumps({"threshold": 0.97}), encoding="utf-8")
            Path(path, "hybrid_manifest.json").write_text(
                json.dumps(
                    {
                        "evaluation": {
                            "total_trades": 50,
                            "net_return_pct": net_return,
                            "max_drawdown_pct": drawdown,
                            "preferred_max_drawdown_pct": -30.0,
                            "walk_forward_worst_net_return_pct": worst_return,
                        }
                    }
                ),
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            model_root = Path(tmpdir)
            best = model_root / "20260516_v65_pf10_hold40_tr26_10"
            latest_weaker = model_root / "20260516_latest_weaker"
            write_model_artifact(best, net_return=337.0, drawdown=-25.0, worst_return=69.0)
            write_model_artifact(latest_weaker, net_return=88.0, drawdown=-6.0, worst_return=6.0)

            loaded_paths = []

            def load_model(path):
                loaded_paths.append(Path(path))
                return supported_hybrid

            with self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", side_effect=load_model):
                bot = MemeBot(self._base_config(str(model_root)))

        self.assertEqual(loaded_paths[-1].name, "20260516_v65_pf10_hold40_tr26_10")
        self.assertEqual(bot.model_path.name, "20260516_v65_pf10_hold40_tr26_10")

    def test_model_parent_loader_prefers_reviewed_current_selection_over_legacy_manifest(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        def write_model_artifact(path: Path, *, net_return: float, selected: bool):
            path.mkdir()
            Path(path, "buy_model.cbm").write_text("x", encoding="utf-8")
            Path(path, "buy_threshold.json").write_text(json.dumps({"threshold": 0.97}), encoding="utf-8")
            payload = {
                "evaluation": {
                    "total_trades": 50,
                    "net_return_pct": net_return,
                    "max_drawdown_pct": -10.0,
                    "preferred_max_drawdown_pct": -30.0,
                    "walk_forward_worst_net_return_pct": 20.0,
                }
            }
            if selected:
                payload["selection"] = {
                    "source_replay_report": "data/replay_reports/current_aligned.json",
                    "execution_calibration": "data/execution_calibration/current.json",
                }
            Path(path, "hybrid_manifest.json").write_text(json.dumps(payload), encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmpdir:
            model_root = Path(tmpdir)
            legacy_high = model_root / "20260509_legacy_high_old_replay"
            reviewed = model_root / "20260515_reviewed_current_aligned"
            write_model_artifact(legacy_high, net_return=10000.0, selected=False)
            write_model_artifact(reviewed, net_return=337.0, selected=True)

            loaded_paths = []

            def load_model(path):
                loaded_paths.append(Path(path))
                return supported_hybrid

            with self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", side_effect=load_model):
                bot = MemeBot(self._base_config(str(model_root)))

        self.assertEqual(loaded_paths[-1].name, "20260515_reviewed_current_aligned")
        self.assertEqual(bot.model_path.name, "20260515_reviewed_current_aligned")

    def test_model_parent_loader_rejects_higher_return_reviewed_artifact_when_stress_collapses(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        def write_reviewed_artifact(path: Path, *, net_return: float, stress_return: float):
            path.mkdir()
            Path(path, "buy_model.cbm").write_text("x", encoding="utf-8")
            Path(path, "buy_threshold.json").write_text(json.dumps({"threshold": 0.97}), encoding="utf-8")
            manifest = {
                "evaluation": {
                    "total_trades": 50,
                    "net_return_pct": net_return,
                    "net_profit_bnb": net_return / 100.0,
                    "max_drawdown_pct": -20.0,
                    "preferred_max_drawdown_pct": -30.0,
                    "walk_forward_worst_net_return_pct": 20.0,
                    "stress_replay": [
                        {
                            "name": "harsh_friction",
                            "net_profit_bnb": -0.01,
                            "net_return_pct": stress_return,
                            "max_drawdown_pct": stress_return,
                        }
                    ],
                },
                "selection": {
                    "source_replay_report": "data/replay_reports/current_aligned.json",
                    "execution_calibration": "data/execution_calibration/current.json",
                },
            }
            Path(path, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmpdir:
            model_root = Path(tmpdir)
            stable = model_root / "20260515_stable_reviewed"
            fragile = model_root / "20260515_fragile_higher_return_reviewed"
            write_reviewed_artifact(stable, net_return=100.0, stress_return=-5.0)
            write_reviewed_artifact(fragile, net_return=120.0, stress_return=-99.0)

            loaded_paths = []

            def load_model(path):
                loaded_paths.append(Path(path))
                return supported_hybrid

            with self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", side_effect=load_model):
                bot = MemeBot(self._base_config(str(model_root)))

        self.assertEqual(loaded_paths[-1].name, "20260515_stable_reviewed")
        self.assertEqual(bot.model_path.name, "20260515_stable_reviewed")

    def test_use_pred_return_filter_true_fails_fast_when_artifacts_unsupported(self):
        from src.trader.bot import MemeBot

        class _UnsupportedHybrid:
            buy_threshold = 0.5
            sell_policy = None

        unsupported_hybrid = _UnsupportedHybrid()

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=unsupported_hybrid):
            with self.assertRaisesRegex(ValueError, r"use_pred_return_filter=true.*predicted-return support"):
                MemeBot(self._base_config(model_dir, use_pred_return_filter=True))

    def test_use_pred_return_filter_true_allows_supported_artifacts(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_return = MagicMock(return_value=1.0)

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, use_pred_return_filter=True))

        self.assertTrue(bot.use_pred_return_filter)

    def test_use_pred_return_filter_true_fails_when_model_load_errors(self):
        from src.trader.bot import MemeBot

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(ValueError, r"use_pred_return_filter=true.*predicted-return support"):
                MemeBot(self._base_config(model_dir, use_pred_return_filter=True))

    def test_use_pred_return_filter_true_allows_inherited_predict_return(self):
        from src.trader.bot import MemeBot

        class _BaseHybrid:
            def predict_return(self, *args, **kwargs):
                return 1.0

        class _InheritedHybrid(_BaseHybrid):
            pass

        supported_hybrid = _InheritedHybrid()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, use_pred_return_filter=True))

        self.assertTrue(bot.use_pred_return_filter)

    def test_legacy_return_model_file_does_not_bypass_single_switch_contract(self):
        from src.trader.bot import MemeBot

        class _UnsupportedHybrid:
            buy_threshold = 0.5
            sell_policy = None

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=_UnsupportedHybrid()):
            Path(model_dir, "return_model.cbm").write_text("legacy", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"use_pred_return_filter=true.*predicted-return support"):
                MemeBot(self._base_config(model_dir, use_pred_return_filter=True))

    def test_pred_return_filter_blocks_buy_when_predicted_return_is_below_threshold(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.9, True)
        supported_hybrid.predict_return.return_value = 10.0

        collector = MagicMock()
        collector._extract_features.return_value = {"current_price": 1.0}
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            }
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, use_pred_return_filter=True, min_pred_return=80.0))
            bot._enqueue_buy_signal = AsyncMock()
            import asyncio
            asyncio.run(bot._process_token_logic("0xToken"))

        bot._enqueue_buy_signal.assert_not_called()

    def test_signal_audit_logs_model_reject_reason_and_feature_hash(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.9, True)
        supported_hybrid.predict_return.return_value = 10.0

        now_ts = datetime.now().timestamp()
        collector = MagicMock()
        collector._extract_features.return_value = {"current_price": 1.0, "buy_pressure": 0.8}
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.0,
                "last_update": now_ts - 3.0,
                "last_update_local": now_ts - 0.4,
                "create_timestamp": now_ts - 123.0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            audit_path = Path(tmpdir) / "signals.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    use_pred_return_filter=True,
                    min_pred_return=80.0,
                    signal_audit_file=str(audit_path),
                )
            )
            asyncio.run(bot._process_token_logic("0xToken"))

            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "SIGNAL_DECISION")
        self.assertEqual(rows[0]["token"], "0xToken")
        self.assertEqual(rows[0]["decision"], "rejected")
        self.assertEqual(rows[0]["reason"], "pred_return_below_min")
        self.assertEqual(rows[0]["prob"], 0.9)
        self.assertEqual(rows[0]["pred_return"], 10.0)
        self.assertRegex(rows[0]["features_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual(rows[0]["feature_count"], 2)
        self.assertTrue(rows[0]["lifecycle_status_has_local_update"])
        self.assertTrue(rows[0]["lifecycle_status_has_chain_update"])
        self.assertLess(rows[0]["lifecycle_status_staleness_seconds"], 5.0)
        self.assertGreater(rows[0]["lifecycle_status_chain_lag_seconds"], 2.0)
        self.assertLess(rows[0]["lifecycle_status_chain_lag_seconds"], 10.0)
        self.assertTrue(rows[0]["lifecycle_status_fast_status_enabled"])
        self.assertTrue(rows[0]["lifecycle_status_fast_status_eligible"])

    def test_pred_return_filter_allows_buy_when_predicted_return_meets_threshold(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.9, True)
        supported_hybrid.predict_return.return_value = 100.0

        now_ts = datetime.now().timestamp()
        collector = MagicMock()
        collector._extract_features.return_value = {"current_price": 1.0}
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.0,
                "last_update": now_ts - 3.0,
                "last_update_local": now_ts - 0.4,
                "create_timestamp": now_ts - 123.0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            audit_path = Path(tmpdir) / "signals.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    use_pred_return_filter=True,
                    min_pred_return=80.0,
                    signal_audit_file=str(audit_path),
                )
            )
            bot._enqueue_buy_signal = AsyncMock(return_value="queued")
            import asyncio
            asyncio.run(bot._process_token_logic("0xToken"))
            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        bot._enqueue_buy_signal.assert_awaited_once()
        self.assertEqual(rows[-1]["action"], "SIGNAL_DECISION")
        self.assertEqual(rows[-1]["decision"], "queued")
        self.assertTrue(rows[-1]["lifecycle_status_has_local_update"])
        self.assertTrue(rows[-1]["lifecycle_status_has_chain_update"])
        self.assertLess(rows[-1]["lifecycle_status_staleness_seconds"], 5.0)
        self.assertGreater(rows[-1]["lifecycle_status_chain_lag_seconds"], 2.0)
        self.assertLess(rows[-1]["lifecycle_status_chain_lag_seconds"], 10.0)
        self.assertTrue(rows[-1]["lifecycle_status_fast_status_enabled"])
        self.assertTrue(rows[-1]["lifecycle_status_fast_status_eligible"])

    def test_near_threshold_rescue_gate_accepts_qualified_near_candidate(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.98
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.949, False)
        supported_hybrid.predict_return.return_value = 32.5

        collector = MagicMock()
        collector._extract_features.return_value = {
            "current_price": 1.0,
            "volume_30s": 1.25,
            "price_volatility": 0.08,
        }

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 120,
            "create_timestamp": 0,
            "unique_buyers": {"a", "b", "c"},
            "buys": [1, 2, 3, 4, 5],
            "sells": [],
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    prob_threshold=0.98,
                    use_pred_return_filter=True,
                    min_pred_return=35.0,
                    max_age_seconds=300,
                    min_entry_volume_30s=1.5,
                    min_entry_price_volatility=0.1,
                    buy_near_threshold_min_prob=0.94,
                    buy_near_min_pred_return=32.0,
                    buy_near_min_entry_volume_30s=1.25,
                    buy_near_min_entry_price_volatility=0.08,
                    buy_near_min_age_seconds=0,
                )
            )

        prob, should_buy, pred_return, _features, reject_reason = bot._run_model_inference(lifecycle)

        self.assertEqual(prob, 0.949)
        self.assertEqual(pred_return, 32.5)
        self.assertTrue(should_buy)
        self.assertIsNone(reject_reason)

    def test_near_threshold_rescue_gate_is_disabled_without_manifest_or_manual_config(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.98
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.949, False)
        supported_hybrid.predict_return.return_value = 32.5

        collector = MagicMock()
        collector._extract_features.return_value = {
            "current_price": 1.0,
            "volume_30s": 1.25,
            "price_volatility": 0.08,
        }

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 120,
            "create_timestamp": 0,
            "unique_buyers": {"a", "b", "c"},
            "buys": [1, 2, 3, 4, 5],
            "sells": [],
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    prob_threshold=0.98,
                    use_pred_return_filter=True,
                    min_pred_return=35.0,
                    max_age_seconds=300,
                    min_entry_volume_30s=1.5,
                    min_entry_price_volatility=0.1,
                )
            )

        prob, should_buy, pred_return, _features, reject_reason = bot._run_model_inference(lifecycle)

        self.assertEqual(prob, 0.949)
        self.assertEqual(pred_return, 32.5)
        self.assertFalse(should_buy)
        self.assertEqual(reject_reason, "buy_model_reject")

    def test_near_threshold_rescue_gate_rejects_stale_candidate(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.98
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.949, False)
        supported_hybrid.predict_return.return_value = 32.5

        collector = MagicMock()
        collector._extract_features.return_value = {
            "current_price": 1.0,
            "volume_30s": 1.25,
            "price_volatility": 0.08,
        }

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 360,
            "create_timestamp": 0,
            "unique_buyers": {"a", "b", "c"},
            "buys": [1, 2, 3, 4, 5],
            "sells": [],
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    prob_threshold=0.98,
                    use_pred_return_filter=True,
                    min_pred_return=35.0,
                    max_age_seconds=300,
                    min_entry_volume_30s=1.5,
                    min_entry_price_volatility=0.1,
                    buy_near_threshold_min_prob=0.94,
                    buy_near_min_pred_return=32.0,
                    buy_near_min_entry_volume_30s=1.25,
                    buy_near_min_entry_price_volatility=0.08,
                    buy_near_min_age_seconds=0,
                )
            )

        prob, should_buy, pred_return, _features, reject_reason = bot._run_model_inference(lifecycle)

        self.assertEqual(prob, 0.949)
        self.assertEqual(pred_return, 32.5)
        self.assertFalse(should_buy)
        self.assertEqual(reject_reason, "near_threshold_age_out_of_range")

    def test_signal_audit_marks_near_threshold_rescue_acceptance(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.98
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.949, False)
        supported_hybrid.predict_return.return_value = 32.5

        collector = MagicMock()
        collector._extract_features.return_value = {
            "current_price": 1.0,
            "volume_30s": 1.25,
            "price_volatility": 0.08,
        }
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            audit_path = Path(tmpdir) / "signals.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    prob_threshold=0.98,
                    use_pred_return_filter=True,
                    min_pred_return=35.0,
                    max_age_seconds=300,
                    min_entry_volume_30s=1.5,
                    min_entry_price_volatility=0.1,
                    buy_near_threshold_min_prob=0.94,
                    buy_near_min_pred_return=32.0,
                    buy_near_min_entry_volume_30s=1.25,
                    buy_near_min_entry_price_volatility=0.08,
                    buy_near_min_age_seconds=0,
                    signal_audit_file=str(audit_path),
                )
            )
            bot._enqueue_buy_signal = AsyncMock(return_value="queued")
            asyncio.run(bot._process_token_logic("0xToken"))
            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(rows[-1]["action"], "SIGNAL_DECISION")
        self.assertEqual(rows[-1]["decision"], "queued")
        self.assertTrue(rows[-1]["near_threshold_rescue_used"])
        self.assertEqual(rows[-1]["buy_near_threshold_min_prob"], 0.94)
        self.assertEqual(rows[-1]["buy_near_min_pred_return"], 32.0)

    def test_primary_score_rescue_gate_accepts_qualified_primary_candidate(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.98
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.991, True)
        supported_hybrid.predict_return.return_value = 25.0

        collector = MagicMock()
        collector._extract_features.return_value = {
            "current_price": 1.0,
            "volume_30s": 3.5,
            "price_volatility": 0.32,
        }

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 120,
            "create_timestamp": 0,
            "unique_buyers": {"a", "b", "c"},
            "buys": [1, 2, 3, 4, 5],
            "sells": [],
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    prob_threshold=0.98,
                    use_pred_return_filter=True,
                    min_pred_return=35.0,
                    max_age_seconds=300,
                    min_entry_volume_30s=1.5,
                    min_entry_price_volatility=0.1,
                    buy_primary_score_rescue_min_prob=0.985,
                    buy_primary_score_rescue_min_pred_return=25.0,
                    buy_primary_score_rescue_min_entry_volume_30s=3.0,
                    buy_primary_score_rescue_min_entry_price_volatility=0.30,
                    buy_primary_score_rescue_min_age_seconds=0,
                )
            )

        prob, should_buy, pred_return, _features, reject_reason = bot._run_model_inference(lifecycle)

        self.assertEqual(prob, 0.991)
        self.assertEqual(pred_return, 25.0)
        self.assertTrue(should_buy)
        self.assertIsNone(reject_reason)

    def test_primary_score_rescue_gate_is_disabled_without_manifest_or_manual_config(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.98
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.991, True)
        supported_hybrid.predict_return.return_value = 25.0

        collector = MagicMock()
        collector._extract_features.return_value = {
            "current_price": 1.0,
            "volume_30s": 3.5,
            "price_volatility": 0.32,
        }

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 120,
            "create_timestamp": 0,
            "unique_buyers": {"a", "b", "c"},
            "buys": [1, 2, 3, 4, 5],
            "sells": [],
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    prob_threshold=0.98,
                    use_pred_return_filter=True,
                    min_pred_return=35.0,
                    max_age_seconds=300,
                    min_entry_volume_30s=1.5,
                    min_entry_price_volatility=0.1,
                )
            )

        prob, should_buy, pred_return, _features, reject_reason = bot._run_model_inference(lifecycle)

        self.assertEqual(prob, 0.991)
        self.assertEqual(pred_return, 25.0)
        self.assertFalse(should_buy)
        self.assertEqual(reject_reason, "pred_return_below_min")

    def test_selected_runtime_params_missing_primary_rescue_keys_do_not_fallback_to_evaluation(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.98
        supported_hybrid.sell_policy = None

        manifest = {
            "evaluation": {
                "buy_primary_score_rescue_min_prob": 0.985,
                "buy_primary_score_rescue_min_pred_return": 25.0,
                "buy_primary_score_rescue_min_entry_volume_30s": 3.0,
                "buy_primary_score_rescue_min_entry_price_volatility": 0.30,
                "buy_primary_score_rescue_min_age_seconds": 0.0,
            },
            "selected_runtime_params": {
                "buy_threshold": 0.98,
            },
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bot = MemeBot(self._base_config(model_dir))

        self.assertIsNone(bot.buy_primary_score_rescue_min_prob)
        self.assertIsNone(bot.buy_primary_score_rescue_min_pred_return)
        self.assertIsNone(bot.buy_primary_score_rescue_min_entry_volume_30s)
        self.assertIsNone(bot.buy_primary_score_rescue_min_entry_price_volatility)
        self.assertIsNone(bot.buy_primary_score_rescue_min_age_seconds)
        self.assertEqual(bot.primary_score_rescue_param_sources["buy_primary_score_rescue_min_prob"], "disabled")

    def test_selected_runtime_params_explicit_null_clears_optional_live_values_without_unlimiting_capacity(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.77
        supported_hybrid.sell_policy = None

        manifest = {
            "evaluation": {
                "fixed_stake_bnb": 0.1,
                "max_open_positions": 8,
            },
            "selected_runtime_params": {
                "fixed_stake_bnb": None,
                "max_open_positions": None,
            },
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    model_manifest_authoritative=True,
                    model_manifest_manual_override_keys=set(),
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=7,
                )
            )

        self.assertIsNone(bot.fixed_stake_bnb)
        self.assertEqual(bot.max_concurrent_positions, 7)
        self.assertEqual(bot.exit_param_sources["fixed_stake_bnb"], "model_manifest")
        self.assertEqual(bot.exit_param_sources["max_concurrent_positions"], "default")

    def test_selected_zero_max_open_positions_does_not_unlimit_live_capacity(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.77
        supported_hybrid.sell_policy = None

        manifest = {
            "evaluation": {"max_open_positions": 8},
            "selected_runtime_params": {"max_open_positions": 0},
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    model_manifest_authoritative=True,
                    model_manifest_manual_override_keys=set(),
                    max_concurrent_positions=7,
                )
            )

        self.assertEqual(bot.max_concurrent_positions, 7)
        self.assertEqual(bot.exit_param_sources["max_concurrent_positions"], "default")

    def test_signal_audit_marks_primary_score_rescue_acceptance(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.98
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.991, True)
        supported_hybrid.predict_return.return_value = 25.0

        collector = MagicMock()
        collector._extract_features.return_value = {
            "current_price": 1.0,
            "volume_30s": 3.5,
            "price_volatility": 0.32,
        }
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            audit_path = Path(tmpdir) / "signals.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    prob_threshold=0.98,
                    use_pred_return_filter=True,
                    min_pred_return=35.0,
                    max_age_seconds=300,
                    min_entry_volume_30s=1.5,
                    min_entry_price_volatility=0.1,
                    buy_primary_score_rescue_min_prob=0.985,
                    buy_primary_score_rescue_min_pred_return=25.0,
                    buy_primary_score_rescue_min_entry_volume_30s=3.0,
                    buy_primary_score_rescue_min_entry_price_volatility=0.30,
                    buy_primary_score_rescue_min_age_seconds=0,
                    signal_audit_file=str(audit_path),
                )
            )
            bot._enqueue_buy_signal = AsyncMock(return_value="queued")
            asyncio.run(bot._process_token_logic("0xToken"))
            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(rows[-1]["action"], "SIGNAL_DECISION")
        self.assertEqual(rows[-1]["decision"], "queued")
        self.assertTrue(rows[-1]["primary_score_rescue_used"])
        self.assertEqual(rows[-1]["buy_primary_score_rescue_min_prob"], 0.985)
        self.assertEqual(rows[-1]["buy_primary_score_rescue_min_pred_return"], 25.0)
        self.assertTrue(bot._enqueue_buy_signal.await_args.kwargs["primary_score_rescue_used"])

    def test_predict_return_none_is_optional_when_filter_disabled(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.9, True)
        supported_hybrid.predict_return.return_value = None

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, use_pred_return_filter=False))

        prob, should_buy, pred_return, _features, reject_reason = bot._run_model_inference(
            {
                "symbol": "TK",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            }
        )

        self.assertEqual(prob, 0.9)
        self.assertTrue(should_buy)
        self.assertIsNone(pred_return)
        self.assertIsNone(reject_reason)

    def test_predict_return_none_blocks_when_filter_enabled(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.9, True)
        supported_hybrid.predict_return.return_value = None

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, use_pred_return_filter=True, min_pred_return=80.0))

        prob, should_buy, pred_return, _features, reject_reason = bot._run_model_inference(
            {
                "symbol": "TK",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            }
        )

        self.assertEqual(prob, 0.9)
        self.assertFalse(should_buy)
        self.assertIsNone(pred_return)
        self.assertEqual(reject_reason, "pred_return_unavailable")

    def test_min_entry_volume_30s_filter_blocks_low_volume_model_buy(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.9, True)

        collector = MagicMock()
        collector._extract_features.return_value = {"current_price": 1.0, "volume_30s": 1.5}
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            }
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, min_entry_volume_30s=2.0))
            bot._enqueue_buy_signal = AsyncMock()
            import asyncio
            asyncio.run(bot._process_token_logic("0xToken"))

        bot._enqueue_buy_signal.assert_not_awaited()

    def test_min_entry_price_volatility_filter_blocks_low_volatility_model_buy(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.9, True)

        collector = MagicMock()
        collector._extract_features.return_value = {
            "current_price": 1.0,
            "volume_30s": 2.0,
            "price_volatility": 0.08,
        }
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            }
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, min_entry_price_volatility=0.10))
            bot._enqueue_buy_signal = AsyncMock()
            import asyncio
            asyncio.run(bot._process_token_logic("0xToken"))

        bot._enqueue_buy_signal.assert_not_awaited()

    def test_process_token_logic_uses_configured_entry_activity_gate(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.9, True)

        collector = MagicMock()
        collector._extract_features.return_value = {"current_price": 1.0}
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b"},
                "buys": [1, 2],
                "sells": [],
            }
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    min_entry_unique_buyers=2,
                    min_entry_buy_count=2,
                )
            )
            bot._enqueue_buy_signal = AsyncMock()
            import asyncio
            asyncio.run(bot._process_token_logic("0xToken"))

        bot._enqueue_buy_signal.assert_awaited_once()

    def test_analysis_queue_coalesces_duplicate_tokens_until_consumed(self):
        from src.trader.bot import MemeBot
        import asyncio

        bot = object.__new__(MemeBot)
        bot.analysis_event_queue_size = 10
        bot._analysis_event_queue = asyncio.Queue(maxsize=bot.analysis_event_queue_size)
        bot._queued_analysis_tokens = set()

        asyncio.run(bot._enqueue_analysis_token("0xToken"))
        asyncio.run(bot._enqueue_analysis_token("0xToken"))
        asyncio.run(bot._enqueue_analysis_token("0xOther"))

        queued = []
        while not bot._analysis_event_queue.empty():
            queued.append(bot._analysis_event_queue.get_nowait())

        self.assertEqual(queued, ["0xToken", "0xOther"])

    def test_buy_capacity_respects_fixed_stake_cash(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", False), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.05,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                )
            )

        self.assertFalse(bot._has_buy_capacity())

    def test_open_position_uses_fixed_stake_bnb_in_paper_mode(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 120,
            "create_timestamp": 0,
        }

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", False), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    position_size=0.5,
                    max_concurrent_positions=10,
                )
            )
            bot.state_file = Path(tmpdir) / "state.json"
            bot.trade_file = Path(tmpdir) / "trades.jsonl"
            asyncio.run(bot._open_position("0xToken", lifecycle, 0.9))

        self.assertAlmostEqual(bot.positions["0xToken"]["size_bnb"], 0.1)
        self.assertAlmostEqual(bot.balance, 0.4)

    def test_signal_audit_logs_open_position_execution_price(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.2,
            "last_update": 120,
            "create_timestamp": 0,
        }

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", False), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            audit_path = Path(tmpdir) / "signals.jsonl"
            trade_path = Path(tmpdir) / "trades.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    signal_audit_file=str(audit_path),
                )
            )
            bot.state_file = Path(tmpdir) / "state.json"
            bot.trade_file = Path(tmpdir) / "trades.jsonl"
            signal_time = datetime.now() - timedelta(seconds=3)
            asyncio.run(
                bot._open_position(
                    "0xToken",
                    lifecycle,
                    0.9,
                    pred_return=42.0,
                    signal_price=1.0,
                    signal_time=signal_time,
                    primary_score_rescue_used=True,
                )
            )
            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
            trade_rows = [json.loads(line) for line in trade_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(rows[-1]["action"], "POSITION_OPENED")
        self.assertEqual(rows[-1]["token"], "0xToken")
        self.assertEqual(rows[-1]["signal_price"], 1.0)
        self.assertEqual(rows[-1]["entry_price"], 1.2)
        self.assertAlmostEqual(rows[-1]["entry_slippage_pct"], 0.2)
        self.assertEqual(rows[-1]["pred_return"], 42.0)
        self.assertTrue(rows[-1]["primary_score_rescue_used"])
        self.assertTrue(trade_rows[-1]["primary_score_rescue_used"])
        self.assertIn("entry_signal_time", rows[-1])
        self.assertIn("entry_due_time", rows[-1])
        self.assertIn("entry_wait_seconds", rows[-1])
        self.assertIn("entry_fill_lag_seconds", rows[-1])
        self.assertAlmostEqual(rows[-1]["entry_wait_seconds"], rows[-1]["signal_to_open_seconds"])
        self.assertAlmostEqual(rows[-1]["entry_fill_lag_seconds"], rows[-1]["entry_wait_seconds"])

    def test_trailing_stop_deferred_audit_marks_primary_score_rescue_origin(self):
        from src.trader.bot import MemeBot
        import asyncio

        token = "0x" + "1" * 40
        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            audit_path = Path(tmpdir) / "signals.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    signal_audit_file=str(audit_path),
                    stop_loss=-0.9,
                    hold_time_seconds=300,
                    trailing_start_pct=0.2,
                    trailing_stop_pct=0.1,
                    rug_sell_pressure=None,
                )
            )
            bot.positions[token] = {
                "symbol": "TK",
                "entry_price": 1.0,
                "tp_base_price": 1.0,
                "peak_price": 1.5,
                "entry_time": datetime.now(),
                "size_bnb": 0.1,
                "primary_score_rescue_used": True,
            }
            bot.collector.token_lifecycle = {
                token: {
                    "symbol": "TK",
                    "price_current": 1.2,
                    "price_current_source": "helper",
                    "last_update": 120,
                    "create_timestamp": 0,
                }
            }
            asyncio.run(bot._process_token_logic(token))
            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(rows[-1]["action"], "TRAILING_STOP_DEFERRED")
        self.assertTrue(rows[-1]["primary_score_rescue_used"])

    def test_sell_execution_failed_audit_marks_primary_score_rescue_origin(self):
        from src.trader.bot import MemeBot
        import asyncio

        token = "0x" + "1" * 40
        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        executor = MagicMock()
        executor.wallet_address = "0xWallet"
        token_contract = MagicMock()
        token_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=10**18)
        executor.w3.eth.contract.return_value = token_contract
        executor.sell_token = AsyncMock(return_value=None)

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            audit_path = Path(tmpdir) / "signals.jsonl"
            bot = MemeBot(self._base_config(model_dir, signal_audit_file=str(audit_path)))
            bot.executor = executor
            result = asyncio.run(
                bot._do_sell(
                    token,
                    {
                        "symbol": "TK",
                        "signal_price": 1.0,
                        "entry_price": 1.0,
                        "primary_score_rescue_used": True,
                    },
                )
            )
            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        self.assertFalse(result)
        self.assertEqual(rows[-1]["action"], "SELL_EXECUTION_FAILED")
        self.assertTrue(rows[-1]["primary_score_rescue_used"])

    def test_do_sell_zero_balance_removes_position_and_saves_state(self):
        from src.trader.bot import MemeBot
        import asyncio

        token = "0x" + "2" * 40
        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        executor = MagicMock()
        executor.wallet_address = "0xWallet"
        token_contract = MagicMock()
        token_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=0)
        executor.w3.eth.contract.return_value = token_contract

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir))
            bot.executor = executor
            bot.state_file = Path(tmpdir) / "state.json"
            bot.signal_audit_file = Path(tmpdir) / "signals.jsonl"
            position = {
                "symbol": "TK",
                "entry_price": 1.0,
                "signal_price": 1.0,
                "tp_base_price": 1.0,
                "peak_price": 1.0,
                "entry_time": datetime.now() - timedelta(seconds=10),
                "size_bnb": 0.1,
                "initial_size_bnb": 0.1,
                "primary_score_rescue_used": True,
            }
            bot.positions = {token: position}
            result = asyncio.run(bot._do_sell(token, position, reason="TIME_EXIT"))
            state = json.loads(bot.state_file.read_text(encoding="utf-8"))
            audit_rows = [
                json.loads(line)
                for line in bot.signal_audit_file.read_text(encoding="utf-8").splitlines()
            ]

        self.assertIsNone(result)
        self.assertNotIn(token, bot.positions)
        self.assertIn(token, bot.closed_tokens)
        self.assertEqual(state["positions"], {})
        self.assertIn(token, state["closed_tokens"])
        self.assertEqual(audit_rows[-1]["action"], "POSITION_ZERO_BALANCE_REMOVED")
        self.assertEqual(audit_rows[-1]["reason"], "TIME_EXIT")
        self.assertTrue(audit_rows[-1]["primary_score_rescue_used"])
        self.assertGreater(audit_rows[-1]["hold_duration"], 0)

    def test_open_position_skips_chasing_price_above_manifest_protection(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.30,
            "last_update": 120,
            "create_timestamp": 0,
        }
        manifest = {"evaluation": {"entry_price_protection_pct": 0.25}}

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", False), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            audit_path = Path(tmpdir) / "signals.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    signal_audit_file=str(audit_path),
                )
            )
            bot.trade_file = Path(tmpdir) / "trades.jsonl"
            asyncio.run(bot._open_position("0xToken", lifecycle, 0.9, pred_return=42.0, signal_price=1.0))
            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(bot.entry_price_protection_pct, 0.25)
        self.assertNotIn("0xToken", bot.positions)
        self.assertAlmostEqual(bot.balance, 0.5)
        self.assertEqual(rows[-1]["action"], "ENTRY_PRICE_PROTECTION_SKIP")
        self.assertEqual(rows[-1]["signal_price"], 1.0)
        self.assertEqual(rows[-1]["candidate_price"], 1.3)
        self.assertAlmostEqual(rows[-1]["entry_slippage_pct"], 0.3)

    def test_real_open_position_uses_configured_buy_confirmation_poll_interval(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 120,
            "create_timestamp": 0,
        }
        executor = MagicMock()
        executor.wallet_address = "0xWallet"
        executor.check_token_status = AsyncMock(
            return_value={
                "ready": True,
                "price": 1.0,
                "reason": "OK",
            }
        )
        executor.buy_token = AsyncMock(return_value="0xbuy")
        token_contract = MagicMock()
        token_contract.functions.balanceOf.return_value.call = AsyncMock(side_effect=[0, 10**18])
        executor.w3.eth.contract.return_value = token_contract
        executor.w3.eth.get_balance = AsyncMock(return_value=400000000000000000)
        executor.w3.from_wei.return_value = 0.4

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, patch.multiple(
            "src.trader.bot",
            TradeExecutor=MagicMock(return_value=executor),
            DataCollector=MagicMock(return_value=MagicMock()),
            FourMemeListener=MagicMock(return_value=MagicMock()),
            WSConnectionManager=MagicMock(return_value=MagicMock()),
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid), patch("src.trader.bot.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            audit_path = Path(tmpdir) / "signals.jsonl"
            trade_path = Path(tmpdir) / "trades.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    buy_confirm_poll_interval_seconds=0.25,
                    buy_confirm_timeout_seconds=10,
                    signal_audit_file=str(audit_path),
                )
            )
            bot.state_file = Path(tmpdir) / "state.json"
            bot.trade_file = trade_path
            asyncio.run(bot._open_position("0xToken", lifecycle, 0.99, pred_return=13.0, signal_price=1.0))
            trade_rows = [json.loads(line) for line in trade_path.read_text(encoding="utf-8").splitlines()]

        sleep_mock.assert_awaited_once_with(0.25)
        self.assertIn("0xToken", bot.positions)
        self.assertEqual(trade_rows[-1]["buy_confirm_poll_interval_seconds"], 0.25)
        self.assertIn("buy_preflight_seconds", trade_rows[-1])
        self.assertGreaterEqual(trade_rows[-1]["buy_preflight_seconds"], 0.0)
        self.assertIn("token_status_check_seconds", trade_rows[-1])
        self.assertGreaterEqual(trade_rows[-1]["token_status_check_seconds"], 0.0)
        self.assertIn("buy_tx_submit_rpc_seconds", trade_rows[-1])
        self.assertIn("buy_token_detect_seconds", trade_rows[-1])
        self.assertIn("buy_post_detect_sync_seconds", trade_rows[-1])

    def test_real_open_position_checks_token_balance_before_first_confirmation_sleep(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 120,
            "create_timestamp": 0,
        }
        events = []
        executor = MagicMock()
        executor.wallet_address = "0xWallet"
        executor.check_token_status = AsyncMock(
            return_value={
                "ready": True,
                "price": 1.0,
                "reason": "OK",
            }
        )
        executor.buy_token = AsyncMock(return_value="0xbuy")

        async def balance_call():
            events.append("balance")
            return 10**18

        async def sleep_call(delay):
            events.append(f"sleep:{delay}")

        token_contract = MagicMock()
        token_contract.functions.balanceOf.return_value.call = AsyncMock(side_effect=balance_call)
        executor.w3.eth.contract.return_value = token_contract
        executor.w3.eth.get_balance = AsyncMock(return_value=400000000000000000)
        executor.w3.from_wei.return_value = 0.4

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, patch.multiple(
            "src.trader.bot",
            TradeExecutor=MagicMock(return_value=executor),
            DataCollector=MagicMock(return_value=MagicMock()),
            FourMemeListener=MagicMock(return_value=MagicMock()),
            WSConnectionManager=MagicMock(return_value=MagicMock()),
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid), patch("src.trader.bot.asyncio.sleep", side_effect=sleep_call):
            trade_path = Path(tmpdir) / "trades.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    buy_confirm_poll_interval_seconds=0.25,
                    buy_confirm_timeout_seconds=10,
                    signal_audit_file=str(Path(tmpdir) / "signals.jsonl"),
                )
            )
            bot.state_file = Path(tmpdir) / "state.json"
            bot.trade_file = trade_path
            asyncio.run(bot._open_position("0xToken", lifecycle, 0.99, pred_return=13.0, signal_price=1.0))
            trade_rows = [json.loads(line) for line in trade_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(events, ["balance"])
        self.assertEqual(trade_rows[-1]["buy_detect_poll_count"], 1)

    def test_real_open_position_defers_balance_sync_after_recording_position(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 120,
            "create_timestamp": 0,
        }
        executor = MagicMock()
        executor.wallet_address = "0xWallet"
        executor.check_token_status = AsyncMock(
            return_value={
                "ready": True,
                "price": 1.0,
                "reason": "OK",
            }
        )
        executor.buy_token = AsyncMock(return_value="0xbuy")
        token_contract = MagicMock()
        token_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=10**18)
        executor.w3.eth.contract.return_value = token_contract

        async def run_case():
            sync_started = asyncio.Event()
            sync_release = asyncio.Event()

            async def slow_sync_balance(bot_self, force=False):
                sync_started.set()
                await sync_release.wait()
                bot_self.balance = 0.39

            with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, patch.multiple(
                "src.trader.bot",
                TradeExecutor=MagicMock(return_value=executor),
                DataCollector=MagicMock(return_value=MagicMock()),
                FourMemeListener=MagicMock(return_value=MagicMock()),
                WSConnectionManager=MagicMock(return_value=MagicMock()),
            ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch.object(MemeBot, "_sync_balance", slow_sync_balance), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
                trade_path = Path(tmpdir) / "trades.jsonl"
                bot = MemeBot(
                    self._base_config(
                        model_dir,
                        initial_balance=0.5,
                        fixed_stake_bnb=0.1,
                        max_concurrent_positions=10,
                        buy_confirm_poll_interval_seconds=0.25,
                        buy_confirm_timeout_seconds=10,
                        signal_audit_file=str(Path(tmpdir) / "signals.jsonl"),
                    )
                )
                bot.state_file = Path(tmpdir) / "state.json"
                bot.trade_file = trade_path

                await asyncio.wait_for(
                    bot._open_position("0xToken", lifecycle, 0.99, pred_return=13.0, signal_price=1.0),
                    timeout=0.2,
                )
                self.assertAlmostEqual(bot.balance, 0.4)
                await asyncio.wait_for(sync_started.wait(), timeout=0.2)
                sync_release.set()
                if bot._background_tasks:
                    await asyncio.gather(*list(bot._background_tasks), return_exceptions=True)
                trade_rows = [json.loads(line) for line in trade_path.read_text(encoding="utf-8").splitlines()]
                return trade_rows

        trade_rows = asyncio.run(run_case())

        self.assertIn("0xToken", trade_rows[-1]["token"])
        self.assertEqual(trade_rows[-1]["buy_post_detect_sync_seconds"], 0.0)

    def test_real_open_position_uses_fresh_lifecycle_fast_status_before_helper(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.01,
            "price_first": 0.90,
            "price_max": 1.20,
            "last_update": datetime.now().timestamp(),
            "create_timestamp": 0,
            "graduated": False,
        }
        executor = MagicMock()
        executor.wallet_address = "0xWallet"
        executor.check_token_status = AsyncMock(
            return_value={
                "ready": True,
                "price": 1.0,
                "reason": "OK",
            }
        )
        executor.check_token_quote_supported = AsyncMock(
            return_value={
                "ready": True,
                "quote": "0x0000000000000000000000000000000000000000",
                "reason": "OK",
            }
        )
        executor.buy_token = AsyncMock(return_value="0xbuy")
        token_contract = MagicMock()
        token_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=10**18)
        executor.w3.eth.contract.return_value = token_contract
        executor.w3.eth.get_balance = AsyncMock(return_value=400000000000000000)
        executor.w3.from_wei.return_value = 0.4

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, patch.multiple(
            "src.trader.bot",
            TradeExecutor=MagicMock(return_value=executor),
            DataCollector=MagicMock(return_value=MagicMock()),
            FourMemeListener=MagicMock(return_value=MagicMock()),
            WSConnectionManager=MagicMock(return_value=MagicMock()),
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            audit_path = Path(tmpdir) / "signals.jsonl"
            trade_path = Path(tmpdir) / "trades.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    entry_price_protection_pct=0.25,
                    buy_confirm_poll_interval_seconds=0.25,
                    buy_confirm_timeout_seconds=10,
                    signal_audit_file=str(audit_path),
                )
            )
            bot.state_file = Path(tmpdir) / "state.json"
            bot.trade_file = trade_path
            asyncio.run(bot._open_position("0xToken", lifecycle, 0.99, pred_return=13.0, signal_price=1.0))
            trade_rows = [json.loads(line) for line in trade_path.read_text(encoding="utf-8").splitlines()]
            audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        executor.check_token_status.assert_not_awaited()
        executor.check_token_quote_supported.assert_awaited_once_with("0xToken")
        executor.buy_token.assert_awaited_once()
        self.assertEqual(trade_rows[-1]["entry_price"], 0.1)
        self.assertTrue(trade_rows[-1]["buy_fast_status_used"])
        self.assertGreaterEqual(trade_rows[-1]["token_status_check_seconds"], 0.0)
        self.assertEqual(audit_rows[-1]["buy_fast_status_used"], True)
        self.assertEqual(audit_rows[-1]["token_status_source"], "lifecycle")
        for row in (trade_rows[-1], audit_rows[-1]):
            self.assertAlmostEqual(row["lifecycle_price_current"], 1.01)
            self.assertAlmostEqual(row["lifecycle_price_first"], 0.90)
            self.assertAlmostEqual(row["lifecycle_price_peak"], 1.20)
            self.assertAlmostEqual(row["lifecycle_price_from_first_pct"], (1.01 / 0.90) - 1.0)
            self.assertAlmostEqual(row["lifecycle_price_from_peak_pct"], (1.01 / 1.20) - 1.0)

    def test_real_open_position_rejects_fast_lifecycle_non_native_quote_before_buy(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.01,
            "price_first": 0.90,
            "price_max": 1.20,
            "last_update": datetime.now().timestamp(),
            "create_timestamp": 0,
            "graduated": False,
        }
        executor = MagicMock()
        executor.wallet_address = "0xWallet"
        executor.check_token_status = AsyncMock(
            return_value={
                "ready": True,
                "price": 1.0,
                "reason": "OK",
            }
        )
        executor.check_token_quote_supported = AsyncMock(
            return_value={
                "ready": False,
                "quote": "0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d",
                "reason": "Unsupported quote asset: 0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d",
            }
        )
        executor.buy_token = AsyncMock(return_value="0xbuy")
        token_contract = MagicMock()
        token_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=10**18)
        executor.w3.eth.contract.return_value = token_contract
        executor.w3.eth.get_balance = AsyncMock(return_value=400000000000000000)
        executor.w3.from_wei.return_value = 0.4

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, patch.multiple(
            "src.trader.bot",
            TradeExecutor=MagicMock(return_value=executor),
            DataCollector=MagicMock(return_value=MagicMock()),
            FourMemeListener=MagicMock(return_value=MagicMock()),
            WSConnectionManager=MagicMock(return_value=MagicMock()),
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            audit_path = Path(tmpdir) / "signals.jsonl"
            trade_path = Path(tmpdir) / "trades.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    entry_price_protection_pct=0.25,
                    buy_confirm_poll_interval_seconds=0.25,
                    buy_confirm_timeout_seconds=10,
                    signal_audit_file=str(audit_path),
                )
            )
            bot.state_file = Path(tmpdir) / "state.json"
            bot.trade_file = trade_path
            asyncio.run(bot._open_position("0xToken", lifecycle, 0.99, pred_return=13.0, signal_price=1.0))
            audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        executor.check_token_status.assert_not_awaited()
        executor.check_token_quote_supported.assert_awaited_once_with("0xToken")
        executor.buy_token.assert_not_awaited()
        self.assertFalse(trade_path.exists())
        self.assertEqual(audit_rows[-1]["action"], "BUY_NOT_READY")
        self.assertEqual(audit_rows[-1]["reason"], "Unsupported quote asset: 0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d")
        self.assertEqual(audit_rows[-1]["token_quote"], "0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d")
        self.assertAlmostEqual(audit_rows[-1]["lifecycle_price_current"], 1.01)
        self.assertAlmostEqual(audit_rows[-1]["lifecycle_price_first"], 0.90)
        self.assertAlmostEqual(audit_rows[-1]["lifecycle_price_peak"], 1.20)
        self.assertAlmostEqual(audit_rows[-1]["lifecycle_price_from_first_pct"], (1.01 / 0.90) - 1.0)
        self.assertAlmostEqual(audit_rows[-1]["lifecycle_price_from_peak_pct"], (1.01 / 1.20) - 1.0)

    def test_lifecycle_price_attribution_helpers_handle_missing_or_invalid_prices(self):
        from src.trader.bot import MemeBot

        self.assertIsNone(MemeBot._positive_lifecycle_price(None))
        self.assertIsNone(MemeBot._positive_lifecycle_price(0))
        self.assertIsNone(MemeBot._positive_lifecycle_price(-1))
        self.assertIsNone(MemeBot._positive_lifecycle_price("not-a-price"))
        self.assertEqual(MemeBot._positive_lifecycle_price("1.2"), 1.2)

        self.assertIsNone(MemeBot._price_change_pct(current=1.0, baseline=None))
        self.assertIsNone(MemeBot._price_change_pct(current=0.0, baseline=1.0))
        self.assertIsNone(MemeBot._price_change_pct(current=1.0, baseline=0.0))
        self.assertAlmostEqual(MemeBot._price_change_pct(current=0.8, baseline=1.0), -0.2)

        lifecycle_status = {
            "source": "lifecycle",
            "lifecycle_price_current": 1.0,
            "lifecycle_price_first": None,
            "lifecycle_price_peak": None,
            "lifecycle_price_from_first_pct": None,
            "lifecycle_price_from_peak_pct": None,
        }

        self.assertEqual(
            MemeBot._lifecycle_price_attribution_fields(lifecycle_status),
            {
                "lifecycle_price_current": 1.0,
                "lifecycle_price_first": None,
                "lifecycle_price_peak": None,
                "lifecycle_price_from_first_pct": None,
                "lifecycle_price_from_peak_pct": None,
            },
        )
        self.assertEqual(
            MemeBot._lifecycle_price_attribution_fields({"source": "helper", "lifecycle_price_current": 1.0}),
            {},
        )

    def test_real_open_position_uses_fresh_local_lifecycle_update_before_helper(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        now_ts = datetime.now().timestamp()
        lifecycle = {
            "symbol": "TK",
            "price_current": 1.01,
            "last_update": now_ts - 1,
            "last_update_local": now_ts,
            "create_timestamp": 0,
            "graduated": False,
        }
        executor = MagicMock()
        executor.wallet_address = "0xWallet"
        executor.check_token_status = AsyncMock(
            return_value={
                "ready": True,
                "price": 1.0,
                "reason": "OK",
            }
        )
        executor.buy_token = AsyncMock(return_value="0xbuy")
        token_contract = MagicMock()
        token_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=10**18)
        executor.w3.eth.contract.return_value = token_contract
        executor.w3.eth.get_balance = AsyncMock(return_value=400000000000000000)
        executor.w3.from_wei.return_value = 0.4

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, patch.multiple(
            "src.trader.bot",
            TradeExecutor=MagicMock(return_value=executor),
            DataCollector=MagicMock(return_value=MagicMock()),
            FourMemeListener=MagicMock(return_value=MagicMock()),
            WSConnectionManager=MagicMock(return_value=MagicMock()),
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            audit_path = Path(tmpdir) / "signals.jsonl"
            trade_path = Path(tmpdir) / "trades.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    entry_price_protection_pct=0.25,
                    buy_confirm_poll_interval_seconds=0.25,
                    buy_confirm_timeout_seconds=10,
                    signal_audit_file=str(audit_path),
                )
            )
            bot.state_file = Path(tmpdir) / "state.json"
            bot.trade_file = trade_path
            asyncio.run(bot._open_position("0xToken", lifecycle, 0.99, pred_return=13.0, signal_price=1.0))
            trade_rows = [json.loads(line) for line in trade_path.read_text(encoding="utf-8").splitlines()]
            audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        executor.check_token_status.assert_not_awaited()
        executor.buy_token.assert_awaited_once()
        self.assertTrue(trade_rows[-1]["buy_fast_status_used"])
        self.assertEqual(trade_rows[-1]["token_status_check_seconds"], 0.0)
        self.assertEqual(audit_rows[-1]["token_status_source"], "lifecycle")

    def test_real_open_position_falls_back_to_helper_when_chain_event_lag_is_stale(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.01,
            "last_update": datetime.now().timestamp() - 30,
            "last_update_local": datetime.now().timestamp(),
            "create_timestamp": 0,
            "graduated": False,
        }
        executor = MagicMock()
        executor.wallet_address = "0xWallet"
        executor.check_token_status = AsyncMock(
            return_value={
                "ready": True,
                "price": 1.0,
                "reason": "OK",
            }
        )
        executor.buy_token = AsyncMock(return_value="0xbuy")
        token_contract = MagicMock()
        token_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=10**18)
        executor.w3.eth.contract.return_value = token_contract
        executor.w3.eth.get_balance = AsyncMock(return_value=400000000000000000)
        executor.w3.from_wei.return_value = 0.4

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, patch.multiple(
            "src.trader.bot",
            TradeExecutor=MagicMock(return_value=executor),
            DataCollector=MagicMock(return_value=MagicMock()),
            FourMemeListener=MagicMock(return_value=MagicMock()),
            WSConnectionManager=MagicMock(return_value=MagicMock()),
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            trade_path = Path(tmpdir) / "trades.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    buy_fast_status_max_chain_lag_seconds=8,
                    buy_confirm_poll_interval_seconds=0.25,
                    buy_confirm_timeout_seconds=10,
                    signal_audit_file=str(Path(tmpdir) / "signals.jsonl"),
                )
            )
            bot.state_file = Path(tmpdir) / "state.json"
            bot.trade_file = trade_path
            asyncio.run(bot._open_position("0xToken", lifecycle, 0.99, pred_return=13.0, signal_price=1.0))
            trade_rows = [json.loads(line) for line in trade_path.read_text(encoding="utf-8").splitlines()]

        executor.check_token_status.assert_awaited_once_with("0xToken")
        self.assertFalse(trade_rows[-1]["buy_fast_status_used"])
        self.assertEqual(trade_rows[-1]["token_status_source"], "helper")
        self.assertGreater(trade_rows[-1]["lifecycle_status_chain_lag_seconds"], 8)

    def test_real_open_position_falls_back_to_helper_for_stale_lifecycle_fast_status(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.01,
            "last_update": 1,
            "create_timestamp": 0,
            "graduated": False,
        }
        executor = MagicMock()
        executor.wallet_address = "0xWallet"
        executor.check_token_status = AsyncMock(
            return_value={
                "ready": True,
                "price": 1.0,
                "reason": "OK",
            }
        )
        executor.buy_token = AsyncMock(return_value="0xbuy")
        token_contract = MagicMock()
        token_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=10**18)
        executor.w3.eth.contract.return_value = token_contract
        executor.w3.eth.get_balance = AsyncMock(return_value=400000000000000000)
        executor.w3.from_wei.return_value = 0.4

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, patch.multiple(
            "src.trader.bot",
            TradeExecutor=MagicMock(return_value=executor),
            DataCollector=MagicMock(return_value=MagicMock()),
            FourMemeListener=MagicMock(return_value=MagicMock()),
            WSConnectionManager=MagicMock(return_value=MagicMock()),
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            trade_path = Path(tmpdir) / "trades.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    buy_confirm_poll_interval_seconds=0.25,
                    buy_confirm_timeout_seconds=10,
                    signal_audit_file=str(Path(tmpdir) / "signals.jsonl"),
                )
            )
            bot.state_file = Path(tmpdir) / "state.json"
            bot.trade_file = trade_path
            asyncio.run(bot._open_position("0xToken", lifecycle, 0.99, pred_return=13.0, signal_price=1.0))
            trade_rows = [json.loads(line) for line in trade_path.read_text(encoding="utf-8").splitlines()]

        executor.check_token_status.assert_awaited_once_with("0xToken")
        self.assertFalse(trade_rows[-1]["buy_fast_status_used"])
        self.assertEqual(trade_rows[-1]["token_status_source"], "helper")

    def test_real_open_position_prefetches_nonce_while_checking_token_status(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 120,
            "create_timestamp": 0,
        }
        events = []
        allow_prefetch_finish = asyncio.Event()
        executor = MagicMock()
        executor.wallet_address = "0xWallet"

        async def prefetch_next_nonce():
            events.append("prefetch_start")
            await allow_prefetch_finish.wait()
            events.append("prefetch_done")

        async def check_token_status(_token):
            events.append("status_start")
            allow_prefetch_finish.set()
            return {
                "ready": True,
                "price": 1.0,
                "reason": "OK",
            }

        executor.prefetch_next_nonce = prefetch_next_nonce
        executor.check_token_status = AsyncMock(side_effect=check_token_status)
        executor.buy_token = AsyncMock(return_value="0xbuy")
        token_contract = MagicMock()
        token_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=10**18)
        executor.w3.eth.contract.return_value = token_contract
        executor.w3.eth.get_balance = AsyncMock(return_value=400000000000000000)
        executor.w3.from_wei.return_value = 0.4

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, patch.multiple(
            "src.trader.bot",
            TradeExecutor=MagicMock(return_value=executor),
            DataCollector=MagicMock(return_value=MagicMock()),
            FourMemeListener=MagicMock(return_value=MagicMock()),
            WSConnectionManager=MagicMock(return_value=MagicMock()),
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    buy_confirm_poll_interval_seconds=0.25,
                    buy_confirm_timeout_seconds=10,
                    signal_audit_file=str(Path(tmpdir) / "signals.jsonl"),
                )
            )
            bot.state_file = Path(tmpdir) / "state.json"
            bot.trade_file = Path(tmpdir) / "trades.jsonl"
            asyncio.run(
                asyncio.wait_for(
                    bot._open_position("0xToken", lifecycle, 0.99, pred_return=13.0, signal_price=1.0),
                    timeout=1.0,
                )
            )

        self.assertEqual(events[:3], ["prefetch_start", "status_start", "prefetch_done"])

    def test_real_open_position_schedules_sell_preapproval_after_tokens_received(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 120,
            "create_timestamp": 0,
        }
        executor = MagicMock()
        executor.wallet_address = "0xWallet"
        executor.check_token_status = AsyncMock(
            return_value={
                "ready": True,
                "price": 1.0,
                "reason": "OK",
            }
        )
        executor.buy_token = AsyncMock(return_value="0xbuy")
        executor.schedule_sell_approval = MagicMock()
        token_contract = MagicMock()
        token_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=10**18)
        executor.w3.eth.contract.return_value = token_contract
        executor.w3.eth.get_balance = AsyncMock(return_value=400000000000000000)
        executor.w3.from_wei.return_value = 0.4

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, patch.multiple(
            "src.trader.bot",
            TradeExecutor=MagicMock(return_value=executor),
            DataCollector=MagicMock(return_value=MagicMock()),
            FourMemeListener=MagicMock(return_value=MagicMock()),
            WSConnectionManager=MagicMock(return_value=MagicMock()),
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid), patch("src.trader.bot.asyncio.sleep", new_callable=AsyncMock):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    buy_confirm_poll_interval_seconds=0.25,
                    buy_confirm_timeout_seconds=10,
                    signal_audit_file=str(Path(tmpdir) / "signals.jsonl"),
                )
            )
            bot.state_file = Path(tmpdir) / "state.json"
            bot.trade_file = Path(tmpdir) / "trades.jsonl"
            asyncio.run(bot._open_position("0xToken", lifecycle, 0.99, pred_return=13.0, signal_price=1.0))

        executor.schedule_sell_approval.assert_called_once_with("0xToken", 10**18)

    def test_real_open_position_records_position_before_post_buy_balance_sync(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 120,
            "create_timestamp": 0,
        }
        executor = MagicMock()
        executor.wallet_address = "0xWallet"
        executor.check_token_status = AsyncMock(
            return_value={
                "ready": True,
                "price": 1.0,
                "reason": "OK",
            }
        )
        executor.buy_token = AsyncMock(return_value="0xbuy")
        token_contract = MagicMock()
        token_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=10**18)
        executor.w3.eth.contract.return_value = token_contract

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, patch.multiple(
            "src.trader.bot",
            TradeExecutor=MagicMock(return_value=executor),
            DataCollector=MagicMock(return_value=MagicMock()),
            FourMemeListener=MagicMock(return_value=MagicMock()),
            WSConnectionManager=MagicMock(return_value=MagicMock()),
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    buy_confirm_poll_interval_seconds=0.25,
                    buy_confirm_timeout_seconds=10,
                    signal_audit_file=str(Path(tmpdir) / "signals.jsonl"),
                )
            )
            bot.state_file = Path(tmpdir) / "state.json"
            bot.trade_file = Path(tmpdir) / "trades.jsonl"
            sync_seen_position = []

            async def sync_balance(force=False):
                sync_seen_position.append("0xToken" in bot.positions)

            bot._sync_balance = AsyncMock(side_effect=sync_balance)
            asyncio.run(bot._open_position("0xToken", lifecycle, 0.99, pred_return=13.0, signal_price=1.0))

        self.assertEqual(sync_seen_position, [True])

    def test_real_open_position_immediately_exits_post_fill_slippage_breach(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 120,
            "create_timestamp": 0,
        }
        executor = MagicMock()
        executor.wallet_address = "0xWallet"
        executor.check_token_status = AsyncMock(
            return_value={
                "ready": True,
                "price": 1.0,
                "reason": "OK",
            }
        )
        executor.buy_token = AsyncMock(return_value="0xbuy")
        token_contract = MagicMock()
        token_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=int(0.05 * 10**18))
        executor.w3.eth.contract.return_value = token_contract
        executor.w3.eth.get_balance = AsyncMock(return_value=400000000000000000)
        executor.w3.from_wei.return_value = 0.4

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, patch.multiple(
            "src.trader.bot",
            TradeExecutor=MagicMock(return_value=executor),
            DataCollector=MagicMock(return_value=MagicMock()),
            FourMemeListener=MagicMock(return_value=MagicMock()),
            WSConnectionManager=MagicMock(return_value=MagicMock()),
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            audit_path = Path(tmpdir) / "signals.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    entry_price_protection_pct=0.25,
                    buy_confirm_poll_interval_seconds=0.25,
                    buy_confirm_timeout_seconds=10,
                    signal_audit_file=str(audit_path),
                )
            )
            bot.state_file = Path(tmpdir) / "state.json"
            bot.trade_file = Path(tmpdir) / "trades.jsonl"
            bot._close_position = AsyncMock()
            asyncio.run(bot._open_position("0xToken", lifecycle, 0.99, pred_return=13.0, signal_price=1.0))
            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        bot._close_position.assert_awaited_once_with("0xToken", reason="ENTRY_SLIPPAGE_PROTECTION")
        self.assertEqual(rows[-1]["action"], "ENTRY_PRICE_PROTECTION_POST_FILL_EXIT")
        self.assertGreater(rows[-1]["entry_slippage_pct"], 0.25)

    def test_real_entry_price_protection_normalizes_helper_price(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 7.719648494861625e-09,
            "last_update": 120,
            "create_timestamp": 0,
        }
        executor = MagicMock()
        executor.check_token_status = AsyncMock(
            return_value={
                "ready": True,
                "price": 8055655943,
                "reason": "OK",
            }
        )
        executor.buy_token = AsyncMock(return_value=None)

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, patch.multiple(
            "src.trader.bot",
            TradeExecutor=MagicMock(return_value=executor),
            DataCollector=MagicMock(return_value=MagicMock()),
            FourMemeListener=MagicMock(return_value=MagicMock()),
            WSConnectionManager=MagicMock(return_value=MagicMock()),
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            audit_path = Path(tmpdir) / "signals.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    entry_price_protection_pct=0.25,
                    signal_audit_file=str(audit_path),
                )
            )
            bot.trade_file = Path(tmpdir) / "trades.jsonl"
            asyncio.run(
                bot._open_position(
                    "0xToken",
                    lifecycle,
                    0.99,
                    pred_return=13.0,
                    signal_price=7.719648494861625e-09,
                )
            )
            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        executor.buy_token.assert_awaited_once()
        self.assertEqual(rows[-1]["action"], "BUY_EXECUTION_FAILED")
        self.assertFalse(any(row["action"] == "ENTRY_PRICE_PROTECTION_SKIP" for row in rows))

    def test_open_position_stores_continue_hold_route_metadata(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 120,
            "create_timestamp": 0,
        }

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", False), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    signal_audit_file=str(Path(tmpdir) / "signals.jsonl"),
                    trade_file=str(Path(tmpdir) / "trades.jsonl"),
                    state_file=str(Path(tmpdir) / "state.json"),
                )
            )
            asyncio.run(
                bot._open_position(
                    "0xToken",
                    lifecycle,
                    0.99,
                    pred_return=55.0,
                    signal_price=1.0,
                    action_policy_route={
                        "used": True,
                        "route": "continue_hold",
                        "confidence": 0.91,
                        "reason": "continue_hold",
                        "live_feature_count": 4,
                    },
                )
            )

        pos = bot.positions["0xToken"]
        self.assertTrue(pos["action_policy_router_used"])
        self.assertEqual(pos["action_policy_router_route"], "continue_hold")
        self.assertEqual(pos["action_policy_router_confidence"], 0.91)
        # The activation/release percentages come from TradingConfig, which a local .env may
        # override. Assert that the position stores the configured value; the shipped defaults
        # are asserted against .env.example elsewhere.
        from config.trading_config import TradingConfig

        self.assertEqual(
            pos["action_policy_continue_hold_activation_pct"],
            TradingConfig.BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT,
        )
        self.assertEqual(
            pos["action_policy_continue_hold_release_pct"],
            TradingConfig.BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT,
        )

    def test_action_policy_shadow_audit_load_failure_is_nonfatal_and_does_not_enable_flow_features(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.feature_names = ["current_price", "volume_30s"]

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(
            MemeBot,
            "_load_state",
            return_value=None,
        ), patch.object(MemeBot, "_register_handlers", return_value=None), patch(
            "src.model.hybrid_inference.HybridModel.load",
            return_value=supported_hybrid,
        ), patch(
            "src.trader.bot.ActionPolicyRouterRuntime.from_report_paths",
            side_effect=FileNotFoundError("missing router report"),
        ):
            with self.assertLogs("MemeBot", level="WARNING") as logs:
                bot = MemeBot(
                    self._base_config(
                        model_dir,
                        buy_action_policy_router_shadow_audit_enabled=True,
                        buy_action_policy_router_train_rejected_reports=["missing-rejected.json"],
                        buy_action_policy_router_train_accepted_reports=["missing-accepted.json"],
                    )
                )

        self.assertTrue(bot.buy_action_policy_router_shadow_audit_enabled)
        self.assertIsNone(bot.action_policy_shadow_runtime)
        self.assertIsNone(bot.action_policy_router_runtime)
        self.assertFalse(bot.include_flow_features)
        self.assertIn("Action policy router shadow audit disabled", "\n".join(logs.output))

    def test_action_policy_shadow_audit_success_load_keeps_live_router_disabled(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.feature_names = ["current_price", "volume_30s"]

        shadow_runtime = MagicMock()
        shadow_runtime.enabled = True
        shadow_runtime.route_names = ["skip", "continue_hold"]
        shadow_runtime.feature_names = ["flow_signed_imbalance_30s"]

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(
            MemeBot,
            "_load_state",
            return_value=None,
        ), patch.object(MemeBot, "_register_handlers", return_value=None), patch(
            "src.model.hybrid_inference.HybridModel.load",
            return_value=supported_hybrid,
        ), patch(
            "src.trader.bot.ActionPolicyRouterRuntime.from_report_paths",
            return_value=shadow_runtime,
        ):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    buy_action_policy_router_shadow_audit_enabled=True,
                    buy_action_policy_router_train_rejected_reports=["rejected.json"],
                    buy_action_policy_router_train_accepted_reports=["accepted.json"],
                )
            )

        self.assertIs(bot.action_policy_shadow_runtime, shadow_runtime)
        self.assertIsNone(bot.action_policy_router_runtime)
        self.assertFalse(bot.include_flow_features)

    def test_action_policy_shadow_flow_features_use_separate_extraction(self):
        from src.trader.bot import MemeBot

        bot = object.__new__(MemeBot)
        bot.include_flow_features = False
        bot.inference_future_window_seconds = 300
        bot.action_policy_shadow_runtime = MagicMock()
        bot.action_policy_shadow_runtime.feature_names = ["flow_signed_imbalance_30s"]
        bot.collector = MagicMock()
        bot.collector._extract_features.return_value = {
            "current_price": 1.0,
            "flow_signed_imbalance_30s": 0.7,
        }
        lifecycle = {
            "last_update": 120,
            "buys": [{"timestamp": 120, "bnb_amount": 1.0}],
            "sells": [],
        }
        live_features = {"current_price": 1.0}

        shadow_features = bot._action_policy_shadow_features(lifecycle, live_features)

        self.assertIsNot(shadow_features, live_features)
        self.assertEqual(shadow_features["flow_signed_imbalance_30s"], 0.7)
        self.assertNotIn("flow_signed_imbalance_30s", live_features)
        self.assertFalse(bot.include_flow_features)
        bot.collector._extract_features.assert_called_once()
        self.assertTrue(bot.collector._extract_features.call_args.kwargs["include_flow_features"])

    def test_signal_audit_logs_shadow_route_without_enqueuing_live_router_metadata(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.9, True)
        supported_hybrid.predict_return.return_value = 42.0

        collector = MagicMock()
        collector._extract_features.return_value = {
            "current_price": 1.0,
            "volume_30s": 2.0,
            "price_volatility": 0.2,
        }
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            }
        }

        shadow_runtime = MagicMock()
        shadow_runtime.feature_names = ["current_price"]
        shadow_runtime.runtime_params = {"buy_threshold": 0.5}
        shadow_runtime.metadata = {
            "source_groups": {
                "train_rejected": ["rejected.json"],
                "train_accepted": ["accepted.json"],
            }
        }
        shadow_runtime.predict.return_value = {
            "used": True,
            "route": "continue_hold",
            "confidence": 0.91,
            "reason": "continue_hold",
            "live_feature_count": 3,
            "route_probabilities": {"continue_hold": 0.91, "skip": 0.09},
        }

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(
            collector=collector,
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(
            MemeBot,
            "_register_handlers",
            return_value=None,
        ), patch(
            "src.model.hybrid_inference.HybridModel.load",
            return_value=supported_hybrid,
        ):
            audit_path = Path(tmpdir) / "signals.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    signal_audit_file=str(audit_path),
                    buy_action_policy_router_shadow_audit_enabled=True,
                )
            )
            bot.action_policy_shadow_runtime = shadow_runtime
            bot._enqueue_buy_signal = AsyncMock(return_value="queued")
            asyncio.run(bot._process_token_logic("0xToken"))
            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        bot._enqueue_buy_signal.assert_awaited_once()
        self.assertIsNone(bot._enqueue_buy_signal.await_args.kwargs["action_policy_route"])
        self.assertEqual(rows[-1]["action"], "SIGNAL_DECISION")
        self.assertEqual(rows[-1]["decision"], "queued")
        self.assertTrue(rows[-1]["action_policy_shadow_used"])
        self.assertEqual(rows[-1]["action_policy_shadow_route"], "continue_hold")
        self.assertEqual(rows[-1]["action_policy_shadow_confidence"], 0.91)
        self.assertEqual(rows[-1]["action_policy_shadow_reason"], "continue_hold")
        self.assertEqual(rows[-1]["action_policy_shadow_live_feature_count"], 3)
        self.assertEqual(rows[-1]["action_policy_shadow_route_probabilities"]["continue_hold"], 0.91)
        self.assertEqual(rows[-1]["action_policy_shadow_train_rejected_reports"], ["rejected.json"])
        self.assertEqual(rows[-1]["action_policy_shadow_train_accepted_reports"], ["accepted.json"])
        self.assertNotIn("action_policy_router_used", rows[-1])
        self.assertFalse(collector._extract_features.call_args.kwargs["include_flow_features"])

    def test_rejected_signal_audit_logs_shadow_route(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.2, False)
        supported_hybrid.predict_return.return_value = 8.0

        collector = MagicMock()
        collector._extract_features.return_value = {
            "current_price": 1.0,
            "volume_30s": 2.0,
            "price_volatility": 0.2,
        }
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            }
        }

        shadow_runtime = MagicMock()
        shadow_runtime.feature_names = ["current_price"]
        shadow_runtime.runtime_params = {}
        shadow_runtime.metadata = {"source_groups": {}}
        shadow_runtime.predict.return_value = {
            "used": False,
            "route": "skip",
            "confidence": 0.77,
            "reason": "non_continue_hold_route",
            "live_feature_count": 3,
            "route_probabilities": {"continue_hold": 0.23, "skip": 0.77},
        }

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(
            collector=collector,
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(
            MemeBot,
            "_register_handlers",
            return_value=None,
        ), patch(
            "src.model.hybrid_inference.HybridModel.load",
            return_value=supported_hybrid,
        ):
            audit_path = Path(tmpdir) / "signals.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    signal_audit_file=str(audit_path),
                    buy_action_policy_router_shadow_audit_enabled=True,
                )
            )
            bot.action_policy_shadow_runtime = shadow_runtime
            asyncio.run(bot._process_token_logic("0xToken"))
            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(rows[-1]["action"], "SIGNAL_DECISION")
        self.assertEqual(rows[-1]["decision"], "rejected")
        self.assertFalse(rows[-1]["action_policy_shadow_used"])
        self.assertEqual(rows[-1]["action_policy_shadow_route"], "skip")
        self.assertEqual(rows[-1]["action_policy_shadow_reason"], "non_continue_hold_route")
        self.assertNotIn("action_policy_router_used", rows[-1])

    def test_negative_entry_price_protection_is_clamped_to_zero_like_replay(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        lifecycle = {
            "symbol": "TK",
            "price_current": 1.0,
            "last_update": 120,
            "create_timestamp": 0,
        }

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", False), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.5,
                    fixed_stake_bnb=0.1,
                    max_concurrent_positions=10,
                    entry_price_protection_pct=-0.10,
                    signal_audit_file=str(Path(tmpdir) / "signals.jsonl"),
                )
            )
            bot.state_file = Path(tmpdir) / "state.json"
            bot.trade_file = Path(tmpdir) / "trades.jsonl"
            asyncio.run(bot._open_position("0xToken", lifecycle, 0.9, signal_price=1.0))

        self.assertEqual(bot.entry_price_protection_pct, 0.0)
        self.assertIn("0xToken", bot.positions)

    def test_paper_full_close_uses_entry_price_not_signal_price_for_pnl(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        collector = MagicMock()
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.2,
                "last_update": 120,
                "create_timestamp": 0,
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", False), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, initial_balance=0.4))
            bot.trade_file = Path(tmpdir) / "trades.jsonl"
            bot.signal_audit_file = Path(tmpdir) / "signals.jsonl"
            bot.state_file = Path(tmpdir) / "state.json"
            bot.positions = {
                "0xToken": {
                    "symbol": "TK",
                    "entry_price": 1.2,
                    "signal_price": 1.0,
                    "tp_base_price": 1.2,
                    "peak_price": 1.2,
                    "entry_time": datetime.now() - timedelta(seconds=10),
                    "size_bnb": 0.1,
                    "initial_size_bnb": 0.1,
                }
            }
            asyncio.run(bot._close_position_inner("0xToken", "TEST_EXIT"))
            rows = [json.loads(line) for line in bot.signal_audit_file.read_text(encoding="utf-8").splitlines()]

        self.assertNotIn("0xToken", bot.positions)
        self.assertAlmostEqual(bot.balance, 0.5)
        self.assertAlmostEqual(rows[-1]["net_profit"], 0.0)
        self.assertEqual(rows[-1]["entry_price"], 1.2)
        self.assertEqual(rows[-1]["signal_price"], 1.0)

    def test_real_full_close_logs_receipt_sell_price_when_lifecycle_price_is_stale(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        token = "0x0000000000000000000000000000000000000abc"
        account = "0x0000000000000000000000000000000000000def"
        amount = 100 * 10**18
        cost = 150 * 10**18
        sale_topic = "0x0a5575b3648bae2210cee56bf33254cc1ddfbc7bf637c0af2ac18b14fb1bae19"
        data = (
            int(token, 16).to_bytes(32, "big")
            + int(account, 16).to_bytes(32, "big")
            + (0).to_bytes(32, "big")
            + amount.to_bytes(32, "big")
            + cost.to_bytes(32, "big")
            + (0).to_bytes(96, "big")
        )

        collector = MagicMock()
        collector.token_lifecycle = {
            token: {
                "symbol": "TK",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
            }
        }

        executor = MagicMock()
        executor.w3.eth.get_transaction_receipt = AsyncMock(
            return_value={"logs": [{"topics": [sale_topic], "data": data}]}
        )

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, patch.multiple(
            "src.trader.bot",
            TradeExecutor=MagicMock(return_value=executor),
            DataCollector=MagicMock(return_value=collector),
            FourMemeListener=MagicMock(return_value=MagicMock()),
            WSConnectionManager=MagicMock(return_value=MagicMock()),
        ), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot, "_do_sell", AsyncMock(return_value="0xsell")), patch.object(MemeBot, "_sync_balance", AsyncMock()), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, initial_balance=0.4))
            bot.trade_file = Path(tmpdir) / "trades.jsonl"
            bot.signal_audit_file = Path(tmpdir) / "signals.jsonl"
            bot.state_file = Path(tmpdir) / "state.json"
            bot.balance = 0.65
            bot.positions = {
                token: {
                    "symbol": "TK",
                    "entry_price": 1.0,
                    "signal_price": 1.0,
                    "tp_base_price": 1.0,
                    "peak_price": 1.0,
                    "entry_time": datetime.now() - timedelta(seconds=10),
                    "size_bnb": 0.1,
                    "initial_size_bnb": 0.1,
                }
            }
            asyncio.run(bot._close_position_inner(token, "TEST_EXIT"))
            rows = [json.loads(line) for line in bot.signal_audit_file.read_text(encoding="utf-8").splitlines()]

        self.assertAlmostEqual(rows[-1]["sell_trigger_price"], 1.0)
        self.assertAlmostEqual(rows[-1]["exit_price"], 1.5)
        self.assertEqual(rows[-1]["exit_price_source"], "receipt")

    def test_paper_partial_sell_uses_entry_price_not_signal_price_for_pnl(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        collector = MagicMock()
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.2,
                "last_update": 120,
                "create_timestamp": 0,
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", False), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, initial_balance=0.4))
            bot.trade_file = Path(tmpdir) / "trades.jsonl"
            bot.signal_audit_file = Path(tmpdir) / "signals.jsonl"
            bot.state_file = Path(tmpdir) / "state.json"
            bot.positions = {
                "0xToken": {
                    "symbol": "TK",
                    "entry_price": 1.2,
                    "signal_price": 1.0,
                    "tp_base_price": 1.2,
                    "peak_price": 1.2,
                    "entry_time": datetime.now() - timedelta(seconds=10),
                    "size_bnb": 0.1,
                    "initial_size_bnb": 0.1,
                }
            }
            asyncio.run(bot._partial_sell("0xToken", sell_ratio=0.5, reason="TEST_PARTIAL"))
            rows = [json.loads(line) for line in bot.trade_file.read_text(encoding="utf-8").splitlines()]
            audit_rows = [json.loads(line) for line in bot.signal_audit_file.read_text(encoding="utf-8").splitlines()]

        self.assertAlmostEqual(bot.positions["0xToken"]["size_bnb"], 0.05)
        self.assertAlmostEqual(bot.balance, 0.45)
        self.assertAlmostEqual(rows[-1]["net_profit"], 0.0)
        self.assertEqual(rows[-1]["entry_price"], 1.2)
        self.assertEqual(rows[-1]["exit_price"], 1.2)
        self.assertEqual(audit_rows[-1]["action"], "POSITION_PARTIAL_CLOSED")
        self.assertEqual(audit_rows[-1]["sell_ratio"], 0.5)
        self.assertAlmostEqual(audit_rows[-1]["net_profit"], 0.0)

    def test_emergency_liquidation_summary_uses_entry_price_not_signal_price(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        collector = MagicMock()
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.2,
                "last_update": 120,
                "create_timestamp": 0,
            }
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", False), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, initial_balance=0.4))
            bot.positions = {
                "0xToken": {
                    "symbol": "TK",
                    "entry_price": 1.2,
                    "signal_price": 1.0,
                    "tp_base_price": 1.2,
                    "peak_price": 1.2,
                    "entry_time": datetime.now() - timedelta(seconds=10),
                    "size_bnb": 0.1,
                    "initial_size_bnb": 0.1,
                }
            }
            bot._close_position = AsyncMock()
            with self.assertLogs("MemeBot", level="WARNING") as logs:
                asyncio.run(bot.sell_all_positions(timeout=12))

        position_logs = [line for line in logs.output if "0xToken" in line and "PnL:" in line]
        self.assertTrue(position_logs)
        self.assertIn("PnL: +0.0%", position_logs[-1])
        self.assertNotIn("PnL: +20.0%", position_logs[-1])

    def test_runtime_risk_params_load_from_model_manifest_when_not_manual(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.77
        supported_hybrid.sell_policy = None

        manifest = {
            "evaluation": {
                "stop_loss": -0.35,
                "max_hold_seconds": 300,
                "min_policy_hold_seconds": 5,
                "max_entry_age_seconds": 300,
                "position_fraction": 0.05,
                "trailing_start_pct": 0.25,
                "trailing_stop_pct": 0.20,
                "rug_sell_pressure": 0.92,
                "allow_partial_exits": False,
                "max_open_positions": 8,
            }
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bot = MemeBot(self._base_config(model_dir))

        self.assertEqual(bot.stop_loss, -0.35)
        self.assertEqual(bot.hold_time_seconds, 300)
        self.assertEqual(bot.min_policy_hold_seconds, 5)
        self.assertEqual(bot.max_age_seconds, 300)
        self.assertEqual(bot.position_size, 0.05)
        self.assertEqual(bot.trailing_start_pct, 0.25)
        self.assertEqual(bot.trailing_stop_pct, 0.20)
        self.assertEqual(bot.rug_sell_pressure, 0.92)
        self.assertEqual(bot.max_concurrent_positions, 8)

    def test_runtime_risk_params_prefer_selected_runtime_params_over_stale_evaluation(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.77
        supported_hybrid.sell_policy = None

        manifest = {
            "evaluation": {
                "stop_loss": -0.18,
                "max_hold_seconds": 420,
                "min_policy_hold_seconds": 45,
                "position_fraction": 0.25,
                "fixed_stake_bnb": 0.1,
                "buy_threshold": 0.91,
                "min_entry_score": 35.0,
                "entry_ranking_mode": "chronological",
                "buy_near_threshold_min_prob": 0.95,
                "buy_near_min_pred_return": 40.0,
            },
            "selected_runtime_params": {
                "stop_loss": -0.22,
                "max_hold_seconds": 360,
                "min_policy_hold_seconds": 60,
                "position_fraction": 0.1,
                "fixed_stake_bnb": None,
                "buy_threshold": 0.94,
                "min_entry_score": 65.0,
                "entry_ranking_mode": "entry_value",
                "buy_near_threshold_min_prob": 0.94,
                "buy_near_min_pred_return": 32.0,
                "buy_near_min_entry_volume_30s": 1.25,
                "buy_near_min_entry_price_volatility": 0.08,
                "buy_near_min_age_seconds": 0.0,
                "buy_primary_score_rescue_min_prob": 0.985,
                "buy_primary_score_rescue_min_pred_return": 25.0,
                "buy_primary_score_rescue_min_entry_volume_30s": 3.0,
                "buy_primary_score_rescue_min_entry_price_volatility": 0.30,
                "buy_primary_score_rescue_min_age_seconds": 0.0,
            },
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bot = MemeBot(self._base_config(model_dir))

        self.assertEqual(bot.stop_loss, -0.22)
        self.assertEqual(bot.hold_time_seconds, 360)
        self.assertEqual(bot.min_policy_hold_seconds, 60)
        self.assertEqual(bot.position_size, 0.1)
        self.assertIsNone(bot.fixed_stake_bnb)
        self.assertEqual(bot.prob_threshold, 0.94)
        self.assertEqual(supported_hybrid.buy_threshold, 0.94)
        self.assertEqual(bot.min_pred_return, 65.0)
        self.assertEqual(bot.entry_ranking_mode, "entry_value")
        self.assertEqual(bot.buy_near_threshold_min_prob, 0.94)
        self.assertEqual(bot.buy_near_min_pred_return, 32.0)
        self.assertEqual(bot.buy_near_min_entry_volume_30s, 1.25)
        self.assertEqual(bot.buy_near_min_entry_price_volatility, 0.08)
        self.assertEqual(bot.buy_near_min_age_seconds, 0.0)
        self.assertEqual(bot.buy_primary_score_rescue_min_prob, 0.985)
        self.assertEqual(bot.buy_primary_score_rescue_min_pred_return, 25.0)
        self.assertEqual(bot.buy_primary_score_rescue_min_entry_volume_30s, 3.0)
        self.assertEqual(bot.buy_primary_score_rescue_min_entry_price_volatility, 0.30)
        self.assertEqual(bot.buy_primary_score_rescue_min_age_seconds, 0.0)

    def test_model_manifest_authoritative_runtime_overrides_stale_local_defaults(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.77
        supported_hybrid.sell_policy = None

        manifest = {
            "evaluation": {
                "position_fraction": 0.10,
                "max_open_positions": 8,
                "min_entry_unique_buyers": 3,
                "min_entry_buy_count": 5,
                "min_entry_volume_30s": 1.5,
                "min_entry_price_volatility": 0.0,
            }
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    model_manifest_authoritative=True,
                    position_size=0.25,
                    max_concurrent_positions=0,
                    min_entry_unique_buyers=1,
                    min_entry_buy_count=1,
                    min_entry_volume_30s=0.0,
                    min_entry_price_volatility=0.0,
                )
            )

        self.assertEqual(bot.position_size, 0.10)
        self.assertEqual(bot.max_concurrent_positions, 8)
        self.assertEqual(bot.min_entry_unique_buyers, 3)
        self.assertEqual(bot.min_entry_buy_count, 5)
        self.assertEqual(bot.min_entry_volume_30s, 1.5)
        self.assertEqual(bot.min_entry_price_volatility, 0.0)

    def test_manual_zero_max_open_positions_overrides_manifest_limit(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.77
        supported_hybrid.sell_policy = None

        manifest = {"evaluation": {"max_open_positions": 1000}}

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bot = MemeBot(self._base_config(model_dir, max_concurrent_positions=0))

        self.assertEqual(bot.max_concurrent_positions, 0)
        self.assertEqual(bot.exit_param_sources["max_concurrent_positions"], "manual")

    def test_zero_max_concurrent_positions_means_unlimited_capacity(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.9, True)

        collector = MagicMock()
        collector._extract_features.return_value = {"current_price": 1.0}
        collector.token_lifecycle = {
            "0xNew": {
                "symbol": "NEW",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            }
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, initial_balance=1.0, max_concurrent_positions=0))
            bot.positions = {
                "0xHeld": {
                    "symbol": "HELD",
                    "entry_price": 1.0,
                    "entry_time": datetime.now(),
                    "size_bnb": 0.1,
                }
            }
            asyncio.run(bot._process_token_logic("0xNew"))

        self.assertEqual(bot._buy_signal_queue.qsize(), 1)

    def test_fixed_stake_is_capped_by_max_entry_size(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.15,
                    fixed_stake_bnb=0.2,
                    max_entry_size_bnb=0.1,
                )
            )

        self.assertAlmostEqual(bot._entry_size_bnb(), 0.1)

    def test_runtime_fraction_sizing_can_disable_manifest_fixed_stake(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        manifest = {"evaluation": {"fixed_stake_bnb": 0.1, "position_fraction": 0.5}}

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=2.0,
                    position_size=0.10,
                    fixed_stake_bnb=None,
                    max_entry_size_bnb=0.1,
                )
            )

        self.assertIsNone(bot.fixed_stake_bnb)
        self.assertEqual(bot.exit_param_sources["fixed_stake_bnb"], "manual")
        self.assertAlmostEqual(bot.position_size, 0.10)
        self.assertAlmostEqual(bot._entry_size_bnb(), 0.1)

    def test_manifest_max_position_fraction_caps_fractional_entry_size_when_not_manual(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        manifest = {
            "evaluation": {
                "initial_equity_bnb": 0.007055981941653848,
                "position_fraction": 0.15,
                "max_position_fraction": 0.15,
            }
        }
        expected_cap = 0.007055981941653848 * 0.15

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.10,
                    position_size=0.15,
                    fixed_stake_bnb=None,
                    max_entry_size_bnb=None,
                )
            )

        self.assertAlmostEqual(bot.max_entry_size_bnb, expected_cap)
        self.assertEqual(bot.exit_param_sources["max_entry_size_bnb"], "model_manifest")
        self.assertAlmostEqual(bot._entry_size_bnb(), expected_cap)

    def test_manual_max_entry_size_overrides_manifest_cap(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        manifest = {
            "evaluation": {
                "initial_equity_bnb": 0.007055981941653848,
                "position_fraction": 0.15,
                "max_position_fraction": 0.15,
            }
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    initial_balance=0.10,
                    position_size=0.15,
                    fixed_stake_bnb=None,
                    max_entry_size_bnb=0.004,
                )
            )

        self.assertAlmostEqual(bot.max_entry_size_bnb, 0.004)
        self.assertEqual(bot.exit_param_sources["max_entry_size_bnb"], "manual")
        self.assertAlmostEqual(bot._entry_size_bnb(), 0.004)

    def test_pred_return_filter_runtime_params_load_from_model_manifest_when_not_manual(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.77
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_return = MagicMock(return_value=12.0)

        manifest = {
            "evaluation": {
                "min_entry_score": 10.0,
                "use_pred_return_filter": True,
            }
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bot = MemeBot(self._base_config(model_dir))

        self.assertTrue(bot.use_pred_return_filter)
        self.assertEqual(bot.min_pred_return, 10.0)
        self.assertEqual(bot.strategy_param_sources["min_pred_return"], "model_manifest")

    def test_entry_value_ranking_requires_pred_return_support(self):
        from src.trader.bot import MemeBot

        class _UnsupportedHybrid:
            buy_threshold = 0.5
            sell_policy = None

        manifest = {"evaluation": {"entry_ranking_mode": "entry_value"}}

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=_UnsupportedHybrid()):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"entry_ranking_mode=entry_value.*predicted-return support"):
                MemeBot(self._base_config(model_dir))

    def test_live_buy_queue_prioritizes_entry_value_manifest_ranking(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.side_effect = [(0.95, True), (0.90, True)]
        supported_hybrid.predict_return.side_effect = [11.0, 50.0]

        collector = MagicMock()
        collector._extract_features.return_value = {"current_price": 1.0}
        collector.token_lifecycle = {
            "0xLow": {
                "symbol": "LOW",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            },
            "0xHigh": {
                "symbol": "HIGH",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"d", "e", "f"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            },
        }
        manifest = {"evaluation": {"entry_ranking_mode": "entry_value"}}

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bot = MemeBot(self._base_config(model_dir, max_concurrent_positions=10))

            opened = []

            async def record_open(token_address, lifecycle, prob, pred_return=None, signal_price=None, **kwargs):
                opened.append((token_address, pred_return))
                if len(opened) >= 2:
                    bot.active = False

            bot._open_position = record_open

            async def run_flow():
                await bot._process_token_logic("0xLow")
                await bot._process_token_logic("0xHigh")
                await bot._buy_worker_loop()

            asyncio.run(run_flow())

        self.assertEqual(bot.entry_ranking_mode, "entry_value")
        self.assertEqual(opened, [("0xHigh", 50.0), ("0xLow", 11.0)])

    def test_live_buy_queue_replaces_lower_entry_value_signal_when_capacity_reserved(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.side_effect = [(0.95, True), (0.90, True)]
        supported_hybrid.predict_return.side_effect = [11.0, 50.0]

        collector = MagicMock()
        collector._extract_features.return_value = {"current_price": 1.0}
        collector.token_lifecycle = {
            "0xLow": {
                "symbol": "LOW",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            },
            "0xHigh": {
                "symbol": "HIGH",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"d", "e", "f"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            },
        }
        manifest = {"evaluation": {"entry_ranking_mode": "entry_value"}}

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bot = MemeBot(self._base_config(model_dir, max_concurrent_positions=1))

            opened = []

            async def record_open(token_address, lifecycle, prob, pred_return=None, signal_price=None, **kwargs):
                opened.append((token_address, pred_return))
                bot.active = False

            bot._open_position = record_open

            async def run_flow():
                await bot._process_token_logic("0xLow")
                await bot._process_token_logic("0xHigh")
                await bot._buy_worker_loop()

            asyncio.run(run_flow())

        self.assertEqual(opened, [("0xHigh", 50.0)])
        self.assertNotIn("0xLow", bot._pending_buy_signals)

    def test_process_token_logic_blocks_buy_when_max_concurrent_positions_is_full(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None
        supported_hybrid.predict_buy.return_value = (0.9, True)

        collector = MagicMock()
        collector._extract_features.return_value = {"current_price": 1.0}
        collector.token_lifecycle = {
            "0xNew": {
                "symbol": "NEW",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [1, 2, 3, 4, 5],
                "sells": [],
            }
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, max_concurrent_positions=1))
            bot.positions = {
                "0xHeld": {
                    "symbol": "HELD",
                    "entry_price": 1.0,
                    "entry_time": datetime.now(),
                    "size_bnb": 0.1,
                }
            }
            asyncio.run(bot._process_token_logic("0xNew"))

        self.assertEqual(bot._buy_signal_queue.qsize(), 0)

    def test_manual_runtime_config_overrides_model_manifest_and_threshold(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.77
        supported_hybrid.sell_policy = None

        manifest = {
            "evaluation": {
                "stop_loss": -0.35,
                "max_hold_seconds": 300,
                "min_policy_hold_seconds": 5,
                "max_entry_age_seconds": 300,
                "position_fraction": 0.05,
                "trailing_start_pct": 0.25,
                "trailing_stop_pct": 0.20,
                "rug_sell_pressure": 0.92,
                "max_open_positions": 99,
            }
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            Path(model_dir, "hybrid_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    prob_threshold=0.91,
                    stop_loss=-0.50,
                    hold_time_seconds=120,
                    min_policy_hold_seconds=1,
                    max_age_seconds=150,
                    position_size=0.20,
                    trailing_start_pct=0.40,
                    trailing_stop_pct=0.30,
                    rug_sell_pressure=0.95,
                    max_concurrent_positions=7,
                )
            )

        self.assertEqual(bot.hybrid.buy_threshold, 0.91)
        self.assertEqual(bot.stop_loss, -0.50)
        self.assertEqual(bot.hold_time_seconds, 120)
        self.assertEqual(bot.min_policy_hold_seconds, 1)
        self.assertEqual(bot.max_age_seconds, 150)
        self.assertEqual(bot.position_size, 0.20)
        self.assertEqual(bot.trailing_start_pct, 0.40)
        self.assertEqual(bot.trailing_stop_pct, 0.30)
        self.assertEqual(bot.rug_sell_pressure, 0.95)
        self.assertEqual(bot.max_concurrent_positions, 7)

    def test_position_logic_enforces_manifest_rug_exit_before_policy(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = object()

        collector = MagicMock()
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.0,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [],
                "sells": [],
            }
        }
        collector._extract_features.return_value = {
            "total_buy_volume": 1.0,
            "total_sell_volume": 9.0,
            "launch_fee": 0.5,
            "holder_count": 5,
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, rug_sell_pressure=0.80, hold_time_seconds=300))
            bot.positions = {
                "0xToken": {
                    "symbol": "TK",
                    "entry_price": 1.0,
                    "signal_price": 1.0,
                    "tp_base_price": 1.0,
                    "peak_price": 1.0,
                    "entry_time": datetime.now() - timedelta(seconds=10),
                    "size_bnb": 0.1,
                    "initial_size_bnb": 0.1,
                }
            }
            bot._close_position = AsyncMock()
            asyncio.run(bot._process_token_logic("0xToken"))

        bot._close_position.assert_awaited_once_with("0xToken", reason="RUG_EXIT")

    def test_position_logic_enforces_trailing_stop_before_policy(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = object()

        collector = MagicMock()
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.5,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [],
                "sells": [],
            }
        }
        collector._extract_features.return_value = {
            "total_buy_volume": 10.0,
            "total_sell_volume": 1.0,
            "launch_fee": 0.5,
            "holder_count": 5,
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    trailing_start_pct=0.25,
                    trailing_stop_pct=0.20,
                    hold_time_seconds=300,
                )
            )
            bot.positions = {
                "0xToken": {
                    "symbol": "TK",
                    "entry_price": 1.0,
                    "signal_price": 1.0,
                    "tp_base_price": 1.0,
                    "peak_price": 2.0,
                    "entry_time": datetime.now() - timedelta(seconds=10),
                    "size_bnb": 0.1,
                    "initial_size_bnb": 0.1,
                }
            }
            bot._close_position = AsyncMock()
            asyncio.run(bot._process_token_logic("0xToken"))

        bot._close_position.assert_awaited_once_with("0xToken", reason="TRAILING_STOP")

    def test_position_logic_does_not_trailing_stop_on_helper_only_drawdown(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        collector = MagicMock()
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.4,
                "price_current_source": "helper",
                "last_update": datetime.now().timestamp(),
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [],
                "sells": [],
            }
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    trailing_start_pct=0.25,
                    trailing_stop_pct=0.20,
                    hold_time_seconds=300,
                )
            )
            bot.positions = {
                "0xToken": {
                    "symbol": "TK",
                    "entry_price": 1.0,
                    "signal_price": 1.0,
                    "tp_base_price": 1.0,
                    "peak_price": 2.0,
                    "entry_time": datetime.now() - timedelta(seconds=10),
                    "size_bnb": 0.1,
                    "initial_size_bnb": 0.1,
                }
            }
            bot._close_position = AsyncMock()
            asyncio.run(bot._process_token_logic("0xToken"))

        bot._close_position.assert_not_awaited()

    def test_position_logic_continue_hold_suppresses_ppo_sell_after_activation_before_release(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = object()
        supported_hybrid.predict_sell.return_value = 3

        collector = MagicMock()
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.30,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [],
                "sells": [],
            }
        }
        collector._extract_features.return_value = {
            "total_buy_volume": 10.0,
            "total_sell_volume": 1.0,
            "launch_fee": 0.5,
            "holder_count": 5,
        }

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            audit_path = Path(tmpdir) / "signals.jsonl"
            bot = MemeBot(
                self._base_config(
                    model_dir,
                    hold_time_seconds=300,
                    signal_audit_file=str(audit_path),
                )
            )
            bot.positions = {
                "0xToken": {
                    "symbol": "TK",
                    "entry_price": 1.0,
                    "signal_price": 1.0,
                    "tp_base_price": 1.0,
                    "peak_price": 1.40,
                    "entry_time": datetime.now() - timedelta(seconds=10),
                    "size_bnb": 0.1,
                    "initial_size_bnb": 0.1,
                    "action_policy_router_used": True,
                    "action_policy_router_route": "continue_hold",
                    "action_policy_router_confidence": 0.91,
                    "action_policy_continue_hold_activation_pct": 0.35,
                    "action_policy_continue_hold_release_pct": 0.75,
                }
            }
            bot._close_position = AsyncMock()
            asyncio.run(bot._process_token_logic("0xToken"))
            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

        supported_hybrid.predict_sell.assert_called_once()
        bot._close_position.assert_not_awaited()
        self.assertEqual(rows[-1]["action"], "ACTION_POLICY_CONTINUE_HOLD_PPO_SUPPRESSED")
        self.assertEqual(rows[-1]["ppo_action"], 3)

    def test_position_logic_continue_hold_release_returns_control_to_ppo_sell(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = object()
        supported_hybrid.predict_sell.return_value = 3

        collector = MagicMock()
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 1.80,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [],
                "sells": [],
            }
        }
        collector._extract_features.return_value = {
            "total_buy_volume": 10.0,
            "total_sell_volume": 1.0,
            "launch_fee": 0.5,
            "holder_count": 5,
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, hold_time_seconds=300))
            bot.positions = {
                "0xToken": {
                    "symbol": "TK",
                    "entry_price": 1.0,
                    "signal_price": 1.0,
                    "tp_base_price": 1.0,
                    "peak_price": 1.80,
                    "entry_time": datetime.now() - timedelta(seconds=10),
                    "size_bnb": 0.1,
                    "initial_size_bnb": 0.1,
                    "action_policy_router_used": True,
                    "action_policy_router_route": "continue_hold",
                    "action_policy_router_confidence": 0.91,
                    "action_policy_continue_hold_activation_pct": 0.35,
                    "action_policy_continue_hold_release_pct": 0.75,
                }
            }
            bot._close_position = AsyncMock()
            asyncio.run(bot._process_token_logic("0xToken"))

        bot._close_position.assert_awaited_once_with("0xToken", reason="PPO_SELL100")

    def test_position_logic_continue_hold_never_suppresses_stop_loss(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = object()

        collector = MagicMock()
        collector.token_lifecycle = {
            "0xToken": {
                "symbol": "TK",
                "price_current": 0.70,
                "last_update": 120,
                "create_timestamp": 0,
                "unique_buyers": {"a", "b", "c"},
                "buys": [],
                "sells": [],
            }
        }

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir, stop_loss=-0.20, hold_time_seconds=300))
            bot.positions = {
                "0xToken": {
                    "symbol": "TK",
                    "entry_price": 1.0,
                    "signal_price": 1.0,
                    "tp_base_price": 1.0,
                    "peak_price": 1.40,
                    "entry_time": datetime.now() - timedelta(seconds=10),
                    "size_bnb": 0.1,
                    "initial_size_bnb": 0.1,
                    "action_policy_router_used": True,
                    "action_policy_router_route": "continue_hold",
                    "action_policy_router_confidence": 0.91,
                    "action_policy_continue_hold_activation_pct": 0.35,
                    "action_policy_continue_hold_release_pct": 0.75,
                }
            }
            bot._close_position = AsyncMock()
            asyncio.run(bot._process_token_logic("0xToken"))

        bot._close_position.assert_awaited_once_with("0xToken", reason="STOP_LOSS")
        supported_hybrid.predict_sell.assert_not_called()

    def test_restored_lifecycle_stub_contains_feature_required_fields(self):
        from src.trader.bot import MemeBot

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        collector = MagicMock()
        collector.token_lifecycle = {}

        with self._create_model_dir() as model_dir, self._patch_bot_deps(collector=collector), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir))
            bot.positions = {
                "0xToken": {
                    "symbol": "TK",
                    "entry_price": 1.0,
                    "entry_time": datetime.now(),
                    "size_bnb": 0.1,
                }
            }
            bot._ensure_lifecycle("0xToken")

        lifecycle = collector.token_lifecycle["0xToken"]
        for required_key in ("creator", "name", "total_supply", "launch_fee"):
            self.assertIn(required_key, lifecycle)

    def test_sync_positions_removes_invalid_restored_token_address(self):
        from src.trader.bot import MemeBot
        import asyncio

        supported_hybrid = MagicMock()
        supported_hybrid.buy_threshold = 0.5
        supported_hybrid.sell_policy = None

        with tempfile.TemporaryDirectory() as tmpdir, self._create_model_dir() as model_dir, self._patch_bot_deps(), patch.object(MemeBot, "_load_state", return_value=None), patch.object(MemeBot, "_register_handlers", return_value=None), patch.object(MemeBot.__init__.__globals__["TradingConfig"], "ENABLE_TRADING", True), patch("src.model.hybrid_inference.HybridModel.load", return_value=supported_hybrid):
            bot = MemeBot(self._base_config(model_dir))
            bot.state_file = Path(tmpdir) / "state.json"
            bot.executor.wallet_address = "0x867883B3e77E4E12f2baB796F220f56586b38703"
            bot.executor.w3.is_address.return_value = False
            bot.positions = {
                "0xToken": {
                    "symbol": "TK",
                    "entry_price": 1.0,
                    "entry_time": datetime.now(),
                    "size_bnb": 0.1,
                }
            }
            asyncio.run(bot._sync_positions_with_chain())

        self.assertEqual(bot.positions, {})
        self.assertIn("0xToken", bot.closed_tokens)


if __name__ == "__main__":
    unittest.main()

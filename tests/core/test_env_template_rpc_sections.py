import unittest
import json
from pathlib import Path
from unittest.mock import patch

from config.trading_config import TradingConfig


class TestEnvTemplateRpcSections(unittest.TestCase):
    def _env_value(self, content: str, key: str) -> str:
        for raw_line in content.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith('#') or '=' not in stripped:
                continue
            found_key, raw_value = stripped.split('=', 1)
            if found_key.strip() == key:
                return raw_value.split('#', 1)[0].strip()
        self.fail(f'Missing env key: {key}')

    def test_env_example_contains_required_rpc_role_keys(self):
        env_example_path = Path(__file__).resolve().parents[2] / '.env.example'
        content = env_example_path.read_text(encoding='utf-8')

        required_entries = [
            'LISTENER_MODE=hybrid',
            'BSC_WSS_URL=',
            'BSC_LOG_HTTP_ENDPOINTS=',
            'BSC_TRADE_HTTP_RPC=',
            'BSC_HTTP_RPC=',
            'SCAN_HISTORICAL=false',
            'LISTENER_POLL_INTERVAL_SECONDS=0.25',
            'MAX_CONCURRENT_POSITIONS=8',
            'POSITION_SIZE=0.10',
            'FIXED_STAKE_BNB=',
            'MAX_ENTRY_SIZE_BNB=',
            'BUY_CONFIRM_POLL_INTERVAL_SECONDS=0.25',
            'BUY_CONFIRM_TIMEOUT_SECONDS=120',
            'BUY_USE_LIFECYCLE_FAST_STATUS=true',
            'BUY_FAST_STATUS_MAX_STALENESS_SECONDS=3',
            'BUY_FAST_STATUS_MAX_CHAIN_LAG_SECONDS=8',
            'TX_RECEIPT_POLL_LATENCY_SECONDS=0.25',
            'MODEL_DIR=data/models/20260519_v95_v84_selective_nearmiss_gate',
            'DATASET_LABEL_ENTRY_FIXED_COST_BNB=0',
            'DATASET_LABEL_EXIT_FIXED_COST_BNB=0',
            'DATASET_LABEL_FIXED_STAKE_BNB=',
            'DATASET_LABEL_ENTRY_PRICE_PROTECTION_PCT=',
            'MIN_ENTRY_VOLUME_30S=1.5',
            'MIN_ENTRY_PRICE_VOLATILITY=0.1',
            'BUY_NEAR_THRESHOLD_MIN_PROB=',
            'BUY_NEAR_MIN_PRED_RETURN=',
            'BUY_NEAR_MIN_ENTRY_VOLUME_30S=',
            'BUY_NEAR_MIN_ENTRY_PRICE_VOLATILITY=',
            'BUY_NEAR_MIN_AGE_SECONDS=',
            'BUY_PRIMARY_SCORE_RESCUE_MIN_PROB=',
            'BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN=',
            'BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S=',
            'BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY=',
            'BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS=',
            'BUY_ACTION_POLICY_ROUTER_ENABLED=false',
            'BUY_ACTION_POLICY_ROUTER_SHADOW_AUDIT_ENABLED=false',
            'BUY_ACTION_POLICY_ROUTER_TRAIN_REJECTED_REPORTS=',
            'BUY_ACTION_POLICY_ROUTER_TRAIN_ACCEPTED_REPORTS=',
            'BUY_ACTION_POLICY_ROUTER_MIN_CONFIDENCE=0.40',
            'BUY_ACTION_POLICY_ROUTER_MAX_DEPTH=3',
            'BUY_ACTION_POLICY_ROUTER_MIN_SAMPLES_LEAF=10',
            'BUY_ACTION_POLICY_ROUTER_MIN_COMMON_FEATURES=2',
            'BUY_ACTION_POLICY_ROUTER_MIN_LIVE_FEATURES=2',
            'BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT=0.35',
            'BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT=0.75',
            'POST_GRADUATION_RETENTION_ENABLED=false',
            'POST_GRADUATION_RETENTION_SHADOW_ENABLED=false',
            'POST_GRADUATION_RETENTION_MAX_HOLD_SECONDS=345600',
            'POST_GRADUATION_RETENTION_MAX_DATA_AGE_SECONDS=120',
            'POST_GRADUATION_RETENTION_MIN_LIQUIDITY_USD=10000',
            'POST_GRADUATION_RETENTION_MIN_VOLUME_LIQUIDITY_RATIO_5M=0.20',
            'POST_GRADUATION_RETENTION_MIN_NEW_TRADERS_1H=2',
            'POST_GRADUATION_RETENTION_MAX_SELL_PRESSURE_5M=0.60',
            'POST_GRADUATION_RETENTION_MAX_DRAWDOWN=0.35',
        ]

        for entry in required_entries:
            self.assertIn(entry, content)

        self.assertNotIn('BSC_LOG_HTTP_WEIGHTS=', content)
        self.assertIn('Deprecated legacy combined HTTP RPC', content)
        self.assertIn('may add per-token latency', content)

    def test_env_example_selected_model_entry_guards_match_manifest(self):
        root = Path(__file__).resolve().parents[2]
        env_example_path = root / '.env.example'
        content = env_example_path.read_text(encoding='utf-8')

        model_dir = root / self._env_value(content, 'MODEL_DIR')
        manifest = json.loads((model_dir / 'hybrid_manifest.json').read_text(encoding='utf-8'))
        evaluation = manifest['evaluation']

        self.assertEqual(float(self._env_value(content, 'POSITION_SIZE')), evaluation['position_fraction'])
        self.assertEqual(int(self._env_value(content, 'MAX_CONCURRENT_POSITIONS')), evaluation['max_open_positions'])
        self.assertEqual(float(self._env_value(content, 'MIN_ENTRY_VOLUME_30S')), evaluation['min_entry_volume_30s'])
        self.assertEqual(
            float(self._env_value(content, 'MIN_ENTRY_PRICE_VOLATILITY')),
            evaluation['min_entry_price_volatility'],
        )

    def test_selected_model_manifest_runtime_replay_matches_selected_evaluation(self):
        root = Path(__file__).resolve().parents[2]
        env_example_path = root / '.env.example'
        content = env_example_path.read_text(encoding='utf-8')

        model_dir = root / self._env_value(content, 'MODEL_DIR')
        manifest = json.loads((model_dir / 'hybrid_manifest.json').read_text(encoding='utf-8'))
        evaluation = manifest['evaluation']
        runtime_replay = evaluation['runtime_replay']

        self.assertEqual(runtime_replay['total_trades'], evaluation['total_trades'])
        self.assertEqual(runtime_replay['net_profit_bnb'], evaluation['net_profit_bnb'])
        self.assertEqual(runtime_replay['net_return_pct'], evaluation['net_return_pct'])
        self.assertEqual(runtime_replay['buy_near_threshold_min_prob'], evaluation['buy_near_threshold_min_prob'])
        self.assertEqual(runtime_replay['buy_near_min_pred_return'], evaluation['buy_near_min_pred_return'])
        self.assertEqual(runtime_replay['buy_near_min_entry_volume_30s'], evaluation['buy_near_min_entry_volume_30s'])
        self.assertEqual(
            runtime_replay['buy_near_min_entry_price_volatility'],
            evaluation['buy_near_min_entry_price_volatility'],
        )

    def test_selected_model_manifest_near_gate_matches_validation_rule(self):
        root = Path(__file__).resolve().parents[2]
        env_example_path = root / '.env.example'
        content = env_example_path.read_text(encoding='utf-8')

        model_dir = root / self._env_value(content, 'MODEL_DIR')
        manifest = json.loads((model_dir / 'hybrid_manifest.json').read_text(encoding='utf-8'))
        selected = manifest['selected_runtime_params']
        rule = manifest['validation_evaluation']['top_candidate']['rule']

        self.assertEqual(rule['near_prob_min'], selected['buy_near_threshold_min_prob'])
        self.assertEqual(rule['near_score_min'], selected['buy_near_min_pred_return'])
        self.assertEqual(rule['near_volume_min'], selected['buy_near_min_entry_volume_30s'])
        self.assertEqual(rule['near_price_vol_min'], selected['buy_near_min_entry_price_volatility'])
        self.assertEqual(rule['age_min'], selected['buy_near_min_age_seconds'])
        self.assertEqual(rule['age_max'], selected['max_entry_age_seconds'])

    def test_selected_model_primary_score_rescue_contract_is_explicit(self):
        root = Path(__file__).resolve().parents[2]
        env_example_path = root / '.env.example'
        content = env_example_path.read_text(encoding='utf-8')

        model_dir = root / self._env_value(content, 'MODEL_DIR')
        manifest = json.loads((model_dir / 'hybrid_manifest.json').read_text(encoding='utf-8'))
        selected = manifest.get('selected_runtime_params', {})
        evaluation = manifest.get('evaluation', {})
        runtime_replay = evaluation.get('runtime_replay', {})
        key_pairs = [
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_PROB', 'buy_primary_score_rescue_min_prob'),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN', 'buy_primary_score_rescue_min_pred_return'),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S', 'buy_primary_score_rescue_min_entry_volume_30s'),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY', 'buy_primary_score_rescue_min_entry_price_volatility'),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS', 'buy_primary_score_rescue_min_age_seconds'),
        ]

        for env_key, _ in key_pairs:
            self.assertEqual(self._env_value(content, env_key), '')
        self.assertIn('若当前模型未带该参数则关闭主阈值救援门', content)

        present = [runtime_key for _, runtime_key in key_pairs if runtime_key in selected]
        self.assertIn(len(present), (0, len(key_pairs)))
        for _, runtime_key in key_pairs:
            if runtime_key not in selected:
                continue
            self.assertEqual(runtime_replay.get(runtime_key), selected[runtime_key])
            self.assertEqual(evaluation.get(runtime_key), selected[runtime_key])

    def test_trading_config_exposes_near_threshold_gate_defaults(self):
        self.assertIsNone(TradingConfig.BUY_NEAR_THRESHOLD_MIN_PROB)
        self.assertIsNone(TradingConfig.BUY_NEAR_MIN_PRED_RETURN)
        self.assertIsNone(TradingConfig.BUY_NEAR_MIN_ENTRY_VOLUME_30S)
        self.assertIsNone(TradingConfig.BUY_NEAR_MIN_ENTRY_PRICE_VOLATILITY)
        self.assertIsNone(TradingConfig.BUY_NEAR_MIN_AGE_SECONDS)

    def test_trading_config_exposes_primary_score_rescue_defaults(self):
        self.assertIsNone(TradingConfig.BUY_PRIMARY_SCORE_RESCUE_MIN_PROB)
        self.assertIsNone(TradingConfig.BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN)
        self.assertIsNone(TradingConfig.BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S)
        self.assertIsNone(TradingConfig.BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY)
        self.assertIsNone(TradingConfig.BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS)

    def test_trading_config_exposes_action_policy_router_defaults(self):
        self.assertFalse(TradingConfig.BUY_ACTION_POLICY_ROUTER_ENABLED)
        self.assertFalse(TradingConfig.BUY_ACTION_POLICY_ROUTER_SHADOW_AUDIT_ENABLED)
        self.assertEqual(TradingConfig.BUY_ACTION_POLICY_ROUTER_TRAIN_REJECTED_REPORTS, [])
        self.assertEqual(TradingConfig.BUY_ACTION_POLICY_ROUTER_TRAIN_ACCEPTED_REPORTS, [])
        self.assertEqual(TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_CONFIDENCE, 0.40)
        self.assertEqual(TradingConfig.BUY_ACTION_POLICY_ROUTER_MAX_DEPTH, 3)
        self.assertEqual(TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_SAMPLES_LEAF, 10)
        self.assertEqual(TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_COMMON_FEATURES, 2)
        self.assertEqual(TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_LIVE_FEATURES, 2)
        self.assertEqual(TradingConfig.BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT, 0.35)
        self.assertEqual(TradingConfig.BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT, 0.75)

    def test_trading_config_validates_near_threshold_gate_bounds(self):
        invalid_cases = [
            ('BUY_NEAR_THRESHOLD_MIN_PROB', 0.0),
            ('BUY_NEAR_THRESHOLD_MIN_PROB', 1.01),
            ('BUY_NEAR_THRESHOLD_MIN_PROB', float('nan')),
            ('BUY_NEAR_THRESHOLD_MIN_PROB', float('inf')),
            ('BUY_NEAR_MIN_PRED_RETURN', -0.01),
            ('BUY_NEAR_MIN_PRED_RETURN', float('nan')),
            ('BUY_NEAR_MIN_PRED_RETURN', float('inf')),
            ('BUY_NEAR_MIN_ENTRY_VOLUME_30S', -0.01),
            ('BUY_NEAR_MIN_ENTRY_VOLUME_30S', float('nan')),
            ('BUY_NEAR_MIN_ENTRY_VOLUME_30S', float('inf')),
            ('BUY_NEAR_MIN_ENTRY_PRICE_VOLATILITY', -0.01),
            ('BUY_NEAR_MIN_ENTRY_PRICE_VOLATILITY', float('nan')),
            ('BUY_NEAR_MIN_ENTRY_PRICE_VOLATILITY', float('inf')),
            ('BUY_NEAR_MIN_AGE_SECONDS', -0.01),
            ('BUY_NEAR_MIN_AGE_SECONDS', float('nan')),
            ('BUY_NEAR_MIN_AGE_SECONDS', float('inf')),
        ]

        for attr, value in invalid_cases:
            with self.subTest(attr=attr, value=value), patch.object(TradingConfig, attr, value, create=True):
                with self.assertRaisesRegex(ValueError, attr):
                    TradingConfig.validate()

    def test_trading_config_validates_primary_score_rescue_gate_bounds(self):
        invalid_cases = [
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_PROB', 0.0),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_PROB', 1.01),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_PROB', float('nan')),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_PROB', float('inf')),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN', -0.01),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN', float('nan')),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN', float('inf')),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S', -0.01),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S', float('nan')),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S', float('inf')),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY', -0.01),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY', float('nan')),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY', float('inf')),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS', -0.01),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS', float('nan')),
            ('BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS', float('inf')),
        ]

        for attr, value in invalid_cases:
            with self.subTest(attr=attr, value=value), patch.object(TradingConfig, attr, value, create=True):
                with self.assertRaisesRegex(ValueError, attr):
                    TradingConfig.validate()

    def test_trading_config_validates_action_policy_router_bounds(self):
        invalid_cases = [
            ('BUY_ACTION_POLICY_ROUTER_MIN_CONFIDENCE', 0.0),
            ('BUY_ACTION_POLICY_ROUTER_MIN_CONFIDENCE', 1.01),
            ('BUY_ACTION_POLICY_ROUTER_MIN_CONFIDENCE', float('nan')),
            ('BUY_ACTION_POLICY_ROUTER_MAX_DEPTH', 0),
            ('BUY_ACTION_POLICY_ROUTER_MIN_SAMPLES_LEAF', 0),
            ('BUY_ACTION_POLICY_ROUTER_MIN_COMMON_FEATURES', 0),
            ('BUY_ACTION_POLICY_ROUTER_MIN_LIVE_FEATURES', 0),
            ('BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT', -0.01),
            ('BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT', float('nan')),
            ('BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT', -0.01),
            ('BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT', float('nan')),
        ]

        for attr, value in invalid_cases:
            with self.subTest(attr=attr, value=value), patch.object(TradingConfig, attr, value, create=True):
                with self.assertRaisesRegex(ValueError, attr):
                    TradingConfig.validate()

        with patch.object(TradingConfig, 'BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT', 0.80), patch.object(
            TradingConfig,
            'BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT',
            0.75,
        ):
            with self.assertRaisesRegex(ValueError, 'RELEASE'):
                TradingConfig.validate()

        with patch.object(TradingConfig, 'BUY_ACTION_POLICY_ROUTER_ENABLED', True), patch.object(
            TradingConfig,
            'BUY_ACTION_POLICY_ROUTER_TRAIN_REJECTED_REPORTS',
            [],
        ), patch.object(
            TradingConfig,
            'BUY_ACTION_POLICY_ROUTER_TRAIN_ACCEPTED_REPORTS',
            ['accepted.json'],
        ):
            with self.assertRaisesRegex(ValueError, 'requires rejected and accepted'):
                TradingConfig.validate()

        with patch.object(TradingConfig, 'BUY_ACTION_POLICY_ROUTER_ENABLED', False), patch.object(
            TradingConfig,
            'BUY_ACTION_POLICY_ROUTER_SHADOW_AUDIT_ENABLED',
            True,
            create=True,
        ), patch.object(
            TradingConfig,
            'BUY_ACTION_POLICY_ROUTER_TRAIN_REJECTED_REPORTS',
            [],
        ), patch.object(
            TradingConfig,
            'BUY_ACTION_POLICY_ROUTER_TRAIN_ACCEPTED_REPORTS',
            [],
        ):
            self.assertTrue(TradingConfig.validate())


if __name__ == '__main__':
    unittest.main()

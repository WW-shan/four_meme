"""
Meme Trading Bot (Paper Trading Mode)
Integrates Real-time Listener, Data Collector, and ML Models.
"""

import asyncio
import logging
import json
import hashlib
import inspect
import math
import pandas as pd
import numpy as np
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional, List, Mapping

# Add project root to path (Fix for ModuleNotFoundError)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.core.listener import FourMemeListener
from src.core.ws_manager import WSConnectionManager
from src.core.trader import TradeExecutor
from config.trading_config import TradingConfig
from src.data.fourmeme_log_decoder import TRADE_TOPICS
from src.data.collector import DataCollector
from src.data.feature_extractor import requires_flow_features
from src.model.action_policy_router_runtime import ActionPolicyRouterRuntime
from src.rl.trading_env import build_sell_observation
from src.trader.post_graduation_retention import (
    PostGraduationRetentionPolicy,
    RetentionHealthConfig,
)
from src.trader.runner_reserve import RunnerReserveConfig, RunnerReservePolicy

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("MemeBot")

DEFAULT_LIVE_MODEL_DIR = "data/models/20260519_v95_v84_selective_nearmiss_gate"
MODEL_MANIFEST_RUNTIME_KEYS = frozenset(
    {
        "stop_loss",
        "hold_time_seconds",
        "min_policy_hold_seconds",
        "position_size",
        "fixed_stake_bnb",
        "max_entry_size_bnb",
        "trailing_start_pct",
        "trailing_stop_pct",
        "rug_sell_pressure",
        "allow_partial_exits",
        "max_concurrent_positions",
        "min_pred_return",
        "use_pred_return_filter",
        "entry_ranking_mode",
        "entry_price_protection_pct",
        "max_age_seconds",
        "min_entry_unique_buyers",
        "min_entry_buy_count",
        "min_entry_volume_30s",
        "min_entry_price_volatility",
        "buy_near_threshold_min_prob",
        "buy_near_min_pred_return",
        "buy_near_min_entry_volume_30s",
        "buy_near_min_entry_price_volatility",
        "buy_near_min_age_seconds",
        "buy_primary_score_rescue_min_prob",
        "buy_primary_score_rescue_min_pred_return",
        "buy_primary_score_rescue_min_entry_volume_30s",
        "buy_primary_score_rescue_min_entry_price_volatility",
        "buy_primary_score_rescue_min_age_seconds",
    }
)
# Sale topics are derived from the checked-in Four.meme ABIs. The old
# hand-written set wrongly included the LiquidityAdded topic, which could be
# decoded as a sell price in receipt attribution.
FOURMEME_SALE_TOPICS = frozenset(
    topic for topic, side in TRADE_TOPICS.items() if side == "sale"
)


def _runtime_model_dir() -> str:
    """Return the live model path, allowing ops to pin production away from candidates."""
    return os.getenv("MODEL_DIR", "").strip() or DEFAULT_LIVE_MODEL_DIR


class MemeBot:
    def __init__(self, config: Dict):
        self.config = config
        self.w3 = config['w3']
        self.ws_manager = config.get('ws_manager')

        # Trade Executor (Real Trading)
        self.executor = TradeExecutor(self.w3)
        self.trader_lock = asyncio.Lock()

        # Components
        self.collector = DataCollector(output_dir="data/bot_data") # separate dir for bot data
        self.listener = FourMemeListener(self.w3, config, ws_manager=self.ws_manager)

        # Trading State (Paper Trading)
        self.positions: Dict[str, Dict] = {} # token_address -> position_info
        self.balance = config.get('initial_balance', 10.0) # BNB
        self.active = True
        self.trade_file = Path(config.get('trade_file', "data/paper_trades.jsonl"))
        self.signal_audit_file = Path(config.get('signal_audit_file', "data/signal_audit.jsonl"))
        self.state_file = Path(config.get('state_file', "data/bot_state.json"))

        # --- 运行优化参数 ---
        self.failed_buys: Dict[str, float] = {}  # token_address -> timestamp
        self.pending_buys: set = set()            # tokens currently being bought
        self.last_sync_time: float = 0            # last balance sync timestamp
        self.sync_cooldown: int = 10              # 10s cooldown for balance sync
        self.fail_cooldown: int = 60              # 60s cooldown for real failures
        self.retry_cooldown: float = 0.5           # 0.5s high-frequency retry for NOT_READY
        self._shutting_down: bool = False          # cleanup 模式标记，跳过 trader_lock
        self._background_tasks: List[asyncio.Task] = []  # 后台任务引用，用于显式取消
        self.analysis_event_queue_size = max(1, int(config.get('analysis_event_queue_size', 20000)))
        self._analysis_event_queue: asyncio.Queue = asyncio.Queue(maxsize=self.analysis_event_queue_size)
        self.include_flow_features = False
        self._queued_analysis_tokens: set = set()
        self.collector_event_queue_size = max(1, int(config.get('collector_event_queue_size', 20000)))
        self._collector_event_queue: Optional[asyncio.Queue] = None
        self.collector_batch_size = max(1, int(config.get('collector_batch_size', 200)))
        self.collector_loop_sleep = float(config.get('collector_loop_sleep', 0.05))
        self.collector_flush_interval_seconds = int(config.get('collector_flush_interval_seconds', 30))
        self.collector_flush_min_age_seconds = int(config.get('collector_flush_min_age_seconds', 900))
        self.collector_flush_inactivity_seconds = int(config.get('collector_flush_inactivity_seconds', 300))
        self.min_entry_unique_buyers = max(
            1,
            int(config.get('min_entry_unique_buyers', TradingConfig.MIN_ENTRY_UNIQUE_BUYERS) or 1),
        )
        self.min_entry_buy_count = max(
            1,
            int(config.get('min_entry_buy_count', TradingConfig.MIN_ENTRY_BUY_COUNT) or 1),
        )
        self.min_entry_volume_30s = max(
            0.0,
            float(config.get('min_entry_volume_30s', TradingConfig.MIN_ENTRY_VOLUME_30S) or 0.0),
        )
        self.min_entry_price_volatility = max(
            0.0,
            float(config.get('min_entry_price_volatility', TradingConfig.MIN_ENTRY_PRICE_VOLATILITY) or 0.0),
        )
        self.buy_near_threshold_min_prob = self._optional_probability(
            config.get('buy_near_threshold_min_prob'),
            'buy_near_threshold_min_prob',
        ) if self._config_has_value('buy_near_threshold_min_prob') else None
        self.buy_near_min_pred_return = self._optional_nonnegative_runtime_float(
            config.get('buy_near_min_pred_return'),
            'buy_near_min_pred_return',
        ) if self._config_has_value('buy_near_min_pred_return') else None
        self.buy_near_min_entry_volume_30s = self._optional_nonnegative_runtime_float(
            config.get('buy_near_min_entry_volume_30s'),
            'buy_near_min_entry_volume_30s',
        ) if self._config_has_value('buy_near_min_entry_volume_30s') else None
        self.buy_near_min_entry_price_volatility = self._optional_nonnegative_runtime_float(
            config.get('buy_near_min_entry_price_volatility'),
            'buy_near_min_entry_price_volatility',
        ) if self._config_has_value('buy_near_min_entry_price_volatility') else None
        self.buy_near_min_age_seconds = self._optional_nonnegative_runtime_float(
            config.get('buy_near_min_age_seconds'),
            'buy_near_min_age_seconds',
        ) if self._config_has_value('buy_near_min_age_seconds') else None
        self.near_gate_param_sources = {
            'buy_near_threshold_min_prob': 'manual' if self.buy_near_threshold_min_prob is not None else 'disabled',
            'buy_near_min_pred_return': 'manual' if self.buy_near_min_pred_return is not None else 'disabled',
            'buy_near_min_entry_volume_30s': 'manual' if self.buy_near_min_entry_volume_30s is not None else 'disabled',
            'buy_near_min_entry_price_volatility': 'manual' if self.buy_near_min_entry_price_volatility is not None else 'disabled',
            'buy_near_min_age_seconds': 'manual' if self.buy_near_min_age_seconds is not None else 'disabled',
        }
        self.buy_primary_score_rescue_min_prob = self._optional_probability(
            config.get('buy_primary_score_rescue_min_prob'),
            'buy_primary_score_rescue_min_prob',
        ) if self._config_has_value('buy_primary_score_rescue_min_prob') else None
        self.buy_primary_score_rescue_min_pred_return = self._optional_nonnegative_runtime_float(
            config.get('buy_primary_score_rescue_min_pred_return'),
            'buy_primary_score_rescue_min_pred_return',
        ) if self._config_has_value('buy_primary_score_rescue_min_pred_return') else None
        self.buy_primary_score_rescue_min_entry_volume_30s = self._optional_nonnegative_runtime_float(
            config.get('buy_primary_score_rescue_min_entry_volume_30s'),
            'buy_primary_score_rescue_min_entry_volume_30s',
        ) if self._config_has_value('buy_primary_score_rescue_min_entry_volume_30s') else None
        self.buy_primary_score_rescue_min_entry_price_volatility = self._optional_nonnegative_runtime_float(
            config.get('buy_primary_score_rescue_min_entry_price_volatility'),
            'buy_primary_score_rescue_min_entry_price_volatility',
        ) if self._config_has_value('buy_primary_score_rescue_min_entry_price_volatility') else None
        self.buy_primary_score_rescue_min_age_seconds = self._optional_nonnegative_runtime_float(
            config.get('buy_primary_score_rescue_min_age_seconds'),
            'buy_primary_score_rescue_min_age_seconds',
        ) if self._config_has_value('buy_primary_score_rescue_min_age_seconds') else None
        self.primary_score_rescue_param_sources = {
            'buy_primary_score_rescue_min_prob': 'manual' if self.buy_primary_score_rescue_min_prob is not None else 'disabled',
            'buy_primary_score_rescue_min_pred_return': 'manual' if self.buy_primary_score_rescue_min_pred_return is not None else 'disabled',
            'buy_primary_score_rescue_min_entry_volume_30s': 'manual' if self.buy_primary_score_rescue_min_entry_volume_30s is not None else 'disabled',
            'buy_primary_score_rescue_min_entry_price_volatility': 'manual' if self.buy_primary_score_rescue_min_entry_price_volatility is not None else 'disabled',
            'buy_primary_score_rescue_min_age_seconds': 'manual' if self.buy_primary_score_rescue_min_age_seconds is not None else 'disabled',
        }
        self.buy_signal_queue_size = max(1, int(config.get('buy_signal_queue_size', 20000)))
        self._buy_signal_queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=self.buy_signal_queue_size)
        self._pending_buy_signals: set = set()
        self._buy_signal_sequence = 0
        self.entry_ranking_mode = self._normalize_entry_ranking_mode(config.get('entry_ranking_mode', 'chronological'))
        self.entry_ranking_mode_source = 'manual' if self._config_has_value('entry_ranking_mode') else 'default'
        self.collector_events_enqueued = 0
        self.collector_events_processed = 0
        self._selling_tokens: set = set()          # 正在卖出的token，防止并发卖出
        self.closed_tokens: set = set()            # 已完整交易过的token，默认不重复进场

        # Ensure data directory exists
        self.trade_file.parent.mkdir(parents=True, exist_ok=True)
        self.signal_audit_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        # Load saved state if exists
        self._load_state()

        # Strategy defaults (fallback only)
        self._strategy_defaults = {
            'prob_threshold': 0.85,
            'min_pred_return': 80.0,
            'max_age_seconds': 150,
        }

        self.model_path: Optional[Path] = None
        self.strategy_param_sources = {
            'prob_threshold': 'default',
            'min_pred_return': 'default',
            'max_age_seconds': 'default',
        }
        self.exit_param_sources = {
            'first_take_profit': 'default',
            'first_exit_ratio': 'default',
            'drawdown_stop': 'default',
            'stop_loss': 'default',
            'hold_time_seconds': 'default',
            'min_policy_hold_seconds': 'default',
            'position_size': 'default',
            'fixed_stake_bnb': 'default',
            'max_entry_size_bnb': 'default',
            'trailing_start_pct': 'default',
            'trailing_stop_pct': 'default',
            'rug_sell_pressure': 'default',
            'allow_partial_exits': 'default',
            'max_concurrent_positions': 'default',
        }

        # Strategy Parameters (优化参数 based on backtest)
        self.prob_threshold = self._strategy_defaults['prob_threshold']  # 分类概率阈值
        self.min_pred_return = self._strategy_defaults['min_pred_return']  # 预期最低收益%
        self.max_age_seconds = self._strategy_defaults['max_age_seconds']  # Token最大年龄(秒)
        self._exit_strategy_defaults = {
            'first_take_profit': 2.0,
            'first_exit_ratio': 0.6,
            'drawdown_stop': 0.25,
            'stop_loss': -0.50,
            'hold_time_seconds': 240,
            'min_policy_hold_seconds': 0,
            'position_size': TradingConfig.POSITION_SIZE,
            'fixed_stake_bnb': None,
            'trailing_start_pct': None,
            'trailing_stop_pct': None,
            'rug_sell_pressure': None,
            'allow_partial_exits': False,
        }
        self.use_pred_return_filter = self._resolve_use_pred_return_filter(config)
        self.first_take_profit = self._exit_strategy_defaults['first_take_profit']
        self.first_exit_ratio = self._exit_strategy_defaults['first_exit_ratio']
        self.drawdown_stop = self._exit_strategy_defaults['drawdown_stop']
        self.stop_loss = float(config.get('stop_loss', self._exit_strategy_defaults['stop_loss'])) # -50%
        self.position_size = float(config.get('position_size', self._exit_strategy_defaults['position_size'])) # fraction or BNB
        self.fixed_stake_bnb = self._optional_float(config.get('fixed_stake_bnb', TradingConfig.FIXED_STAKE_BNB))
        self.max_entry_size_bnb = self._optional_nonnegative_float(
            config.get('max_entry_size_bnb', TradingConfig.MAX_ENTRY_SIZE_BNB)
        )
        self.hold_time_seconds = int(config.get('hold_time_seconds', self._exit_strategy_defaults['hold_time_seconds']) or 0)
        self.min_policy_hold_seconds = int(config.get('min_policy_hold_seconds', self._exit_strategy_defaults['min_policy_hold_seconds']) or 0)
        self.trailing_start_pct = self._optional_float(config.get('trailing_start_pct', self._exit_strategy_defaults['trailing_start_pct']))
        self.trailing_stop_pct = self._optional_float(config.get('trailing_stop_pct', self._exit_strategy_defaults['trailing_stop_pct']))
        self.rug_sell_pressure = self._optional_float(config.get('rug_sell_pressure', self._exit_strategy_defaults['rug_sell_pressure']))
        self.allow_partial_exits = bool(config.get('allow_partial_exits', self._exit_strategy_defaults['allow_partial_exits']))
        self.runner_reserve = RunnerReservePolicy(
            RunnerReserveConfig(
                enabled=bool(config.get('runner_reserve_enabled', TradingConfig.RUNNER_RESERVE_ENABLED)),
                activation_multiple=float(
                    config.get(
                        'runner_reserve_activation_multiple',
                        TradingConfig.RUNNER_RESERVE_ACTIVATION_MULTIPLE,
                    )
                ),
                partial_exit_ratio=float(
                    config.get(
                        'runner_reserve_partial_exit_ratio',
                        TradingConfig.RUNNER_RESERVE_PARTIAL_EXIT_RATIO,
                    )
                ),
                stop_drawdown_from_peak=float(
                    config.get(
                        'runner_reserve_stop_drawdown',
                        TradingConfig.RUNNER_RESERVE_STOP_DRAWDOWN,
                    )
                ),
                floor_return_after_activation=float(
                    config.get(
                        'runner_reserve_floor_return',
                        TradingConfig.RUNNER_RESERVE_FLOOR_RETURN,
                    )
                ),
                max_hold_seconds=float(
                    config.get(
                        'runner_reserve_max_hold_seconds',
                        TradingConfig.RUNNER_RESERVE_MAX_HOLD_SECONDS,
                    )
                ),
                max_sell_pressure_30s=float(
                    config.get(
                        'runner_reserve_max_sell_pressure_30s',
                        TradingConfig.RUNNER_RESERVE_MAX_SELL_PRESSURE_30S,
                    )
                ),
                min_flow_event_count_30s=int(
                    config.get(
                        'runner_reserve_min_flow_event_count_30s',
                        TradingConfig.RUNNER_RESERVE_MIN_FLOW_EVENT_COUNT_30S,
                    )
                ),
            )
        )
        self.post_graduation_retention_enabled = bool(
            config.get(
                'post_graduation_retention_enabled',
                TradingConfig.POST_GRADUATION_RETENTION_ENABLED,
            )
        )
        self.post_graduation_retention_shadow_enabled = bool(
            config.get(
                'post_graduation_retention_shadow_enabled',
                TradingConfig.POST_GRADUATION_RETENTION_SHADOW_ENABLED,
            )
        )
        self.post_graduation_retention = PostGraduationRetentionPolicy(
            RetentionHealthConfig(
                enabled=bool(
                    self.post_graduation_retention_enabled
                    or self.post_graduation_retention_shadow_enabled
                ),
                max_hold_seconds=float(
                    config.get(
                        'post_graduation_retention_max_hold_seconds',
                        TradingConfig.POST_GRADUATION_RETENTION_MAX_HOLD_SECONDS,
                    )
                ),
                max_data_age_seconds=float(
                    config.get(
                        'post_graduation_retention_max_data_age_seconds',
                        TradingConfig.POST_GRADUATION_RETENTION_MAX_DATA_AGE_SECONDS,
                    )
                ),
                min_liquidity_usd=float(
                    config.get(
                        'post_graduation_retention_min_liquidity_usd',
                        TradingConfig.POST_GRADUATION_RETENTION_MIN_LIQUIDITY_USD,
                    )
                ),
                min_volume_liquidity_ratio_5m=float(
                    config.get(
                        'post_graduation_retention_min_volume_liquidity_ratio_5m',
                        TradingConfig.POST_GRADUATION_RETENTION_MIN_VOLUME_LIQUIDITY_RATIO_5M,
                    )
                ),
                min_new_traders_1h=float(
                    config.get(
                        'post_graduation_retention_min_new_traders_1h',
                        TradingConfig.POST_GRADUATION_RETENTION_MIN_NEW_TRADERS_1H,
                    )
                ),
                max_sell_pressure_5m=float(
                    config.get(
                        'post_graduation_retention_max_sell_pressure_5m',
                        TradingConfig.POST_GRADUATION_RETENTION_MAX_SELL_PRESSURE_5M,
                    )
                ),
                max_drawdown_from_peak=float(
                    config.get(
                        'post_graduation_retention_max_drawdown',
                        TradingConfig.POST_GRADUATION_RETENTION_MAX_DRAWDOWN,
                    )
                ),
            )
        )
        self.entry_price_protection_pct = self._optional_nonnegative_float(config.get('entry_price_protection_pct', None))
        self.entry_price_protection_source = 'manual' if self._config_has_value('entry_price_protection_pct') else 'default'
        self.max_concurrent_positions = max(
            0,
            int(config.get('max_concurrent_positions', TradingConfig.MAX_CONCURRENT_POSITIONS) or 0),
        )
        self.buy_confirm_poll_interval_seconds = max(
            0.1,
            float(config.get('buy_confirm_poll_interval_seconds', TradingConfig.BUY_CONFIRM_POLL_INTERVAL_SECONDS) or 1.0),
        )
        self.buy_confirm_timeout_seconds = max(
            self.buy_confirm_poll_interval_seconds,
            float(config.get('buy_confirm_timeout_seconds', TradingConfig.BUY_CONFIRM_TIMEOUT_SECONDS) or 120),
        )
        self.buy_use_lifecycle_fast_status = self._optional_bool(
            config.get('buy_use_lifecycle_fast_status', TradingConfig.BUY_USE_LIFECYCLE_FAST_STATUS)
        )
        self.buy_fast_status_max_staleness_seconds = max(
            0.1,
            float(
                config.get(
                    'buy_fast_status_max_staleness_seconds',
                    TradingConfig.BUY_FAST_STATUS_MAX_STALENESS_SECONDS,
                )
                or TradingConfig.BUY_FAST_STATUS_MAX_STALENESS_SECONDS
            ),
        )
        self.buy_fast_status_max_chain_lag_seconds = max(
            0.1,
            float(
                config.get(
                    'buy_fast_status_max_chain_lag_seconds',
                    TradingConfig.BUY_FAST_STATUS_MAX_CHAIN_LAG_SECONDS,
                )
                or TradingConfig.BUY_FAST_STATUS_MAX_CHAIN_LAG_SECONDS
            ),
        )
        self.buy_action_policy_router_enabled = self._optional_bool(
            config.get(
                'buy_action_policy_router_enabled',
                TradingConfig.BUY_ACTION_POLICY_ROUTER_ENABLED,
            )
        )
        self.buy_action_policy_router_shadow_audit_enabled = self._optional_bool(
            config.get(
                'buy_action_policy_router_shadow_audit_enabled',
                TradingConfig.BUY_ACTION_POLICY_ROUTER_SHADOW_AUDIT_ENABLED,
            )
        )
        self.buy_action_policy_router_train_rejected_reports = self._path_list(
            config.get(
                'buy_action_policy_router_train_rejected_reports',
                TradingConfig.BUY_ACTION_POLICY_ROUTER_TRAIN_REJECTED_REPORTS,
            )
        )
        self.buy_action_policy_router_train_accepted_reports = self._path_list(
            config.get(
                'buy_action_policy_router_train_accepted_reports',
                TradingConfig.BUY_ACTION_POLICY_ROUTER_TRAIN_ACCEPTED_REPORTS,
            )
        )
        self.buy_action_policy_router_min_confidence = self._optional_probability(
            config.get(
                'buy_action_policy_router_min_confidence',
                TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_CONFIDENCE,
            ),
            'buy_action_policy_router_min_confidence',
        )
        self.buy_action_policy_router_max_depth = max(
            1,
            int(
                config.get(
                    'buy_action_policy_router_max_depth',
                    TradingConfig.BUY_ACTION_POLICY_ROUTER_MAX_DEPTH,
                )
                or TradingConfig.BUY_ACTION_POLICY_ROUTER_MAX_DEPTH
            ),
        )
        self.buy_action_policy_router_min_samples_leaf = max(
            1,
            int(
                config.get(
                    'buy_action_policy_router_min_samples_leaf',
                    TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_SAMPLES_LEAF,
                )
                or TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_SAMPLES_LEAF
            ),
        )
        self.buy_action_policy_router_min_common_features = max(
            1,
            int(
                config.get(
                    'buy_action_policy_router_min_common_features',
                    TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_COMMON_FEATURES,
                )
                or TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_COMMON_FEATURES
            ),
        )
        self.buy_action_policy_router_min_live_features = max(
            1,
            int(
                config.get(
                    'buy_action_policy_router_min_live_features',
                    TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_LIVE_FEATURES,
                )
                or TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_LIVE_FEATURES
            ),
        )
        action_policy_continue_hold_activation = config.get(
            'buy_action_policy_continue_hold_activation_pct',
            TradingConfig.BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT,
        )
        if action_policy_continue_hold_activation is None:
            action_policy_continue_hold_activation = TradingConfig.BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT
        self.buy_action_policy_continue_hold_activation_pct = self._optional_nonnegative_runtime_float(
            action_policy_continue_hold_activation,
            'buy_action_policy_continue_hold_activation_pct',
        )
        action_policy_continue_hold_release = config.get(
            'buy_action_policy_continue_hold_release_pct',
            TradingConfig.BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT,
        )
        if action_policy_continue_hold_release is None:
            action_policy_continue_hold_release = TradingConfig.BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT
        self.buy_action_policy_continue_hold_release_pct = self._optional_nonnegative_runtime_float(
            action_policy_continue_hold_release,
            'buy_action_policy_continue_hold_release_pct',
        )
        if (
            self.buy_action_policy_continue_hold_release_pct
            <= self.buy_action_policy_continue_hold_activation_pct
        ):
            raise ValueError(
                "buy_action_policy_continue_hold_release_pct must be greater than activation pct"
            )
        self.action_policy_router_runtime: Optional[ActionPolicyRouterRuntime] = None
        self.action_policy_shadow_runtime: Optional[ActionPolicyRouterRuntime] = None
        for key in (
            'stop_loss',
            'position_size',
            'fixed_stake_bnb',
            'max_entry_size_bnb',
            'hold_time_seconds',
            'min_policy_hold_seconds',
            'trailing_start_pct',
            'trailing_stop_pct',
            'rug_sell_pressure',
            'allow_partial_exits',
            'max_concurrent_positions',
        ):
            if self._config_has_value(key):
                self.exit_param_sources[key] = 'manual'

        # Load Models
        self.hybrid = None
        self.model_manifest = None
        self.inference_future_window_seconds = int(config.get('inference_future_window_seconds', 300) or 300)

        strategy = self._resolve_strategy_params(self.config, model_path=None)
        self.prob_threshold = strategy['values']['prob_threshold']
        self.min_pred_return = strategy['values']['min_pred_return']
        self.max_age_seconds = strategy['values']['max_age_seconds']
        self.strategy_param_sources = strategy['sources']

        # 动态加载 data/models 目录下的最新模型
        self._load_models(config.get('model_dir', 'data/models'))
        self._load_action_policy_router_runtime()

        # Register Handlers
        self._register_handlers()

        # Periodic Save
        self.last_save_time = datetime.now()

    def _resolve_strategy_params(self, config: Dict, model_path: Optional[Path]) -> Dict:
        resolved = {}
        sources = {}

        keys = ('prob_threshold', 'min_pred_return', 'max_age_seconds')

        for key in keys:
            if key in config and config.get(key) is not None:
                resolved[key] = config[key]
                sources[key] = 'manual'
            else:
                resolved[key] = self._strategy_defaults[key]
                sources[key] = 'default'

        return {
            'values': {
                'prob_threshold': float(resolved['prob_threshold']),
                'min_pred_return': float(resolved['min_pred_return']),
                'max_age_seconds': int(resolved['max_age_seconds']),
            },
            'sources': sources,
        }

    def _resolve_use_pred_return_filter(self, config: Dict) -> bool:
        """Resolve pred-return filter switch from the single supported runtime key only."""
        return bool(config.get('use_pred_return_filter', False))

    @staticmethod
    def _normalize_entry_ranking_mode(value) -> str:
        mode = str(value or "chronological").strip().lower()
        if mode not in {"chronological", "buy_prob", "entry_value"}:
            raise ValueError(f"unsupported entry_ranking_mode: {mode}")
        return mode

    @staticmethod
    def _optional_bool(value) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _path_list(value) -> List[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value).split(",") if item.strip()]

    @staticmethod
    def _optional_float(value):
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _optional_nonnegative_float(value):
        if value is None:
            return None
        return max(0.0, float(value))

    @staticmethod
    def _optional_probability(value, name: str):
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number) or number <= 0.0 or number > 1.0:
            raise ValueError(f"{name} must be positive and <= 1.0")
        return number

    @staticmethod
    def _optional_nonnegative_runtime_float(value, name: str):
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number) or number < 0.0:
            raise ValueError(f"{name} must be non-negative")
        return number

    def _config_has_value(self, key: str) -> bool:
        if self.config.get("model_manifest_authoritative") and key in MODEL_MANIFEST_RUNTIME_KEYS:
            override_keys = self.config.get("model_manifest_manual_override_keys", set()) or set()
            if isinstance(override_keys, str):
                override_keys = {item.strip() for item in override_keys.split(",") if item.strip()}
            if key not in override_keys:
                return False
        if key == 'fixed_stake_bnb':
            return key in self.config
        return key in self.config and self.config.get(key) is not None

    def _artifacts_support_pred_return(self) -> bool:
        if self.hybrid is None:
            return False

        if callable(getattr(self.hybrid, 'predict_return', None)):
            return True

        return False

    def _validate_pred_return_filter_contract(self):
        if not self.use_pred_return_filter:
            return

        if self._artifacts_support_pred_return():
            return

        model_hint = str(self.model_path) if self.model_path is not None else "<none>"
        raise ValueError(
            "use_pred_return_filter=true requires artifacts with predicted-return support; "
            f"loaded artifacts do not support predicted return (model_path={model_hint})"
        )

    def _validate_entry_value_ranking_contract(self):
        if self.entry_ranking_mode != "entry_value":
            return

        if self._artifacts_support_pred_return():
            return

        model_hint = str(self.model_path) if self.model_path is not None else "<none>"
        raise ValueError(
            "entry_ranking_mode=entry_value requires artifacts with predicted-return support; "
            f"loaded artifacts do not support predicted return (model_path={model_hint})"
        )

    def _load_model_manifest(self, model_path: Path) -> Optional[Dict]:
        manifest_path = model_path / "hybrid_manifest.json"
        if not manifest_path.exists():
            return None

        try:
            with manifest_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            logger.warning(f"Failed to load model manifest from {manifest_path}: {exc}")
            return None

        return payload if isinstance(payload, dict) else None

    def _load_model_threshold_value(self, model_path: Path):
        threshold_path = model_path / "buy_threshold.json"
        if not threshold_path.exists():
            return None
        try:
            with threshold_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return float(payload.get("threshold"))
        except Exception as exc:
            logger.warning(f"Failed to load buy threshold from {threshold_path}: {exc}")
            return None

    def _model_artifact_is_deployable(self, model_path: Path) -> bool:
        threshold = self._load_model_threshold_value(model_path)
        if threshold is None or threshold < 0.999:
            return True

        manifest = self._load_model_manifest(model_path)
        if manifest is None:
            return False

        evaluation = manifest.get("evaluation", {})
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        runtime_replay = evaluation.get("runtime_replay", {})
        runtime_replay = runtime_replay if isinstance(runtime_replay, dict) else {}
        total_trades = evaluation.get("total_trades", runtime_replay.get("total_trades"))

        artifacts = manifest.get("artifacts", {})
        artifacts = artifacts if isinstance(artifacts, dict) else {}
        buy_model = artifacts.get("buy_model", {})
        buy_model = buy_model if isinstance(buy_model, dict) else {}
        risk_tuning = buy_model.get("risk_tuning", {})
        risk_tuning = risk_tuning if isinstance(risk_tuning, dict) else {}

        if risk_tuning.get("status") == "infeasible":
            return False
        if total_trades is not None:
            try:
                return int(total_trades) > 0
            except Exception:
                return False
        return False

    @staticmethod
    def _as_float(value, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _as_int(value, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except Exception:
            return default

    def _model_artifact_selection_score(self, model_path: Path):
        manifest = self._load_model_manifest(model_path)
        if not manifest:
            return (0, 0, -1e12, 0.0, 0.0, -1e12, 0.0, 0, model_path.name)

        evaluation = manifest.get("evaluation", {})
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        runtime_replay = evaluation.get("runtime_replay", {})
        runtime_replay = runtime_replay if isinstance(runtime_replay, dict) else {}
        selection = manifest.get("selection", {})
        selection = selection if isinstance(selection, dict) else {}

        def metric(name: str, default: float = 0.0) -> float:
            value = evaluation.get(name)
            if value is None:
                value = runtime_replay.get(name)
            return self._as_float(value, default)

        total_trades = self._as_int(evaluation.get("total_trades", runtime_replay.get("total_trades")), 0)
        net_return = metric("net_return_pct", -1e12)
        worst_return = metric("walk_forward_worst_net_return_pct", -1e12)
        max_drawdown = metric("max_drawdown_pct", -1e12)
        win_rate = metric("win_rate", 0.0)
        preferred_drawdown = metric("preferred_max_drawdown_pct", -35.0)

        rolling_validation = evaluation.get("rolling_validation", {})
        rolling_validation = rolling_validation if isinstance(rolling_validation, dict) else {}
        rolling_passed = bool(rolling_validation.get("passed", True))
        risk_passed = (
            total_trades > 0
            and net_return > 0.0
            and worst_return > 0.0
            and max_drawdown >= preferred_drawdown
            and rolling_passed
        )
        reviewed_selection = bool(selection.get("source_replay_report") or selection.get("execution_calibration"))
        robust_score = self._model_artifact_robust_score(evaluation)
        return (
            1 if reviewed_selection else 0,
            1 if risk_passed else 0,
            robust_score,
            net_return,
            worst_return,
            max_drawdown,
            win_rate,
            total_trades,
            model_path.name,
        )

    def _model_artifact_robust_score(self, evaluation: Dict) -> float:
        net_profit = self._as_float(evaluation.get("net_profit_bnb"), 0.0)
        max_drawdown = self._as_float(evaluation.get("max_drawdown_pct"), 0.0)
        worst_walk_forward_return = self._as_float(evaluation.get("walk_forward_worst_net_return_pct"), 0.0)
        preferred_drawdown = abs(self._as_float(evaluation.get("preferred_max_drawdown_pct"), -30.0))

        harsh_rows = [
            row for row in (evaluation.get("stress_replay", []) or [])
            if str(row.get("name", "")) in {"harsh_friction", "harsh_execution"}
        ]
        harsh_profit = min((self._as_float(row.get("net_profit_bnb"), 0.0) for row in harsh_rows), default=0.0)
        harsh_return_pct = min((self._as_float(row.get("net_return_pct"), 0.0) for row in harsh_rows), default=0.0)
        harsh_drawdown_pct = min((self._as_float(row.get("max_drawdown_pct"), 0.0) for row in harsh_rows), default=0.0)

        drawdown_excess = max(0.0, abs(min(0.0, max_drawdown)) - preferred_drawdown)
        walk_forward_loss = max(0.0, -worst_walk_forward_return / 100.0)
        harsh_loss = max(0.0, -harsh_profit)
        harsh_return_loss = max(0.0, -harsh_return_pct / 100.0)
        harsh_drawdown_excess = max(0.0, abs(min(0.0, harsh_drawdown_pct)) - preferred_drawdown) / 100.0

        return float(
            net_profit
            - drawdown_excess * 0.08
            - walk_forward_loss * 2.0
            - harsh_loss * 1.5
            - harsh_return_loss * 0.7
            - harsh_drawdown_excess * 0.5
        )

    def _select_best_model_artifact(self, model_paths: List[Path]) -> Path:
        return max(model_paths, key=self._model_artifact_selection_score)


    def _apply_manifest_runtime_params(self, manifest: Optional[Dict]):
        if not manifest:
            return

        evaluation = manifest.get("evaluation", {})
        if not isinstance(evaluation, dict):
            evaluation = {}
        selected_runtime_params_present = "selected_runtime_params" in manifest
        selected_runtime_params = manifest.get("selected_runtime_params", {})
        if not isinstance(selected_runtime_params, dict):
            selected_runtime_params = {}
        if not evaluation and not selected_runtime_params and not selected_runtime_params_present:
            return

        missing = object()

        def runtime_has_key(key: str) -> bool:
            if selected_runtime_params_present:
                return key in selected_runtime_params
            return key in evaluation

        def runtime_value(key: str, default=None):
            if selected_runtime_params_present:
                return selected_runtime_params.get(key, default)
            return evaluation.get(key, default)

        if not self._config_has_value("prob_threshold"):
            buy_threshold = runtime_value("buy_threshold")
            if buy_threshold is not None:
                self.prob_threshold = float(buy_threshold)
                if self.hybrid is not None:
                    self.hybrid.buy_threshold = float(buy_threshold)
                self.strategy_param_sources["prob_threshold"] = "model_manifest"

        def apply_exit_param(attr: str, manifest_key: str, coerce, *, null_value=missing):
            if self._config_has_value(attr):
                return
            if not runtime_has_key(manifest_key):
                return
            value = runtime_value(manifest_key)
            if value is None:
                if null_value is missing:
                    return
                setattr(self, attr, null_value)
                self.exit_param_sources[attr] = "model_manifest"
                return
            setattr(self, attr, coerce(value))
            self.exit_param_sources[attr] = "model_manifest"

        apply_exit_param("stop_loss", "stop_loss", float)
        apply_exit_param("hold_time_seconds", "max_hold_seconds", lambda value: int(value))
        apply_exit_param("min_policy_hold_seconds", "min_policy_hold_seconds", lambda value: int(value))
        apply_exit_param("position_size", "position_fraction", float)
        apply_exit_param("fixed_stake_bnb", "fixed_stake_bnb", self._optional_float, null_value=None)
        if not self._config_has_value("max_entry_size_bnb"):
            max_entry_size_bnb = runtime_value("max_entry_size_bnb", missing)
            max_position_fraction = runtime_value("max_position_fraction", missing)
            if max_entry_size_bnb is missing and max_position_fraction is not missing and max_position_fraction is not None:
                initial_equity = runtime_value("initial_equity_bnb")
                if initial_equity is not None:
                    max_entry_size_bnb = float(initial_equity) * float(max_position_fraction)
            if max_entry_size_bnb is None:
                self.max_entry_size_bnb = None
                self.exit_param_sources["max_entry_size_bnb"] = "model_manifest"
            elif max_entry_size_bnb is not missing:
                self.max_entry_size_bnb = max(0.0, float(max_entry_size_bnb))
                self.exit_param_sources["max_entry_size_bnb"] = "model_manifest"
        apply_exit_param("trailing_start_pct", "trailing_start_pct", self._optional_float, null_value=None)
        apply_exit_param("trailing_stop_pct", "trailing_stop_pct", self._optional_float, null_value=None)
        apply_exit_param("rug_sell_pressure", "rug_sell_pressure", self._optional_float, null_value=None)
        apply_exit_param("allow_partial_exits", "allow_partial_exits", bool)
        if not self._config_has_value("max_concurrent_positions") and runtime_has_key("max_open_positions"):
            max_open_positions = runtime_value("max_open_positions")
            if max_open_positions is not None:
                max_open_positions = int(max_open_positions)
                if max_open_positions > 0:
                    self.max_concurrent_positions = max_open_positions
                    self.exit_param_sources["max_concurrent_positions"] = "model_manifest"

        if not self._config_has_value("min_pred_return"):
            min_pred_return = runtime_value("min_pred_return")
            if min_pred_return is None:
                min_pred_return = runtime_value("min_entry_score")
            if min_pred_return is not None:
                self.min_pred_return = float(min_pred_return)
                self.strategy_param_sources["min_pred_return"] = "model_manifest"

        if not self._config_has_value("use_pred_return_filter"):
            use_pred_return_filter = runtime_value("use_pred_return_filter")
            if use_pred_return_filter is None and runtime_value("min_entry_score") is not None:
                use_pred_return_filter = True
            if use_pred_return_filter is not None:
                self.use_pred_return_filter = bool(use_pred_return_filter)

        if not self._config_has_value("entry_ranking_mode"):
            entry_ranking_mode = runtime_value("entry_ranking_mode")
            if entry_ranking_mode is not None:
                self.entry_ranking_mode = self._normalize_entry_ranking_mode(entry_ranking_mode)
                self.entry_ranking_mode_source = "model_manifest"

        if not self._config_has_value("entry_price_protection_pct"):
            entry_price_protection_pct = runtime_value("entry_price_protection_pct", missing)
            if entry_price_protection_pct is None:
                self.entry_price_protection_pct = None
                self.entry_price_protection_source = "model_manifest"
            elif entry_price_protection_pct is not missing:
                self.entry_price_protection_pct = self._optional_nonnegative_float(entry_price_protection_pct)
                self.entry_price_protection_source = "model_manifest"

        if not self._config_has_value("max_age_seconds"):
            max_entry_age = runtime_value("max_entry_age_seconds")
            if max_entry_age is not None:
                self.max_age_seconds = int(max_entry_age)
                self.strategy_param_sources["max_age_seconds"] = "model_manifest"
        if not self._config_has_value("min_entry_unique_buyers"):
            value = runtime_value("min_entry_unique_buyers")
            if value is not None:
                self.min_entry_unique_buyers = max(1, int(value))
        if not self._config_has_value("min_entry_buy_count"):
            value = runtime_value("min_entry_buy_count")
            if value is not None:
                self.min_entry_buy_count = max(1, int(value))
        if not self._config_has_value("min_entry_volume_30s"):
            value = runtime_value("min_entry_volume_30s")
            if value is not None:
                self.min_entry_volume_30s = max(0.0, float(value))
        if not self._config_has_value("min_entry_price_volatility"):
            value = runtime_value("min_entry_price_volatility")
            if value is not None:
                self.min_entry_price_volatility = max(0.0, float(value))

        def apply_near_gate_param(attr: str, coerce):
            if self._config_has_value(attr):
                return
            if not runtime_has_key(attr):
                return
            value = runtime_value(attr)
            if value is None:
                setattr(self, attr, None)
                self.near_gate_param_sources[attr] = "model_manifest"
                return
            setattr(self, attr, coerce(value))
            self.near_gate_param_sources[attr] = "model_manifest"

        apply_near_gate_param(
            "buy_near_threshold_min_prob",
            lambda value: self._optional_probability(value, "buy_near_threshold_min_prob"),
        )
        apply_near_gate_param(
            "buy_near_min_pred_return",
            lambda value: self._optional_nonnegative_runtime_float(value, "buy_near_min_pred_return"),
        )
        apply_near_gate_param(
            "buy_near_min_entry_volume_30s",
            lambda value: self._optional_nonnegative_runtime_float(value, "buy_near_min_entry_volume_30s"),
        )
        apply_near_gate_param(
            "buy_near_min_entry_price_volatility",
            lambda value: self._optional_nonnegative_runtime_float(value, "buy_near_min_entry_price_volatility"),
        )
        apply_near_gate_param(
            "buy_near_min_age_seconds",
            lambda value: self._optional_nonnegative_runtime_float(value, "buy_near_min_age_seconds"),
        )

        def apply_primary_score_rescue_param(attr: str, coerce):
            if self._config_has_value(attr):
                return
            if not runtime_has_key(attr):
                return
            value = runtime_value(attr)
            if value is None:
                setattr(self, attr, None)
                self.primary_score_rescue_param_sources[attr] = "model_manifest"
                return
            setattr(self, attr, coerce(value))
            self.primary_score_rescue_param_sources[attr] = "model_manifest"

        apply_primary_score_rescue_param(
            "buy_primary_score_rescue_min_prob",
            lambda value: self._optional_probability(value, "buy_primary_score_rescue_min_prob"),
        )
        apply_primary_score_rescue_param(
            "buy_primary_score_rescue_min_pred_return",
            lambda value: self._optional_nonnegative_runtime_float(value, "buy_primary_score_rescue_min_pred_return"),
        )
        apply_primary_score_rescue_param(
            "buy_primary_score_rescue_min_entry_volume_30s",
            lambda value: self._optional_nonnegative_runtime_float(value, "buy_primary_score_rescue_min_entry_volume_30s"),
        )
        apply_primary_score_rescue_param(
            "buy_primary_score_rescue_min_entry_price_volatility",
            lambda value: self._optional_nonnegative_runtime_float(value, "buy_primary_score_rescue_min_entry_price_volatility"),
        )
        apply_primary_score_rescue_param(
            "buy_primary_score_rescue_min_age_seconds",
            lambda value: self._optional_nonnegative_runtime_float(value, "buy_primary_score_rescue_min_age_seconds"),
        )

    def _extract_lifecycle_features(self, lifecycle: Dict) -> Dict:
        return self.collector._extract_features(
            lifecycle,
            lifecycle['buys'],
            lifecycle['sells'],
            lifecycle['last_update'],
            future_window=self.inference_future_window_seconds,
            include_flow_features=getattr(self, "include_flow_features", False),
        )

    @staticmethod
    def _runner_market_snapshot(lifecycle: Dict, features: Dict) -> Dict:
        """Add a point-in-time 30s flow heartbeat for the reserve policy."""
        market = dict(features or {})
        try:
            anchor = float(lifecycle.get('last_update') or datetime.now().timestamp())
        except (TypeError, ValueError):
            anchor = datetime.now().timestamp()
        recent_buys = []
        recent_sells = []
        for key, target in (('buys', recent_buys), ('sells', recent_sells)):
            for row in lifecycle.get(key) or []:
                try:
                    timestamp = float(row.get('timestamp', 0) or 0)
                    amount = float(row.get('bnb_amount', 0) or 0)
                except (AttributeError, TypeError, ValueError):
                    continue
                if amount >= 0.0 and 0.0 <= anchor - timestamp <= 30.0:
                    target.append(amount)
        buy_volume = sum(recent_buys)
        sell_volume = sum(recent_sells)
        total_volume = buy_volume + sell_volume
        market['flow_buy_volume_30s'] = buy_volume
        market['flow_sell_volume_30s'] = sell_volume
        market['flow_total_volume_30s'] = total_volume
        market['flow_event_count_30s'] = len(recent_buys) + len(recent_sells)
        market['sell_pressure_30s'] = sell_volume / total_volume if total_volume > 0.0 else 0.0
        return market

    @staticmethod
    def _post_graduation_market_snapshot(
        position: Mapping[str, Any],
        lifecycle: Mapping[str, Any],
        features: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Collect only explicit DEX health fields and retain their provenance.

        A future DEX adapter can attach a ``post_graduation_market`` mapping to
        the position or lifecycle.  Curve features are not substituted for DEX
        health because doing so would make a missing post-graduation feed look
        healthy.
        """

        field_names = {
            "graduated",
            "dex_liquidity_usd",
            "liquidity_usd",
            "pair_liquidity_usd",
            "liquidity",
            "volume_liquidity_ratio_5m",
            "dex_volume_liquidity_ratio_5m",
            "volume_to_liquidity_5m",
            "dex_volume_5m_usd",
            "volume_5m_usd",
            "volume_5m",
            "current_price",
            "price_current",
            "mid_price",
            "dex_price",
            "dex_price_usd",
            "price",
            "new_traders_1h",
            "new_trader_growth_1h",
            "dex_new_traders_1h",
            "sell_pressure_5m",
            "dex_sell_pressure_5m",
            "sell_pressure",
            "dex_buy_volume_5m_usd",
            "buy_volume_5m_usd",
            "buy_volume_5m",
            "dex_sell_volume_5m_usd",
            "sell_volume_5m_usd",
            "sell_volume_5m",
            "observed_at",
            "dex_observed_at",
            "market_timestamp",
            "updated_at",
            "timestamp",
            "data_age_seconds",
            "dex_data_age_seconds",
            "data_source",
            "dex_data_source",
            "source",
            "chain_id",
            "pair_address",
            "pair",
            "pair_created_at",
            "block_number",
            "log_index",
            "tx_hash",
        }
        market: Dict[str, Any] = {}
        sources = ((lifecycle, True), (features or {}, False), (position, True))
        for source, allow_top_level in sources:
            if not isinstance(source, Mapping):
                continue
            if allow_top_level:
                for key in field_names:
                    if key in source and source.get(key) is not None:
                        market[key] = source[key]
            nested = source.get("post_graduation_market")
            if isinstance(nested, Mapping):
                for key in field_names:
                    if key in nested and nested.get(key) is not None:
                        market[key] = nested[key]
        if position.get("runner_graduated"):
            market["graduated"] = True
        return market

    def _defers_short_hold_timeout(
        self,
        position: Mapping[str, Any],
        age_seconds: float,
        lifecycle: Mapping[str, Any] | None = None,
    ) -> bool:
        """Let an enforced DEX-health runner use its longer conditional horizon."""

        if (
            self.post_graduation_retention_enabled
            and not self.post_graduation_retention_shadow_enabled
            and bool(position.get("runner_graduated"))
            and str(position.get("runner_state") or "").lower() == "active"
        ):
            return float(age_seconds) < float(self.post_graduation_retention.config.max_hold_seconds)
        return self.runner_reserve.defers_short_hold_timeout(position, age_seconds)

    @staticmethod
    def _post_graduation_snapshot_sort_key(snapshot: Mapping[str, Any]) -> float:
        for key in ("observed_at", "timestamp", "updated_at"):
            try:
                value = float(snapshot.get(key))
            except (AttributeError, TypeError, ValueError):
                continue
            if math.isfinite(value):
                return value
        return float("inf")

    def update_post_graduation_market(self, token_address: str, snapshot: Mapping[str, Any]) -> bool:
        """Attach one adapter snapshot to an open position for shadow/replay use."""

        if token_address not in self.positions or not isinstance(snapshot, Mapping):
            return False
        normalized = {str(key): value for key, value in snapshot.items()}

        def store(target: dict[str, Any]) -> None:
            current = target.setdefault("post_graduation_market", {})
            if not isinstance(current, dict):
                current = {}
                target["post_graduation_market"] = current
            current.update(normalized)
            history = target.setdefault("post_graduation_snapshots", [])
            if not isinstance(history, list):
                history = []
                target["post_graduation_snapshots"] = history
            marker = json.dumps(normalized, sort_keys=True, default=str)
            existing_markers = {
                json.dumps(item, sort_keys=True, default=str)
                for item in history
                if isinstance(item, Mapping)
            }
            if marker not in existing_markers:
                history.append(dict(normalized))
            history.sort(key=self._post_graduation_snapshot_sort_key)

        lifecycle = getattr(self.collector, "token_lifecycle", {}).get(token_address)
        store(self.positions[token_address])
        if isinstance(lifecycle, dict):
            store(lifecycle)
        self._save_state()
        return True

    def _action_policy_router_runtime_params(self) -> Dict[str, Any]:
        return {
            "buy_threshold": self.prob_threshold,
            "buy_near_threshold_min_prob": self.buy_near_threshold_min_prob,
            "buy_near_min_pred_return": self.buy_near_min_pred_return,
            "buy_near_min_entry_volume_30s": self.buy_near_min_entry_volume_30s,
            "buy_near_min_entry_price_volatility": self.buy_near_min_entry_price_volatility,
            "buy_near_min_age_seconds": self.buy_near_min_age_seconds,
            "min_entry_score": self.min_pred_return if self.use_pred_return_filter else None,
            "min_entry_volume_30s": self.min_entry_volume_30s,
            "min_entry_price_volatility": self.min_entry_price_volatility,
        }

    def _build_action_policy_router_runtime(self) -> ActionPolicyRouterRuntime:
        if self.hybrid is None:
            raise ValueError("buy_action_policy_router_enabled requires loaded hybrid artifacts")
        if (
            not self.buy_action_policy_router_train_rejected_reports
            or not self.buy_action_policy_router_train_accepted_reports
        ):
            raise ValueError(
                "buy_action_policy_router_enabled requires rejected and accepted train report paths"
            )

        runtime = ActionPolicyRouterRuntime.from_report_paths(
            train_rejected_report_paths=self.buy_action_policy_router_train_rejected_reports,
            train_accepted_report_paths=self.buy_action_policy_router_train_accepted_reports,
            runtime_params=self._action_policy_router_runtime_params(),
            min_confidence=float(self.buy_action_policy_router_min_confidence or 0.4),
            max_depth=self.buy_action_policy_router_max_depth,
            min_samples_leaf=self.buy_action_policy_router_min_samples_leaf,
            min_common_features=self.buy_action_policy_router_min_common_features,
            min_live_features=self.buy_action_policy_router_min_live_features,
        )
        if not runtime.enabled:
            reasons = runtime.metadata.get("support_reasons", [])
            raise ValueError(f"action policy router support gate failed: {reasons}")

        return runtime

    def _load_action_policy_router_runtime(self):
        if self.buy_action_policy_router_enabled:
            runtime = self._build_action_policy_router_runtime()
            self.action_policy_router_runtime = runtime
            if self.buy_action_policy_router_shadow_audit_enabled:
                self.action_policy_shadow_runtime = runtime
            self.include_flow_features = True
            logger.info(
                "✅ Action policy router loaded | routes=%s | features=%s | continue_hold_activation=%.4f | release=%.4f",
                ",".join(runtime.route_names),
                len(runtime.feature_names),
                float(self.buy_action_policy_continue_hold_activation_pct),
                float(self.buy_action_policy_continue_hold_release_pct),
            )
            return

        if not self.buy_action_policy_router_shadow_audit_enabled:
            return

        try:
            runtime = self._build_action_policy_router_runtime()
        except Exception as exc:
            self.action_policy_shadow_runtime = None
            logger.warning("Action policy router shadow audit disabled: %s", exc)
            return

        self.action_policy_shadow_runtime = runtime
        logger.info(
            "✅ Action policy router shadow audit loaded | routes=%s | features=%s",
            ",".join(runtime.route_names),
            len(runtime.feature_names),
        )

    @staticmethod
    def _action_policy_feature_names_require_flow(feature_names) -> bool:
        if not isinstance(feature_names, (list, tuple, set)):
            return False
        names = [str(name) for name in feature_names]
        return requires_flow_features(names) or any(name.startswith("flow_") for name in names)

    def _action_policy_shadow_features(self, lifecycle: Dict, features_dict: Dict) -> Dict:
        runtime = self.action_policy_shadow_runtime
        if (
            runtime is None
            or not self._action_policy_feature_names_require_flow(getattr(runtime, "feature_names", None))
            or getattr(self, "include_flow_features", False)
        ):
            return features_dict

        try:
            return self.collector._extract_features(
                lifecycle,
                lifecycle['buys'],
                lifecycle['sells'],
                lifecycle['last_update'],
                future_window=self.inference_future_window_seconds,
                include_flow_features=True,
            )
        except Exception as exc:
            logger.warning("Action policy shadow feature extraction failed: %s", exc)
            return features_dict

    def _action_policy_shadow_source_groups(self) -> Dict[str, List[str]]:
        runtime = self.action_policy_shadow_runtime
        metadata = getattr(runtime, "metadata", {}) if runtime is not None else {}
        if not isinstance(metadata, dict):
            metadata = {}
        source_groups = metadata.get("source_groups", {})
        if not isinstance(source_groups, dict):
            source_groups = {}
        return {
            "train_rejected": [
                str(item)
                for item in source_groups.get("train_rejected", [])
            ],
            "train_accepted": [
                str(item)
                for item in source_groups.get("train_accepted", [])
            ],
        }

    def _predict_action_policy_shadow_route(
        self,
        *,
        token_address: str,
        lifecycle: Dict,
        features_dict: Dict,
        prob,
        pred_return,
    ) -> Optional[Dict[str, Any]]:
        runtime = self.action_policy_shadow_runtime
        if runtime is None:
            return None
        try:
            shadow_features = self._action_policy_shadow_features(lifecycle, features_dict)
            return runtime.predict(
                lifecycle=lifecycle,
                features=shadow_features,
                prob=float(prob),
                pred_return=pred_return,
                token_address=token_address,
                sample_time=lifecycle.get('last_update'),
                create_timestamp=lifecycle.get('create_timestamp'),
            )
        except Exception as exc:
            logger.warning("Action policy shadow prediction failed for %s: %s", token_address, exc)
            return {
                "used": False,
                "route": "skip",
                "confidence": 0.0,
                "reason": "shadow_prediction_error",
                "live_feature_count": 0,
                "route_probabilities": {},
            }

    def _action_policy_shadow_audit_fields(
        self,
        route_decision: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if (
            not self.buy_action_policy_router_shadow_audit_enabled
            or self.action_policy_shadow_runtime is None
            or not route_decision
        ):
            return {}

        runtime = self.action_policy_shadow_runtime
        runtime_params = getattr(runtime, "runtime_params", {})
        if not isinstance(runtime_params, dict):
            runtime_params = {}
        source_groups = self._action_policy_shadow_source_groups()
        return {
            "action_policy_shadow_enabled": True,
            "action_policy_shadow_used": bool(route_decision.get("used", False)),
            "action_policy_shadow_route": route_decision.get("route"),
            "action_policy_shadow_confidence": route_decision.get("confidence"),
            "action_policy_shadow_reason": route_decision.get("reason"),
            "action_policy_shadow_live_feature_count": route_decision.get("live_feature_count"),
            "action_policy_shadow_route_probabilities": route_decision.get("route_probabilities", {}),
            "action_policy_shadow_min_confidence": getattr(
                runtime,
                "min_confidence",
                self.buy_action_policy_router_min_confidence,
            ),
            "action_policy_shadow_min_live_features": getattr(
                runtime,
                "min_live_features",
                self.buy_action_policy_router_min_live_features,
            ),
            "action_policy_shadow_runtime_params": runtime_params,
            "action_policy_shadow_train_rejected_reports": source_groups["train_rejected"],
            "action_policy_shadow_train_accepted_reports": source_groups["train_accepted"],
        }

    def _predict_action_policy_route(
        self,
        *,
        token_address: str,
        lifecycle: Dict,
        features_dict: Dict,
        prob,
        pred_return,
    ) -> Optional[Dict[str, Any]]:
        runtime = self.action_policy_router_runtime
        if runtime is None:
            return None
        try:
            return runtime.predict(
                lifecycle=lifecycle,
                features=features_dict,
                prob=float(prob),
                pred_return=pred_return,
                token_address=token_address,
                sample_time=lifecycle.get('last_update'),
                create_timestamp=lifecycle.get('create_timestamp'),
            )
        except Exception as exc:
            logger.warning("Action policy router prediction failed for %s: %s", token_address, exc)
            return {
                "used": False,
                "route": "skip",
                "confidence": 0.0,
                "reason": "router_prediction_error",
                "live_feature_count": 0,
            }

    @staticmethod
    def _action_policy_route_audit_fields(route_decision: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not route_decision:
            return {}
        return {
            "action_policy_router_used": bool(route_decision.get("used", False)),
            "action_policy_router_route": route_decision.get("route"),
            "action_policy_router_confidence": route_decision.get("confidence"),
            "action_policy_router_reason": route_decision.get("reason"),
            "action_policy_router_live_feature_count": route_decision.get("live_feature_count"),
        }

    def _action_policy_route_position_fields(
        self,
        route_decision: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if (
            not route_decision
            or not route_decision.get("used", False)
            or str(route_decision.get("route") or "") != "continue_hold"
        ):
            return {}
        return {
            "action_policy_router_used": True,
            "action_policy_router_route": "continue_hold",
            "action_policy_router_confidence": float(route_decision.get("confidence") or 0.0),
            "action_policy_continue_hold_activation_pct": float(
                self.buy_action_policy_continue_hold_activation_pct
            ),
            "action_policy_continue_hold_release_pct": float(
                self.buy_action_policy_continue_hold_release_pct
            ),
            "action_policy_continue_hold_forced_hold_count": 0,
        }

    @staticmethod
    def _continue_hold_number(value) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _continue_hold_suppresses_policy_sell(
        self,
        pos: Dict,
        *,
        pnl_pct: float,
        peak_pnl_pct: float,
    ) -> bool:
        if str(pos.get("action_policy_router_route") or "") != "continue_hold":
            return False
        activation = self._continue_hold_number(
            pos.get(
                "action_policy_continue_hold_activation_pct",
                self.buy_action_policy_continue_hold_activation_pct,
            )
        )
        release = self._continue_hold_number(
            pos.get(
                "action_policy_continue_hold_release_pct",
                self.buy_action_policy_continue_hold_release_pct,
            )
        )
        if activation is None or release is None or release <= activation:
            return False
        return peak_pnl_pct >= activation and pnl_pct < release

    def _record_continue_hold_policy_suppression(
        self,
        *,
        token_address: str,
        pos: Dict,
        pnl_pct: float,
        peak_pnl_pct: float,
        action: int,
    ):
        try:
            pos["action_policy_continue_hold_forced_hold_count"] = (
                int(pos.get("action_policy_continue_hold_forced_hold_count", 0) or 0) + 1
            )
        except Exception:
            pos["action_policy_continue_hold_forced_hold_count"] = 1

        now_ts = datetime.now().timestamp()
        last_log_ts = self._continue_hold_number(pos.get("last_action_policy_continue_hold_log_ts")) or 0.0
        if now_ts - last_log_ts < 5.0:
            return
        pos["last_action_policy_continue_hold_log_ts"] = now_ts
        self._log_signal_audit({
            "action": "ACTION_POLICY_CONTINUE_HOLD_PPO_SUPPRESSED",
            "token": token_address,
            "symbol": pos.get("symbol"),
            "ppo_action": int(action),
            "pnl_pct": float(pnl_pct),
            "peak_pnl_pct": float(peak_pnl_pct),
            "action_policy_router_confidence": pos.get("action_policy_router_confidence"),
            "action_policy_continue_hold_activation_pct": pos.get(
                "action_policy_continue_hold_activation_pct"
            ),
            "action_policy_continue_hold_release_pct": pos.get(
                "action_policy_continue_hold_release_pct"
            ),
        })

    def _load_models(self, model_dir: str):
        """Load trained hybrid ML models"""
        from src.model.hybrid_inference import HybridModel
        path = Path(model_dir)
        if not (path / "buy_model.cbm").exists():
            if path.exists() and path.is_dir():
                subdirs = sorted([d for d in path.iterdir() if d.is_dir() and (d / "buy_model.cbm").exists()])
                if subdirs:
                    deployable_subdirs = [d for d in subdirs if self._model_artifact_is_deployable(d)]
                    if deployable_subdirs:
                        path = self._select_best_model_artifact(deployable_subdirs)
                        skipped = ", ".join(d.name for d in subdirs if d not in deployable_subdirs)
                        if skipped:
                            logger.warning(
                                "Skipping no-trade model artifact(s): %s; loading %s",
                                skipped,
                                path,
                            )
                        latest_deployable = deployable_subdirs[-1]
                        if path != latest_deployable:
                            logger.warning(
                                "Selected best replay model artifact instead of latest: selected=%s latest=%s",
                                path,
                                latest_deployable,
                            )
                    else:
                        path = subdirs[-1]
                else:
                    logger.warning(f"No hybrid models found in {path}! Bot will only collect data.")
                    self._validate_pred_return_filter_contract()
                    self._validate_entry_value_ranking_contract()
                    return
            else:
                logger.warning(f"Model path {path} does not exist! Bot will only collect data.")
                self._validate_pred_return_filter_contract()
                self._validate_entry_value_ranking_contract()
                return

        logger.info(f"📂 Loading hybrid models from: {path}")
        try:
            self.hybrid = HybridModel.load(str(path))
            self.include_flow_features = requires_flow_features(getattr(self.hybrid, "feature_names", None))
            self.model_path = path
            self.model_manifest = self._load_model_manifest(path)
            self._apply_manifest_runtime_params(self.model_manifest)
            self._validate_pred_return_filter_contract()
            self._validate_entry_value_ranking_contract()

            if self._config_has_value('prob_threshold'):
                self.hybrid.buy_threshold = float(self.config.get('prob_threshold'))
                self.prob_threshold = float(self.hybrid.buy_threshold)
                self.strategy_param_sources['prob_threshold'] = 'manual'
            elif self.strategy_param_sources.get('prob_threshold') != 'model_manifest':
                self.prob_threshold = float(self.hybrid.buy_threshold)
                self.strategy_param_sources['prob_threshold'] = 'model'

            logger.info(
                f"✅ Hybrid models loaded | buy_threshold={self.hybrid.buy_threshold:.2f} | "
                f"sell_policy={'PPO' if self.hybrid.sell_policy is not None else 'rules'}"
            )
        except Exception as e:
            if isinstance(e, ValueError):
                raise
            logger.error(f"Failed to load hybrid models: {e}")
            self._validate_pred_return_filter_contract()
            self._validate_entry_value_ranking_contract()

    def _register_handlers(self):
        """Register event handlers with listener"""
        self.listener.register_handler('TokenCreate', self._on_token_create)
        self.listener.register_handler('TokenPurchase', self._on_trade)
        self.listener.register_handler('TokenSale', self._on_trade)
        self.listener.register_handler('TokenPurchaseV1', self._on_trade)
        self.listener.register_handler('TokenSaleV1', self._on_trade)
        self.listener.register_handler('TokenPurchase2', self._on_trade)
        self.listener.register_handler('TokenSale2', self._on_trade)
        self.listener.register_handler('TradeStop', self._on_trade_stop)
        self.listener.register_handler('LiquidityAdded', self._on_trade_stop)

    async def _on_token_create(self, event_name, event_data):
        self.collector.on_token_create(event_data)
        args = event_data.get('args', {})
        symbol = args.get('symbol', 'UNKNOWN')
        token_address = args.get('token')
        token_hint = token_address[:10] if isinstance(token_address, str) else 'unknown'
        logger.info(f"🆕 New Token Detected: {symbol} ({token_hint}...)")

    async def _on_trade(self, event_name, event_data):
        """Listener 回调仅入队，避免在回调中做重计算阻塞事件循环。"""
        if self._collector_event_queue is None:
            self._collector_event_queue = asyncio.Queue(maxsize=self.collector_event_queue_size)

        try:
            self._collector_event_queue.put_nowait((event_name, event_data))
            self.collector_events_enqueued += 1
        except asyncio.QueueFull:
            # 队列满说明消费已经落后，记录并阻塞等待（不丢事件）
            logger.error(
                f"❌ Collector queue full ({self._collector_event_queue.qsize()}/{self.collector_event_queue_size}); "
                "listener callback is backpressured"
            )
            await self._collector_event_queue.put((event_name, event_data))
            self.collector_events_enqueued += 1

    async def _on_trade_stop(self, event_name, event_data):
        args = event_data.get('args', {})
        token_address = args.get('token') or args.get('base')
        self.collector.on_trade_stop(event_data)
        if token_address:
            await self._enqueue_analysis_token(token_address)
        if token_address in self.positions:
            position = self.positions[token_address]
            if self.runner_reserve.should_survive_graduation(position):
                position['runner_graduated'] = True
                position['runner_graduated_at'] = datetime.now()
                self._save_state()
                logger.info(
                    f"🎓 Token {token_address} graduated; retaining active runner reserve "
                    "until its health/timeout policy exits."
                )
                return
            # Graduation is a venue transition, not a profit target. Keep the
            # position under ordinary target/stop/time management; a DEX feed
            # can update its price after this event.
            position['graduation_observed'] = True
            position['graduation_observed_at'] = datetime.now()
            self._save_state()
            logger.info(
                f"🎓 Token {token_address} graduated; no forced exit, "
                "continuing normal target/stop/time management."
            )

    async def _enqueue_analysis_token(self, token_address: Optional[str]):
        if not token_address:
            return
        if not hasattr(self, "_queued_analysis_tokens"):
            self._queued_analysis_tokens = set()
        if token_address in self._queued_analysis_tokens:
            return
        try:
            self._analysis_event_queue.put_nowait(token_address)
            self._queued_analysis_tokens.add(token_address)
        except asyncio.QueueFull:
            logger.error(
                f"❌ Analysis queue full ({self._analysis_event_queue.qsize()}/{self.analysis_event_queue_size}); "
                "analysis producer is backpressured"
            )
            await self._analysis_event_queue.put(token_address)
            self._queued_analysis_tokens.add(token_address)

    def _buy_slot_count(self, ignore_signal_token: Optional[str] = None) -> int:
        pending_signals = set(self._pending_buy_signals)
        if ignore_signal_token:
            pending_signals.discard(ignore_signal_token)
        return int(len(self.positions) + len(self.pending_buys) + len(pending_signals))

    def _cap_entry_size(self, size_bnb: float) -> float:
        size = max(0.0, float(size_bnb or 0.0))
        if self.max_entry_size_bnb is None:
            return size
        if self.max_entry_size_bnb <= 0.0:
            return 0.0
        return min(size, float(self.max_entry_size_bnb))

    def _entry_size_bnb(self) -> float:
        if self.fixed_stake_bnb is not None:
            capped_stake = self._cap_entry_size(float(self.fixed_stake_bnb))
            return capped_stake if self.balance + 1e-12 >= capped_stake else 0.0
        if self.position_size < 1:
            size_bnb = self.balance * self.position_size
        else:
            size_bnb = min(self.position_size, self.balance)
        return self._cap_entry_size(size_bnb)

    def _has_entry_cash_capacity(self, ignore_signal_token: Optional[str] = None) -> bool:
        if self.fixed_stake_bnb is None:
            return self._entry_size_bnb() >= 0.0001

        pending_signals = set(self._pending_buy_signals)
        if ignore_signal_token:
            pending_signals.discard(ignore_signal_token)
        reserved_count = len(self.pending_buys) + len(pending_signals)
        effective_stake = self._cap_entry_size(float(self.fixed_stake_bnb))
        available_balance = self.balance - (reserved_count * effective_stake)
        return effective_stake >= 0.0001 and available_balance + 1e-12 >= effective_stake

    def _has_buy_capacity(self, ignore_signal_token: Optional[str] = None) -> bool:
        max_positions = int(self.max_concurrent_positions)
        slot_available = True
        if max_positions > 0:
            slot_available = self._buy_slot_count(ignore_signal_token=ignore_signal_token) < max_positions
        return bool(slot_available and self._has_entry_cash_capacity(ignore_signal_token=ignore_signal_token))

    def _can_replace_queued_buy_signal(self) -> bool:
        return bool(
            self.entry_ranking_mode in {"buy_prob", "entry_value"}
            and not self._buy_signal_queue.empty()
        )

    @staticmethod
    def _json_safe(value):
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, dict):
            return {str(key): MemeBot._json_safe(val) for key, val in sorted(value.items(), key=lambda item: str(item[0]))}
        if isinstance(value, (list, tuple)):
            return [MemeBot._json_safe(item) for item in value]
        if isinstance(value, set):
            return sorted(MemeBot._json_safe(item) for item in value)
        return str(value)

    @classmethod
    def _features_hash(cls, features: Dict) -> str:
        payload = json.dumps(cls._json_safe(features), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _log_signal_audit(self, event: Dict):
        try:
            payload = {
                "time": datetime.now(),
                **event,
            }
            with self.signal_audit_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, default=str, sort_keys=True) + "\n")
        except Exception as exc:
            logger.error(f"Failed to save signal audit event: {exc}")

    def _entry_price_protection_skip(self, *, signal_price: float, candidate_price: float) -> bool:
        if self.entry_price_protection_pct is None:
            return False
        signal = float(signal_price or 0.0)
        candidate = float(candidate_price or 0.0)
        if signal <= 0.0 or candidate <= 0.0:
            return False
        return bool(candidate > signal * (1.0 + float(self.entry_price_protection_pct)))

    def _fresh_lifecycle_token_status(self, lifecycle: Dict) -> Optional[Dict]:
        if not self.buy_use_lifecycle_fast_status:
            return None

        local_last_update = float(lifecycle.get('last_update_local') or 0.0)
        chain_last_update = float(lifecycle.get('last_update') or 0.0)
        last_update = local_last_update if local_last_update > 0.0 else chain_last_update
        if last_update <= 0.0:
            return None

        staleness = max(0.0, datetime.now().timestamp() - last_update)
        if staleness > float(self.buy_fast_status_max_staleness_seconds):
            return None

        chain_lag = self._lifecycle_chain_lag_seconds(lifecycle)
        if chain_lag is not None and chain_lag > float(self.buy_fast_status_max_chain_lag_seconds):
            return None

        price = float(lifecycle.get('price_current') or 0.0)
        price_first = self._positive_lifecycle_price(lifecycle.get('price_first'))
        price_peak = self._positive_lifecycle_price(lifecycle.get('price_max'))
        status = {
            'exists': True,
            'ready': False,
            'price': price,
            'launch_time': lifecycle.get('launch_time', 0),
            'reason': '',
            'source': 'lifecycle',
            'staleness_seconds': staleness,
            'chain_lag_seconds': chain_lag,
            'lifecycle_price_current': price if price > 0.0 else None,
            'lifecycle_price_first': price_first,
            'lifecycle_price_peak': price_peak,
            'lifecycle_price_from_first_pct': self._price_change_pct(
                current=price,
                baseline=price_first,
            ),
            'lifecycle_price_from_peak_pct': self._price_change_pct(
                current=price,
                baseline=price_peak,
            ),
        }

        launch_time = int(lifecycle.get('launch_time') or 0)
        current_time = int(datetime.now().timestamp())
        if launch_time > current_time:
            status['reason'] = f"Not launched yet ({launch_time} > {current_time})"
            return status

        if lifecycle.get('graduated'):
            status['reason'] = 'Graduated/Liquidity Added'
            return status

        if price <= 0.0:
            status['reason'] = 'Lifecycle price is 0'
            return status

        status['ready'] = True
        status['reason'] = 'OK'
        return status

    def _signal_lifecycle_freshness_fields(self, lifecycle: Dict) -> Dict:
        local_last_update = self._positive_timestamp(lifecycle.get('last_update_local'))
        chain_last_update = self._positive_timestamp(lifecycle.get('last_update'))
        freshness_anchor = local_last_update if local_last_update is not None else chain_last_update
        now_ts = datetime.now().timestamp()
        staleness = None
        chain_lag = None
        if freshness_anchor is not None:
            staleness = max(0.0, now_ts - freshness_anchor)
        if chain_last_update is not None:
            chain_lag = max(0.0, now_ts - chain_last_update)
        fast_status_eligible = bool(
            self.buy_use_lifecycle_fast_status
            and staleness is not None
            and staleness <= float(self.buy_fast_status_max_staleness_seconds)
            and (
                chain_lag is None
                or chain_lag <= float(self.buy_fast_status_max_chain_lag_seconds)
            )
        )
        return {
            "lifecycle_status_staleness_seconds": staleness,
            "lifecycle_status_chain_lag_seconds": chain_lag,
            "lifecycle_status_has_local_update": local_last_update is not None,
            "lifecycle_status_has_chain_update": chain_last_update is not None,
            "lifecycle_status_fast_status_enabled": bool(self.buy_use_lifecycle_fast_status),
            "lifecycle_status_fast_status_eligible": fast_status_eligible,
            "buy_fast_status_max_staleness_seconds": float(self.buy_fast_status_max_staleness_seconds),
            "buy_fast_status_max_chain_lag_seconds": float(self.buy_fast_status_max_chain_lag_seconds),
        }

    @staticmethod
    def _positive_lifecycle_price(value):
        try:
            price = float(value or 0.0)
        except (TypeError, ValueError):
            return None
        return price if price > 0.0 else None

    @staticmethod
    def _positive_timestamp(value):
        try:
            timestamp = float(value or 0.0)
        except (TypeError, ValueError):
            return None
        return timestamp if timestamp > 0.0 else None

    @staticmethod
    def _price_change_pct(*, current: float, baseline: Optional[float]):
        if baseline is None or baseline <= 0.0 or current <= 0.0:
            return None
        return (current / baseline) - 1.0

    @staticmethod
    def _lifecycle_price_attribution_fields(status: Optional[Dict]) -> Dict:
        if not isinstance(status, dict) or status.get('source') != 'lifecycle':
            return {}
        keys = (
            'lifecycle_price_current',
            'lifecycle_price_first',
            'lifecycle_price_peak',
            'lifecycle_price_from_first_pct',
            'lifecycle_price_from_peak_pct',
        )
        return {key: status.get(key) for key in keys if key in status}

    @staticmethod
    def _lifecycle_chain_lag_seconds(lifecycle: Dict):
        chain_last_update = float(lifecycle.get('last_update') or 0.0)
        if chain_last_update <= 0.0:
            return None
        return max(0.0, datetime.now().timestamp() - chain_last_update)

    @staticmethod
    def _entry_slippage_pct(*, signal_price: float, entry_price: float):
        signal = float(signal_price or 0.0)
        entry = float(entry_price or 0.0)
        if signal <= 0.0 or entry <= 0.0:
            return None
        return (entry / signal) - 1.0

    @staticmethod
    def _hex_from_topic(topic) -> str:
        if hasattr(topic, "hex"):
            raw = topic.hex()
        else:
            raw = str(topic)
        return raw[2:] if raw.startswith("0x") else raw

    @staticmethod
    def _bytes_from_log_data(data) -> bytes:
        if isinstance(data, bytes):
            return data
        if isinstance(data, bytearray):
            return bytes(data)
        raw = str(data)
        return bytes.fromhex(raw[2:] if raw.startswith("0x") else raw)

    @classmethod
    def _decode_fourmeme_trade_log_price(cls, log, *, token_address: str, expected_topics) -> Optional[float]:
        try:
            topics = log.get("topics") if isinstance(log, dict) else getattr(log, "topics", None)
            if not topics:
                return None
            topic0 = cls._hex_from_topic(topics[0])
            if topic0 not in expected_topics:
                return None

            data = log.get("data") if isinstance(log, dict) else getattr(log, "data", None)
            payload = cls._bytes_from_log_data(data)
            decoded_token = None
            amount = 0
            cost = 0
            if len(topics) == 1 and len(payload) >= 160:
                decoded_token = "0x" + payload[12:32].hex()
                amount = int.from_bytes(payload[96:128], "big")
                cost = int.from_bytes(payload[128:160], "big")
                if amount == 0 or cost == 0:
                    amount = int.from_bytes(payload[64:96], "big")
                    cost = int.from_bytes(payload[96:128], "big")
            elif len(topics) >= 3 and len(payload) >= 96:
                decoded_token = "0x" + cls._hex_from_topic(topics[1])[-40:]
                amount = int.from_bytes(payload[32:64], "big")
                cost = int.from_bytes(payload[64:96], "big")
            elif len(topics) == 2 and len(payload) >= 128:
                decoded_token = "0x" + cls._hex_from_topic(topics[1])[-40:]
                amount = int.from_bytes(payload[64:96], "big")
                cost = int.from_bytes(payload[96:128], "big")

            if (
                decoded_token is None
                or decoded_token.lower() != str(token_address).lower()
                or amount <= 0
                or cost <= 0
            ):
                return None
            return cost / amount
        except Exception:
            return None

    async def _sell_execution_price_from_receipt(self, token_address: str, tx_hash: Optional[str]) -> Optional[float]:
        if not tx_hash or not TradingConfig.ENABLE_TRADING:
            return None
        try:
            receipt = await self.executor.w3.eth.get_transaction_receipt(tx_hash)
            logs = receipt.get("logs") if isinstance(receipt, dict) else getattr(receipt, "logs", None)
            for log in logs or []:
                price = self._decode_fourmeme_trade_log_price(
                    log,
                    token_address=token_address,
                    expected_topics=FOURMEME_SALE_TOPICS,
                )
                if price is not None:
                    return price
        except Exception as exc:
            logger.debug(f"Could not resolve sell execution price from receipt for {token_address}: {exc}")
        return None

    def _is_valid_token_address(self, token_address: str) -> bool:
        try:
            checker = getattr(self.executor.w3, "is_address", None)
            if callable(checker):
                checked = checker(token_address)
                if isinstance(checked, bool):
                    return checked
            self.executor.w3.to_checksum_address(token_address)
            return True
        except Exception:
            return False

    def _log_entry_price_protection_skip(
        self,
        *,
        token_address: str,
        symbol: str,
        signal_price: float,
        candidate_price: float,
        prob,
        pred_return,
        primary_score_rescue_used: bool = False,
        extra_fields: Optional[Dict] = None,
    ):
        payload = {
            "action": "ENTRY_PRICE_PROTECTION_SKIP",
            "token": token_address,
            "symbol": symbol,
            "signal_price": float(signal_price),
            "candidate_price": float(candidate_price),
            "entry_slippage_pct": self._entry_slippage_pct(
                signal_price=signal_price,
                entry_price=candidate_price,
            ),
            "entry_price_protection_pct": self.entry_price_protection_pct,
            "prob": prob,
            "pred_return": pred_return,
            "primary_score_rescue_used": bool(primary_score_rescue_used),
        }
        if extra_fields:
            payload.update(extra_fields)
        self._log_signal_audit(payload)

    @staticmethod
    def _feature_value(features_dict: Dict, key: str) -> float:
        try:
            value = float(features_dict.get(key, 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return value if math.isfinite(value) else 0.0

    @staticmethod
    def _lifecycle_age_seconds(lifecycle: Dict) -> float:
        try:
            return max(
                0.0,
                float(lifecycle.get('last_update', 0.0) or 0.0)
                - float(lifecycle.get('create_timestamp', 0.0) or 0.0),
            )
        except (TypeError, ValueError):
            return 0.0

    def _near_threshold_rescue_prob_candidate(self, prob: float) -> bool:
        if self.buy_near_threshold_min_prob is None:
            return False
        try:
            probability = float(prob)
        except (TypeError, ValueError):
            return False
        return (
            math.isfinite(probability)
            and probability >= float(self.buy_near_threshold_min_prob)
            and probability < float(self.prob_threshold)
        )

    def _near_threshold_rescue_reject_reason(self, *, prob, pred_return, features_dict: Dict, lifecycle: Dict):
        if not self._near_threshold_rescue_prob_candidate(prob):
            return "near_threshold_prob_out_of_range"
        if self.buy_near_min_pred_return is None:
            return "near_threshold_disabled"
        if pred_return is None:
            return "near_threshold_pred_return_unavailable"
        if float(pred_return) < float(self.buy_near_min_pred_return):
            return "near_threshold_pred_return_below_min"
        if (
            self.buy_near_min_entry_volume_30s is not None
            and self._feature_value(features_dict, 'volume_30s') < float(self.buy_near_min_entry_volume_30s)
        ):
            return "near_threshold_volume_30s_below_min"
        if (
            self.buy_near_min_entry_price_volatility is not None
            and self._feature_value(features_dict, 'price_volatility') < float(self.buy_near_min_entry_price_volatility)
        ):
            return "near_threshold_price_volatility_below_min"

        age_seconds = self._lifecycle_age_seconds(lifecycle)
        if (
            self.buy_near_min_age_seconds is not None
            and age_seconds < float(self.buy_near_min_age_seconds)
        ):
            return "near_threshold_age_out_of_range"
        if age_seconds > float(self.max_age_seconds):
            return "near_threshold_age_out_of_range"
        return None

    def _primary_score_rescue_prob_candidate(self, prob: float) -> bool:
        if (
            self.buy_primary_score_rescue_min_prob is None
            or self.buy_primary_score_rescue_min_pred_return is None
        ):
            return False
        try:
            probability = float(prob)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(probability):
            return False
        return probability >= max(
            float(self.prob_threshold),
            float(self.buy_primary_score_rescue_min_prob),
        )

    def _primary_score_rescue_reject_reason(self, *, prob, pred_return, features_dict: Dict, lifecycle: Dict):
        if not self._primary_score_rescue_prob_candidate(prob):
            return "primary_score_rescue_prob_out_of_range"
        if pred_return is None:
            return "primary_score_rescue_pred_return_unavailable"
        try:
            pred_return_value = float(pred_return)
        except (TypeError, ValueError):
            return "primary_score_rescue_pred_return_unavailable"
        if not math.isfinite(pred_return_value):
            return "primary_score_rescue_pred_return_unavailable"
        if pred_return_value < float(self.buy_primary_score_rescue_min_pred_return):
            return "primary_score_rescue_pred_return_below_min"

        volume_floor = self.buy_primary_score_rescue_min_entry_volume_30s
        if volume_floor is None and self.min_entry_volume_30s > 0.0:
            volume_floor = self.min_entry_volume_30s
        if (
            volume_floor is not None
            and self._feature_value(features_dict, 'volume_30s') < float(volume_floor)
        ):
            return "primary_score_rescue_volume_30s_below_min"

        price_volatility_floor = self.buy_primary_score_rescue_min_entry_price_volatility
        if price_volatility_floor is None and self.min_entry_price_volatility > 0.0:
            price_volatility_floor = self.min_entry_price_volatility
        if (
            price_volatility_floor is not None
            and self._feature_value(features_dict, 'price_volatility') < float(price_volatility_floor)
        ):
            return "primary_score_rescue_price_volatility_below_min"

        age_seconds = self._lifecycle_age_seconds(lifecycle)
        if (
            self.buy_primary_score_rescue_min_age_seconds is not None
            and age_seconds < float(self.buy_primary_score_rescue_min_age_seconds)
        ):
            return "primary_score_rescue_age_out_of_range"
        if age_seconds > float(self.max_age_seconds):
            return "primary_score_rescue_age_out_of_range"
        return None

    def _primary_score_rescue_used(self, *, prob, pred_return, features_dict: Dict, lifecycle: Dict):
        if not self.use_pred_return_filter:
            return False
        if pred_return is None:
            return False
        try:
            pred_return_value = float(pred_return)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(pred_return_value) or pred_return_value >= float(self.min_pred_return):
            return False
        return self._primary_score_rescue_reject_reason(
            prob=prob,
            pred_return=pred_return,
            features_dict=features_dict,
            lifecycle=lifecycle,
        ) is None

    def _primary_entry_reject_reason(self, *, features_dict: Dict, pred_return):
        if self.min_entry_volume_30s > 0.0:
            volume_30s = self._feature_value(features_dict, 'volume_30s')
            if volume_30s < float(self.min_entry_volume_30s):
                return "entry_volume_30s_below_min"

        if self.min_entry_price_volatility > 0.0:
            price_volatility = self._feature_value(features_dict, 'price_volatility')
            if price_volatility < float(self.min_entry_price_volatility):
                return "entry_price_volatility_below_min"

        if self.use_pred_return_filter:
            if pred_return is None:
                return "pred_return_unavailable"
            if pred_return < float(self.min_pred_return):
                return "pred_return_below_min"

        return None

    def _run_model_inference(self, lifecycle):
        if self.hybrid is None:
            return 0.0, False, None, {}, "model_unavailable"

        features_dict = self._extract_lifecycle_features(lifecycle)
        prob, model_should_buy = self.hybrid.predict_buy(features_dict)

        pred_return = None
        predict_return_fn = getattr(self.hybrid, 'predict_return', None)
        if callable(predict_return_fn):
            raw_pred_return = predict_return_fn(features_dict)
            try:
                pred_return = None if raw_pred_return is None else float(raw_pred_return)
            except (TypeError, ValueError):
                pred_return = None
            if pred_return is not None and not math.isfinite(pred_return):
                pred_return = None

        if model_should_buy:
            reject_reason = self._primary_entry_reject_reason(
                features_dict=features_dict,
                pred_return=pred_return,
            )
            if reject_reason == "pred_return_below_min" and self._primary_score_rescue_prob_candidate(prob):
                reject_reason = self._primary_score_rescue_reject_reason(
                    prob=prob,
                    pred_return=pred_return,
                    features_dict=features_dict,
                    lifecycle=lifecycle,
                )
            return prob, reject_reason is None, pred_return, features_dict, reject_reason

        if self._near_threshold_rescue_prob_candidate(prob):
            reject_reason = self._near_threshold_rescue_reject_reason(
                prob=prob,
                pred_return=pred_return,
                features_dict=features_dict,
                lifecycle=lifecycle,
            )
            return prob, reject_reason is None, pred_return, features_dict, reject_reason

        return prob, False, pred_return, features_dict, "buy_model_reject"

    async def _process_token_logic(self, token_address: str):
        if not self.active:
            return

        lifecycle = self.collector.token_lifecycle.get(token_address)

        # 持仓的时间退出不依赖价格更新：即使无成交/无新价格，也要能按时卖出
        if token_address in self.positions:
            pos = self.positions[token_address]

            # 防止无限卖出循环：指数退避 + 最大重试次数
            sell_retries = pos.get('sell_retry_count', 0)
            if sell_retries >= 10:
                logger.warning(f"⛔ {pos.get('symbol','?')} 卖出重试已达上限({sell_retries}次)，放弃并移除仓位")
                self.positions.pop(token_address, None)
                self._save_state()
                return
            if 'last_sell_attempt' in pos:
                # 退避: 5s, 15s, 45s, 120s, 300s, 300s...
                cooldown = min(5 * (3 ** sell_retries), 300)
                if (datetime.now() - pos['last_sell_attempt']).total_seconds() < cooldown:
                    return

            time_held = (datetime.now() - pos['entry_time']).total_seconds()
            if (
                time_held >= self.hold_time_seconds
                and not self._defers_short_hold_timeout(pos, time_held, lifecycle)
            ):
                await self._close_position(token_address, reason="TIME_EXIT")
                return

        if not lifecycle:
            return

        current_price = lifecycle.get('price_current', 0)
        if current_price <= 0:
            return  # 价格未初始化，跳过避免误触发止损

        if token_address in self.positions:
            pos = self.positions[token_address]

            tp_base_price = pos.get('tp_base_price', pos['entry_price'])
            pnl_pct = (current_price - tp_base_price) / tp_base_price
            peak_price = max(float(pos.get('peak_price', tp_base_price)), current_price)
            pos['peak_price'] = peak_price
            peak_pnl_pct = (peak_price / tp_base_price) - 1.0 if tp_base_price > 0 else 0.0
            features_dict = None

            def position_features():
                nonlocal features_dict
                if features_dict is None:
                    features_dict = self._extract_lifecycle_features(lifecycle)
                return features_dict

            # Hard stop-loss floor: always enforced regardless of PPO
            if pnl_pct <= self.stop_loss:
                await self._close_position(token_address, reason="STOP_LOSS")
                return

            if self.rug_sell_pressure is not None:
                features = position_features()
                buy_vol = float(features.get("total_buy_volume", 0.0))
                sell_vol = float(features.get("total_sell_volume", 0.0))
                sell_pressure = sell_vol / max(buy_vol + sell_vol, 1e-9)
                if sell_pressure >= float(self.rug_sell_pressure):
                    await self._close_position(token_address, reason="RUG_EXIT")
                    return

            # Once a runner crosses the curve-to-DEX boundary, the old
            # FourMeme flow heartbeat is no longer sufficient. Shadow mode
            # records this policy while leaving the legacy runner untouched.
            retention_enforced = bool(
                self.post_graduation_retention_enabled
                and not self.post_graduation_retention_shadow_enabled
            )
            if (
                bool(pos.get('runner_graduated'))
                and (
                    self.post_graduation_retention_enabled
                    or self.post_graduation_retention_shadow_enabled
                )
            ):
                retention_market = self._post_graduation_market_snapshot(pos, lifecycle)
                retention_price = current_price
                for price_key in ("dex_price_usd", "dex_price", "price", "current_price", "mid_price", "price_current"):
                    try:
                        candidate_price = float(retention_market.get(price_key, 0.0) or 0.0)
                    except (TypeError, ValueError):
                        candidate_price = 0.0
                    if math.isfinite(candidate_price) and candidate_price > 0.0:
                        retention_price = candidate_price
                        break
                retention_decision = self.post_graduation_retention.evaluate(
                    pos,
                    current_price=float(retention_price),
                    now=datetime.now(),
                    market=retention_market,
                )
                self._log_signal_audit({
                    "action": "POST_GRADUATION_RETENTION_DECISION",
                    "token": token_address,
                    "symbol": pos.get('symbol'),
                    "mode": "enforced" if retention_enforced else "shadow",
                    "decision": retention_decision.action,
                    "reason": retention_decision.reason,
                    "health_score": retention_decision.health_score,
                    "checks": dict(retention_decision.checks),
                    "missing_fields": list(retention_decision.missing_fields),
                    **dict(retention_decision.provenance),
                })
                if retention_enforced:
                    retention_updates = dict(retention_decision.updates)
                    if retention_decision.action in {"hold", "close"}:
                        try:
                            prior_runner_peak = float(pos.get("runner_peak_price", 0.0) or 0.0)
                        except (TypeError, ValueError):
                            prior_runner_peak = 0.0
                        try:
                            prior_peak = float(pos.get("peak_price", 0.0) or 0.0)
                        except (TypeError, ValueError):
                            prior_peak = 0.0
                        peak = max(prior_runner_peak, prior_peak, float(retention_price))
                        retention_updates["runner_peak_price"] = peak
                        retention_updates["peak_price"] = peak
                    if retention_decision.action == "close":
                        pos.update(retention_updates)
                        await self._close_position(
                            token_address,
                            reason=str(retention_decision.reason).upper(),
                        )
                        return
                    if retention_decision.action == "hold":
                        changed = any(pos.get(key) != value for key, value in retention_updates.items())
                        pos.update(retention_updates)
                        if changed:
                            self._save_state()
                        return

            runner_decision = self.runner_reserve.evaluate(
                pos,
                current_price=float(current_price),
                now=datetime.now(),
                market=(
                    self._runner_market_snapshot(lifecycle, position_features())
                    if self.runner_reserve.config.enabled
                    else {}
                ),
            )
            if runner_decision.action == "partial_exit":
                partial_succeeded = await self._partial_sell(
                    token_address,
                    sell_ratio=float(runner_decision.partial_exit_ratio or 0.0),
                    reason="RUNNER_ACTIVATION_2X",
                )
                if partial_succeeded:
                    pos.update(dict(runner_decision.updates))
                    pos['runner_partial_exit_ratio'] = float(runner_decision.partial_exit_ratio or 0.0)
                    self._save_state()
                return
            if runner_decision.action == "close":
                pos.update(dict(runner_decision.updates))
                await self._close_position(token_address, reason=str(runner_decision.reason).upper())
                return
            if runner_decision.action == "hold":
                runner_updates = dict(runner_decision.updates)
                changed = any(pos.get(key) != value for key, value in runner_updates.items())
                pos.update(runner_updates)
                if changed:
                    self._save_state()
                return

            if self.trailing_start_pct is not None and self.trailing_stop_pct is not None:
                drawdown_from_peak_pct = (current_price / peak_price) - 1.0 if peak_price > 0 else 0.0
                if (
                    peak_pnl_pct >= float(self.trailing_start_pct)
                    and drawdown_from_peak_pct <= -float(self.trailing_stop_pct)
                ):
                    price_source = str(lifecycle.get('price_current_source') or 'event')
                    if price_source != 'event':
                        now_dt = datetime.now()
                        last_deferred_log = pos.get('last_trailing_defer_log')
                        if (
                            not isinstance(last_deferred_log, datetime)
                            or (now_dt - last_deferred_log).total_seconds() >= 5
                        ):
                            self._log_signal_audit({
                                "action": "TRAILING_STOP_DEFERRED",
                                "token": token_address,
                                "symbol": pos.get("symbol"),
                                "reason": "price_source_not_event",
                                "price_current_source": price_source,
                                "current_price": current_price,
                                "peak_price": peak_price,
                                "drawdown_from_peak_pct": drawdown_from_peak_pct,
                                "trailing_start_pct": self.trailing_start_pct,
                                "trailing_stop_pct": self.trailing_stop_pct,
                                "primary_score_rescue_used": bool(pos.get('primary_score_rescue_used', False)),
                            })
                            pos['last_trailing_defer_log'] = now_dt
                        return
                    await self._close_position(token_address, reason="TRAILING_STOP")
                    return

            # PPO sell decision
            if self.hybrid is not None and self.hybrid.sell_policy is not None:
                if time_held < self.min_policy_hold_seconds:
                    return
                features_dict = position_features()
                buy_vol = float(features_dict.get("total_buy_volume", 0.0))
                sell_vol = float(features_dict.get("total_sell_volume", 0.0))
                tp_base_price = pos.get('tp_base_price', pos['entry_price'])
                initial_size_bnb = float(pos.get('initial_size_bnb', pos.get('size_bnb', 0.0)) or 0.0)
                current_size_bnb = float(pos.get('size_bnb', 0.0) or 0.0)
                position_remaining = current_size_bnb / max(initial_size_bnb, 1e-9)
                obs = build_sell_observation(
                    {
                        "mid_price": current_price,
                        "lp_depth": float(features_dict.get("launch_fee", 0.0)),
                        "sell_pressure": sell_vol / max(buy_vol + sell_vol, 1e-9),
                        "buy_sell_ratio": buy_vol / max(sell_vol, 1e-9),
                        "holders": float(features_dict.get("holder_count", 0.0)),
                        "ts": float(lifecycle.get('last_update', datetime.now().timestamp())),
                    },
                    entry_price=tp_base_price,
                    peak_price=peak_price,
                    position_remaining=position_remaining,
                    entry_ts=pos['entry_time'].timestamp(),
                    episode_start_ts=float(lifecycle.get('create_timestamp', pos['entry_time'].timestamp())),
                )
                action = self.hybrid.predict_sell(obs)
                if action in (1, 2, 3) and self._continue_hold_suppresses_policy_sell(
                    pos,
                    pnl_pct=pnl_pct,
                    peak_pnl_pct=peak_pnl_pct,
                ):
                    self._record_continue_hold_policy_suppression(
                        token_address=token_address,
                        pos=pos,
                        pnl_pct=pnl_pct,
                        peak_pnl_pct=peak_pnl_pct,
                        action=int(action),
                    )
                    return
                if not self.allow_partial_exits and action in (1, 2, 3):
                    await self._close_position(token_address, reason="PPO_SELL100")
                    return
                if action == 1:
                    if self.allow_partial_exits:
                        await self._partial_sell(token_address, sell_ratio=0.25, reason="PPO_SELL25")
                        return
                elif action == 2:
                    if self.allow_partial_exits:
                        await self._partial_sell(token_address, sell_ratio=0.50, reason="PPO_SELL50")
                        return
                elif action == 3:
                    await self._close_position(token_address, reason="PPO_SELL100")
                    return
                # action == 0: hold, fall through to time exit
            else:
                # Fallback: rule-based sell (original logic)
                if pnl_pct >= self.first_take_profit and not pos.get('partial_sold', False):
                    first_tp_label = int(round(self.first_take_profit * 100))
                    await self._partial_sell(
                        token_address,
                        sell_ratio=self.first_exit_ratio,
                        reason=f"FIRST_TP_{first_tp_label}"
                    )
                    pos['partial_sold'] = True
                    pos['peak_price'] = current_price
                    return

                if pos.get('partial_sold', False):
                    if 'peak_price' not in pos:
                        tp_base_price = pos.get('tp_base_price', pos.get('entry_price', 0))
                        pos['peak_price'] = max(current_price, tp_base_price * (1.0 + self.first_take_profit))
                    else:
                        pos['peak_price'] = max(pos['peak_price'], current_price)
                    drawdown_pct = (current_price - pos['peak_price']) / pos['peak_price']
                    if drawdown_pct <= -self.drawdown_stop:
                        await self._close_position(token_address, reason="POST_TP_DRAWDOWN_EXIT")
                        return

            # Base positions use the short timeout; active reserves use their
            # own max-hold guard through the policy above.
            time_held = (datetime.now() - pos['entry_time']).total_seconds()
            if (
                time_held >= self.hold_time_seconds
                and not self._defers_short_hold_timeout(pos, time_held, lifecycle)
            ):
                await self._close_position(token_address, reason="TIME_EXIT")
                return
            return

        if token_address in self.pending_buys:
            return
        if token_address in self.closed_tokens:
            return

        if not self._has_buy_capacity() and not self._can_replace_queued_buy_signal():
            return

        now = datetime.now().timestamp()
        if token_address in self.failed_buys:
            if now < self.failed_buys[token_address]:
                return
            else:
                self.failed_buys.pop(token_address)

        if self.hybrid is None:
            return

        time_since_launch = lifecycle['last_update'] - lifecycle['create_timestamp']
        if time_since_launch > self.max_age_seconds:
            return

        # 活跃度过滤: 排除单人币/低活跃度币（不限制最低时间，靠买家数和交易数过滤质量）
        unique_buyers_count = len(lifecycle.get('unique_buyers', set()))
        total_buys = len(lifecycle.get('buys', []))
        if unique_buyers_count < self.min_entry_unique_buyers or total_buys < self.min_entry_buy_count:
            return

        try:
            prob, should_buy, pred_return, features_dict, reject_reason = await asyncio.to_thread(self._run_model_inference, lifecycle)
            near_threshold_rescue_used = bool(
                should_buy
                and self._near_threshold_rescue_prob_candidate(prob)
            )
            primary_score_rescue_used = bool(
                should_buy
                and self._primary_score_rescue_used(
                    prob=prob,
                    pred_return=pred_return,
                    features_dict=features_dict,
                    lifecycle=lifecycle,
                )
            )

            pred_return_text = "n/a" if pred_return is None else f"{pred_return:.2f}"
            logger.info(
                f"🧐 Analysis: {lifecycle['symbol']} | Score: {prob:.4f} | Buy: {should_buy} | "
                f"PredReturn: {pred_return_text} | Age: {time_since_launch:.0f}s"
            )
            action_policy_shadow_route = None

            if should_buy:
                action_policy_route = self._predict_action_policy_route(
                    token_address=token_address,
                    lifecycle=lifecycle,
                    features_dict=features_dict,
                    prob=prob,
                    pred_return=pred_return,
                )
                if (
                    self.action_policy_shadow_runtime is not None
                    and self.action_policy_shadow_runtime is self.action_policy_router_runtime
                    and action_policy_route is not None
                ):
                    action_policy_shadow_route = action_policy_route
                else:
                    action_policy_shadow_route = self._predict_action_policy_shadow_route(
                        token_address=token_address,
                        lifecycle=lifecycle,
                        features_dict=features_dict,
                        prob=prob,
                        pred_return=pred_return,
                    )
                enqueue_result = await self._enqueue_buy_signal(
                    token_address,
                    lifecycle,
                    prob,
                    pred_return=pred_return,
                    primary_score_rescue_used=primary_score_rescue_used,
                    action_policy_route=action_policy_route,
                )
                self._log_signal_audit({
                    "action": "SIGNAL_DECISION",
                    "token": token_address,
                    "symbol": lifecycle.get("symbol"),
                    "decision": enqueue_result or "dropped",
                    "reason": enqueue_result or "unknown",
                    "prob": float(prob),
                    "pred_return": pred_return,
                    "features_hash": self._features_hash(features_dict),
                    "feature_count": len(features_dict),
                    "entry_ranking_mode": self.entry_ranking_mode,
                    "min_pred_return": float(self.min_pred_return),
                    "use_pred_return_filter": bool(self.use_pred_return_filter),
                    "min_entry_volume_30s": float(self.min_entry_volume_30s),
                    "volume_30s": float(features_dict.get("volume_30s", 0.0) or 0.0),
                    "min_entry_price_volatility": float(self.min_entry_price_volatility),
                    "price_volatility": float(features_dict.get("price_volatility", 0.0) or 0.0),
                    "token_age_seconds": float(time_since_launch),
                    "near_threshold_rescue_used": near_threshold_rescue_used,
                    "buy_near_threshold_min_prob": self.buy_near_threshold_min_prob,
                    "buy_near_min_pred_return": self.buy_near_min_pred_return,
                    "buy_near_min_entry_volume_30s": self.buy_near_min_entry_volume_30s,
                    "buy_near_min_entry_price_volatility": self.buy_near_min_entry_price_volatility,
                    "buy_near_min_age_seconds": self.buy_near_min_age_seconds,
                    "primary_score_rescue_used": primary_score_rescue_used,
                    "buy_primary_score_rescue_min_prob": self.buy_primary_score_rescue_min_prob,
                    "buy_primary_score_rescue_min_pred_return": self.buy_primary_score_rescue_min_pred_return,
                    "buy_primary_score_rescue_min_entry_volume_30s": self.buy_primary_score_rescue_min_entry_volume_30s,
                    "buy_primary_score_rescue_min_entry_price_volatility": self.buy_primary_score_rescue_min_entry_price_volatility,
                    "buy_primary_score_rescue_min_age_seconds": self.buy_primary_score_rescue_min_age_seconds,
                    **self._signal_lifecycle_freshness_fields(lifecycle),
                    **self._action_policy_route_audit_fields(action_policy_route),
                    **self._action_policy_shadow_audit_fields(action_policy_shadow_route),
                })
            else:
                action_policy_shadow_route = self._predict_action_policy_shadow_route(
                    token_address=token_address,
                    lifecycle=lifecycle,
                    features_dict=features_dict,
                    prob=prob,
                    pred_return=pred_return,
                )
                self._log_signal_audit({
                    "action": "SIGNAL_DECISION",
                    "token": token_address,
                    "symbol": lifecycle.get("symbol"),
                    "decision": "rejected",
                    "reason": reject_reason or "buy_model_reject",
                    "prob": float(prob),
                    "pred_return": pred_return,
                    "features_hash": self._features_hash(features_dict),
                    "feature_count": len(features_dict),
                    "entry_ranking_mode": self.entry_ranking_mode,
                    "min_pred_return": float(self.min_pred_return),
                    "use_pred_return_filter": bool(self.use_pred_return_filter),
                    "min_entry_volume_30s": float(self.min_entry_volume_30s),
                    "volume_30s": float(features_dict.get("volume_30s", 0.0) or 0.0),
                    "min_entry_price_volatility": float(self.min_entry_price_volatility),
                    "price_volatility": float(features_dict.get("price_volatility", 0.0) or 0.0),
                    "token_age_seconds": float(time_since_launch),
                    "near_threshold_rescue_used": near_threshold_rescue_used,
                    "buy_near_threshold_min_prob": self.buy_near_threshold_min_prob,
                    "buy_near_min_pred_return": self.buy_near_min_pred_return,
                    "buy_near_min_entry_volume_30s": self.buy_near_min_entry_volume_30s,
                    "buy_near_min_entry_price_volatility": self.buy_near_min_entry_price_volatility,
                    "buy_near_min_age_seconds": self.buy_near_min_age_seconds,
                    "primary_score_rescue_used": primary_score_rescue_used,
                    "buy_primary_score_rescue_min_prob": self.buy_primary_score_rescue_min_prob,
                    "buy_primary_score_rescue_min_pred_return": self.buy_primary_score_rescue_min_pred_return,
                    "buy_primary_score_rescue_min_entry_volume_30s": self.buy_primary_score_rescue_min_entry_volume_30s,
                    "buy_primary_score_rescue_min_entry_price_volatility": self.buy_primary_score_rescue_min_entry_price_volatility,
                    "buy_primary_score_rescue_min_age_seconds": self.buy_primary_score_rescue_min_age_seconds,
                    **self._signal_lifecycle_freshness_fields(lifecycle),
                    **self._action_policy_shadow_audit_fields(action_policy_shadow_route),
                })

        except Exception as e:
            logger.error(f"Prediction error for {lifecycle.get('symbol', 'Unknown')}: {e}", exc_info=True)

    def _log_trade_to_file(self, trade_data: Dict):
        try:
            with open(self.trade_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(trade_data, default=str) + '\n')
        except Exception as e:
            logger.error(f"Failed to save trade to file: {e}")

    async def _sync_balance(self, force: bool = False):
        now = datetime.now().timestamp()
        if not force and now - self.last_sync_time < self.sync_cooldown:
            return
        if TradingConfig.ENABLE_TRADING and self.executor.wallet_address:
            try:
                balance_wei = await self.executor.w3.eth.get_balance(self.executor.wallet_address)
                self.balance = float(self.executor.w3.from_wei(balance_wei, 'ether'))
                self.last_sync_time = now
                logger.info(f"💰 On-chain balance synced: {self.balance:.4f} BNB")
            except Exception as e:
                logger.error(f"Failed to sync balance: {e}")

    def _buy_signal_queue_item(self, signal: Dict):
        sequence = int(signal.get('sequence', 0))
        if self.entry_ranking_mode == "buy_prob":
            raw_score = signal.get('prob')
        elif self.entry_ranking_mode == "entry_value":
            raw_score = signal.get('pred_return')
        else:
            return (sequence, 0, signal)

        score = float(raw_score) if raw_score is not None else -1.0
        return (-score, sequence, signal)

    @staticmethod
    def _unwrap_buy_signal_queue_item(item):
        if isinstance(item, tuple) and len(item) == 3 and isinstance(item[2], dict):
            return item[2]
        return item

    def _replace_lower_ranked_buy_signal(self, signal: Dict) -> bool:
        if self.entry_ranking_mode not in {"buy_prob", "entry_value"}:
            return False

        candidate_item = self._buy_signal_queue_item(signal)
        queued_items = []
        try:
            while True:
                queued_items.append(self._buy_signal_queue.get_nowait())
        except asyncio.QueueEmpty:
            pass

        if not queued_items:
            return False

        worst_index = max(range(len(queued_items)), key=lambda index: queued_items[index][:2])
        worst_item = queued_items[worst_index]

        if candidate_item[:2] >= worst_item[:2]:
            for item in queued_items:
                self._buy_signal_queue.put_nowait(item)
            return False

        replaced_signal = self._unwrap_buy_signal_queue_item(worst_item)
        for index, item in enumerate(queued_items):
            if index != worst_index:
                self._buy_signal_queue.put_nowait(item)
        self._buy_signal_queue.put_nowait(candidate_item)

        replaced_token = replaced_signal.get('token') if isinstance(replaced_signal, dict) else None
        if replaced_token:
            self._pending_buy_signals.discard(replaced_token)
        self._pending_buy_signals.add(signal['token'])
        logger.info(
            "↕️ Replaced queued buy signal via %s ranking: dropped=%s kept=%s",
            self.entry_ranking_mode,
            replaced_token,
            signal['token'],
        )
        self._log_signal_audit({
            "action": "QUEUE_REPLACE",
            "token": signal.get("token"),
            "replaced_token": replaced_token,
            "entry_ranking_mode": self.entry_ranking_mode,
            "prob": signal.get("prob"),
            "pred_return": signal.get("pred_return"),
            "primary_score_rescue_used": signal.get("primary_score_rescue_used", False),
            **self._action_policy_route_audit_fields(signal.get("action_policy_route")),
        })
        return True

    async def _enqueue_buy_signal(
        self,
        token_address,
        lifecycle,
        prob,
        pred_return=None,
        primary_score_rescue_used: bool = False,
        action_policy_route: Optional[Dict[str, Any]] = None,
    ):
        if token_address in self.pending_buys:
            return "pending_buy_exists"
        if token_address in self.closed_tokens:
            return "already_closed"

        now = datetime.now().timestamp()
        if token_address in self.failed_buys and now < self.failed_buys[token_address]:
            return "failed_buy_cooldown"

        if token_address in self._pending_buy_signals:
            return "already_queued"

        self._buy_signal_sequence += 1
        signal = {
            'token': token_address,
            'lifecycle': lifecycle,
            'prob': prob,
            'pred_return': pred_return,
            'signal_price': float(lifecycle.get('price_current', 0.0) or 0.0),
            'signal_time': datetime.now(),
            'primary_score_rescue_used': bool(primary_score_rescue_used),
            'action_policy_route': action_policy_route,
            'sequence': self._buy_signal_sequence,
        }

        if not self._has_buy_capacity():
            if self._replace_lower_ranked_buy_signal(signal):
                return "replaced"
            logger.info(
                "⏸️ Buy capacity full: positions=%s pending_buys=%s queued_signals=%s max=%s",
                len(self.positions),
                len(self.pending_buys),
                len(self._pending_buy_signals),
                self.max_concurrent_positions,
            )
            return "capacity_full"

        try:
            self._buy_signal_queue.put_nowait(self._buy_signal_queue_item(signal))
            self._pending_buy_signals.add(token_address)
            return "queued"
        except asyncio.QueueFull:
            if self._replace_lower_ranked_buy_signal(signal):
                return "replaced"
            logger.error(
                f"❌ Buy signal queue full ({self._buy_signal_queue.qsize()}/{self.buy_signal_queue_size}); dropping signal for {token_address}"
            )
            return "queue_full"

    async def _buy_worker_loop(self):
        logger.info("🛒 Buy worker loop started")
        while self.active:
            try:
                signal = self._unwrap_buy_signal_queue_item(await self._buy_signal_queue.get())
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Buy worker wait error: {e}")
                continue

            token_address = signal.get('token')
            lifecycle = signal.get('lifecycle')
            prob = signal.get('prob')
            pred_return = signal.get('pred_return')
            signal_price = signal.get('signal_price')
            signal_time = signal.get('signal_time')
            primary_score_rescue_used = bool(signal.get('primary_score_rescue_used', False))
            action_policy_route = signal.get('action_policy_route')

            try:
                if token_address and lifecycle is not None and prob is not None:
                    await self._open_position(
                        token_address,
                        lifecycle,
                        prob,
                        pred_return=pred_return,
                        signal_price=signal_price,
                        signal_time=signal_time,
                        primary_score_rescue_used=primary_score_rescue_used,
                        action_policy_route=action_policy_route,
                    )
            except Exception as e:
                logger.error(f"Buy worker error for {token_address}: {e}")
            finally:
                if token_address:
                    self._pending_buy_signals.discard(token_address)

    async def _open_position(
        self,
        token_address,
        lifecycle,
        prob,
        pred_return=None,
        signal_price=None,
        signal_time=None,
        primary_score_rescue_used: bool = False,
        action_policy_route: Optional[Dict[str, Any]] = None,
    ):
        """Execute Buy"""
        if token_address in self.pending_buys:
            return

        if not self._has_buy_capacity(ignore_signal_token=token_address):
            return

        now = datetime.now().timestamp()
        open_started_at = datetime.now()
        self.pending_buys.add(token_address)
        try:
            size_bnb = self._entry_size_bnb()
            buy_sent_at = None

            if size_bnb < 0.0001:
                logger.warning(f"⚠️ Trade size {size_bnb:.4f} BNB too small, skipping.")
                return

            symbol = lifecycle['symbol']
            signal_price = float(signal_price if signal_price is not None else lifecycle['price_current'])  # 信号触发时的价格
            price = signal_price  # 买入价格（可能因滑点不同）
            tx_hash = None
            actual_size_bnb = size_bnb
            paper_candidate_price = float(lifecycle.get('price_current', signal_price) or signal_price)
            buy_submit_started_at = None
            buy_submit_completed_at = None
            buy_tx_submit_rpc_seconds = None
            buy_preflight_seconds = None
            token_status_check_seconds = None
            buy_token_detect_seconds = None
            buy_detect_poll_count = None
            buy_confirm_poll_interval_used = None
            buy_post_detect_sync_seconds = None
            buy_fast_status_used = False
            token_status_source = None
            lifecycle_status_staleness_seconds = None
            lifecycle_status_chain_lag_seconds = None
            # Populated only by the live lifecycle fast-status path; paper/helper paths omit it.
            lifecycle_price_fields = {}

            if not TradingConfig.ENABLE_TRADING and self._entry_price_protection_skip(
                signal_price=signal_price,
                candidate_price=paper_candidate_price,
            ):
                logger.info(
                    "⛔ Entry price protection skipped %s: signal=%s candidate=%s protection=%s",
                    symbol,
                    signal_price,
                    paper_candidate_price,
                    self.entry_price_protection_pct,
                )
                self._log_entry_price_protection_skip(
                    token_address=token_address,
                    symbol=symbol,
                    signal_price=signal_price,
                    candidate_price=paper_candidate_price,
                    prob=prob,
                    pred_return=pred_return,
                    primary_score_rescue_used=primary_score_rescue_used,
                )
                return

            if TradingConfig.ENABLE_TRADING:
                async with self.trader_lock:
                    nonce_prefetch_task = None
                    prefetch_next_nonce = getattr(self.executor, "prefetch_next_nonce", None)
                    if inspect.iscoroutinefunction(prefetch_next_nonce):
                        nonce_prefetch_task = asyncio.create_task(prefetch_next_nonce())

                        def _consume_nonce_prefetch(done_task):
                            try:
                                done_task.result()
                            except asyncio.CancelledError:
                                pass
                            except Exception as exc:
                                logger.debug(f"Nonce prefetch failed before buy submission: {exc}")

                        nonce_prefetch_task.add_done_callback(_consume_nonce_prefetch)
                        await asyncio.sleep(0)

                    lifecycle_status_chain_lag_seconds = self._lifecycle_chain_lag_seconds(lifecycle)
                    status = self._fresh_lifecycle_token_status(lifecycle)
                    if status is not None:
                        buy_fast_status_used = True
                        token_status_source = "lifecycle"
                        lifecycle_status_staleness_seconds = status.get('staleness_seconds')
                        lifecycle_status_chain_lag_seconds = status.get('chain_lag_seconds')
                        lifecycle_price_fields = self._lifecycle_price_attribution_fields(status)
                        token_status_check_seconds = 0.0
                        quote_checker = getattr(self.executor, "check_token_quote_supported", None)
                        if status.get('ready') and callable(quote_checker):
                            quote_check_result = quote_checker(token_address)
                            if inspect.isawaitable(quote_check_result):
                                status_check_started_at = datetime.now()
                                quote_status = await quote_check_result
                                token_status_check_seconds = max(
                                    0.0,
                                    (datetime.now() - status_check_started_at).total_seconds(),
                                )
                                if isinstance(quote_status, dict):
                                    if quote_status.get('quote') is not None:
                                        status['quote'] = quote_status.get('quote')
                                    if not quote_status.get('ready', False):
                                        status['ready'] = False
                                        status['reason'] = quote_status.get('reason') or 'Unsupported quote asset'
                        logger.info(
                            "⚡ Using lifecycle fast token status: %s | price=%s | stale=%.3fs | chain_lag=%s",
                            symbol,
                            status.get('price'),
                            float(lifecycle_status_staleness_seconds or 0.0),
                            "n/a" if lifecycle_status_chain_lag_seconds is None else f"{float(lifecycle_status_chain_lag_seconds):.3f}s",
                        )
                    else:
                        token_status_source = "helper"
                        # 使用 TradeExecutor 的 check_token_status 进行检查
                        logger.info(f"🔍 Checking token readiness: {symbol} ({token_address})")

                        status_check_started_at = datetime.now()
                        status = await self.executor.check_token_status(token_address)
                        token_status_check_seconds = max(
                            0.0,
                            (datetime.now() - status_check_started_at).total_seconds(),
                        )

                    if not status['ready']:
                        logger.warning(f"⚠️ Token not ready: {symbol} | Reason: {status['reason']}")
                        self._log_signal_audit({
                            "action": "BUY_NOT_READY",
                            "token": token_address,
                            "symbol": symbol,
                            "reason": status.get("reason"),
                            "signal_price": signal_price,
                            "prob": prob,
                            "pred_return": pred_return,
                            "primary_score_rescue_used": bool(primary_score_rescue_used),
                            "buy_fast_status_used": buy_fast_status_used,
                            "token_status_source": token_status_source,
                            "token_quote": status.get("quote"),
                            "lifecycle_status_staleness_seconds": lifecycle_status_staleness_seconds,
                            "lifecycle_status_chain_lag_seconds": lifecycle_status_chain_lag_seconds,
                            **lifecycle_price_fields,
                        })
                        # 根据不同原因设置重试策略
                        if "Not launched yet" in status['reason']:
                            self.failed_buys[token_address] = now + 1.0 # 等待1秒
                        elif "Price is 0" in status['reason']:
                            self.failed_buys[token_address] = now + 0.5
                        elif "Helper query failed" in status['reason']:
                            self.failed_buys[token_address] = now + 1.0  # Helper 可能还没索引到，1秒后重试
                        else: # Graduated or Error
                            self.failed_buys[token_address] = now + 3600
                        return

                    raw_candidate_price = float(status.get('price', signal_price) or signal_price)
                    candidate_price = self._normalize_helper_price(raw_candidate_price, signal_price)
                    if self._entry_price_protection_skip(
                        signal_price=signal_price,
                        candidate_price=candidate_price,
                    ):
                        logger.info(
                            "⛔ Entry price protection skipped %s: signal=%s candidate=%s protection=%s",
                            symbol,
                            signal_price,
                            candidate_price,
                            self.entry_price_protection_pct,
                        )
                        self._log_entry_price_protection_skip(
                            token_address=token_address,
                            symbol=symbol,
                            signal_price=signal_price,
                            candidate_price=candidate_price,
                            prob=prob,
                            pred_return=pred_return,
                            primary_score_rescue_used=primary_score_rescue_used,
                            extra_fields=lifecycle_price_fields,
                        )
                        return

                    price = candidate_price
                    logger.info(f"✅ Token ready - Current price: {candidate_price} (raw={status['price']})")
                    logger.info(f"💰 Executing Real Buy: {symbol} ({token_address}) | Size: {size_bnb:.6f} BNB")

                    buy_submit_started_at = datetime.now()
                    preflight_start = signal_time if signal_time is not None else open_started_at
                    buy_preflight_seconds = max(
                        0.0,
                        (buy_submit_started_at - preflight_start).total_seconds(),
                    )
                    tx_hash = await self.executor.buy_token(
                        token_address, size_bnb, expected_price=candidate_price,
                        skip_estimate=True, wait=False
                    )
                    buy_submit_completed_at = datetime.now()
                    buy_tx_submit_rpc_seconds = max(
                        0.0,
                        (buy_submit_completed_at - buy_submit_started_at).total_seconds(),
                    )

                if not tx_hash:
                    logger.warning(f"⚠️ Real Buy failed for {symbol}. Retrying in 1.5s...")
                    self._log_signal_audit({
                        "action": "BUY_EXECUTION_FAILED",
                        "token": token_address,
                        "symbol": symbol,
                        "signal_price": signal_price,
                        "prob": prob,
                        "pred_return": pred_return,
                        "primary_score_rescue_used": bool(primary_score_rescue_used),
                        "buy_fast_status_used": buy_fast_status_used,
                        "token_status_source": token_status_source,
                        "lifecycle_status_staleness_seconds": lifecycle_status_staleness_seconds,
                        "lifecycle_status_chain_lag_seconds": lifecycle_status_chain_lag_seconds,
                        **lifecycle_price_fields,
                    })
                    self.failed_buys[token_address] = now + 1.5
                    return

                if tx_hash == "ALREADY_SENT":
                    logger.info(f"⏳ {symbol} transaction already in pool, waiting...")
                    self._log_signal_audit({
                        "action": "BUY_ALREADY_SENT",
                        "token": token_address,
                        "symbol": symbol,
                        "signal_price": signal_price,
                        "prob": prob,
                        "pred_return": pred_return,
                        "primary_score_rescue_used": bool(primary_score_rescue_used),
                        "buy_fast_status_used": buy_fast_status_used,
                        "token_status_source": token_status_source,
                        "lifecycle_status_staleness_seconds": lifecycle_status_staleness_seconds,
                        "lifecycle_status_chain_lag_seconds": lifecycle_status_chain_lag_seconds,
                        **lifecycle_price_fields,
                    })
                    return

                logger.info(f"⚡ Buy Tx Sent: {tx_hash}")
                buy_sent_at = buy_submit_completed_at or datetime.now()

                # === 轮询钱包确认买入 ===
                # 直接查 token balance，不依赖 receipt 超时
                abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
                token_contract = self.executor.w3.eth.contract(address=token_address, abi=abi)

                token_balance = 0
                poll_interval = float(self.buy_confirm_poll_interval_seconds)
                buy_confirm_poll_interval_used = poll_interval
                max_polls = max(1, int(math.ceil(self.buy_confirm_timeout_seconds / poll_interval)))
                receipt_poll_every = max(1, int(round(15.0 / poll_interval)))
                for poll in range(max_polls):
                    try:
                        token_balance = await token_contract.functions.balanceOf(self.executor.wallet_address).call()
                        if token_balance > 0:
                            detected_at = datetime.now()
                            buy_detect_poll_count = poll + 1
                            buy_token_detect_seconds = max(0.0, (detected_at - buy_sent_at).total_seconds())
                            elapsed = buy_token_detect_seconds
                            logger.info(f"✅ Token received after {elapsed:.1f}s: {token_balance / 1e18:.2f} tokens")
                            break
                    except Exception:
                        pass

                    # 同时检查交易是否 revert
                    if poll % receipt_poll_every == receipt_poll_every - 1:
                        try:
                            receipt = await self.executor.w3.eth.get_transaction_receipt(tx_hash)
                            if receipt and receipt['status'] == 0:
                                logger.error(f"❌ Buy transaction reverted! {symbol}")
                                self._log_signal_audit({
                                    "action": "BUY_RECEIPT_REVERT",
                                    "token": token_address,
                                    "symbol": symbol,
                                    "signal_price": signal_price,
                                    "prob": prob,
                                    "pred_return": pred_return,
                                    "primary_score_rescue_used": bool(primary_score_rescue_used),
                                    "tx_hash": tx_hash,
                                    **lifecycle_price_fields,
                                })
                                self.failed_buys[token_address] = now + 5
                                return
                        except Exception:
                            pass

                    if poll < max_polls - 1:
                        await asyncio.sleep(poll_interval)

                # 入场价直接用发送金额 / 收到代币数量
                # 不用余额差值：轮询期间其他卖出交易会污染余额差，导致入场价虚低
                actual_size_bnb = size_bnb

                if token_balance > 0:
                    tokens_received = token_balance / 1e18
                    if tokens_received > 0:
                        price = actual_size_bnb / tokens_received
                        logger.info(f"🏷️ Entry Price: {price:.10g} BNB (Cost: {actual_size_bnb:.6f} / Tokens: {tokens_received:.2f})")
                else:
                    # 120s 没收到 token，但钱可能已出去，保守记录持仓
                    logger.warning(f"⚠️ No tokens detected after {max_polls*poll_interval:.1f}s, recording position to avoid fund loss")
                    actual_size_bnb = size_bnb
                if token_balance > 0 and hasattr(self.executor, "schedule_sell_approval"):
                    try:
                        self.executor.schedule_sell_approval(token_address, int(token_balance))
                    except Exception as exc:
                        logger.warning(f"⚠️ Failed to warm up sell approval for {symbol}: {exc}")
            else:
                price = paper_candidate_price
                self.balance -= size_bnb

            opened_at = datetime.now()
            signal_to_open_seconds = None
            entry_submit_seconds = None
            entry_fill_lag_seconds = None
            entry_submit_time = None
            if signal_time is not None:
                signal_to_open_seconds = max(0.0, (opened_at - signal_time).total_seconds())
            if buy_sent_at is not None:
                entry_submit_time = buy_sent_at
                if signal_time is not None:
                    entry_submit_seconds = max(0.0, (buy_sent_at - signal_time).total_seconds())
                entry_fill_lag_seconds = max(0.0, (opened_at - buy_sent_at).total_seconds())
            elif signal_time is not None:
                entry_submit_seconds = signal_to_open_seconds
                entry_fill_lag_seconds = signal_to_open_seconds
            entry_slippage_pct = self._entry_slippage_pct(signal_price=signal_price, entry_price=price)

            logger.info(f"🚀 BUY SIGNAL: {symbol} | Prob: {prob:.4f} | Price: {price} | Size: {actual_size_bnb:.4f} BNB")

            self.positions[token_address] = {
                'symbol': symbol,
                'signal_price': signal_price,  # 信号触发时的价格（用于计算收益）
                'entry_price': price,  # 实际买入价格（可能有滑点）
                'entry_time': opened_at,
                'size_bnb': actual_size_bnb,
                'initial_size_bnb': actual_size_bnb,
                'prob': prob,
                'pred_return': pred_return,
                'primary_score_rescue_used': bool(primary_score_rescue_used),
                'last_log_time': datetime.now(),
                'tx_hash_buy': tx_hash,
                # 基于实盘实际成交价的锚点，避免信号价与成交价偏差导致止盈错判
                'tp_base_price': price,
                'peak_price': price,
                # Runner reserve is armed only when explicitly enabled; the
                # disabled state keeps legacy position files self-describing.
                'runner_state': 'armed' if self.runner_reserve.config.enabled else 'disabled',
                'runner_peak_price': price,
                **self._action_policy_route_position_fields(action_policy_route),
            }
            if TradingConfig.ENABLE_TRADING:
                # Reserve balance locally so the next entry cannot size from stale cash.
                self.balance = max(0.0, float(self.balance or 0.0) - float(actual_size_bnb or 0.0))
                buy_post_detect_sync_seconds = 0.0

                async def _sync_balance_after_open():
                    await self._sync_balance(force=True)

                balance_sync_task = asyncio.create_task(_sync_balance_after_open())
                self._background_tasks.append(balance_sync_task)

                def _consume_balance_sync(done_task):
                    try:
                        done_task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.warning(f"⚠️ Deferred balance sync failed after buy: {exc}")
                    try:
                        self._background_tasks.remove(done_task)
                    except ValueError:
                        pass

                balance_sync_task.add_done_callback(_consume_balance_sync)
            trade_payload = {
                'action': 'OPEN',
                'token': token_address,
                'symbol': symbol,
                'signal_price': signal_price,
                'entry_signal_time': signal_time,
                'entry_due_time': entry_submit_time or signal_time,
                'entry_submit_time': entry_submit_time,
                'entry_submit_seconds': entry_submit_seconds,
                'entry_wait_seconds': signal_to_open_seconds,
                'entry_fill_lag_seconds': entry_fill_lag_seconds,
                'buy_preflight_seconds': buy_preflight_seconds,
                'token_status_check_seconds': token_status_check_seconds,
                'buy_fast_status_used': buy_fast_status_used,
                'token_status_source': token_status_source,
                'lifecycle_status_staleness_seconds': lifecycle_status_staleness_seconds,
                'lifecycle_status_chain_lag_seconds': lifecycle_status_chain_lag_seconds,
                **lifecycle_price_fields,
                'buy_tx_submit_rpc_seconds': buy_tx_submit_rpc_seconds,
                'buy_token_detect_seconds': buy_token_detect_seconds,
                'buy_detect_poll_count': buy_detect_poll_count,
                'buy_confirm_poll_interval_seconds': buy_confirm_poll_interval_used,
                'buy_post_detect_sync_seconds': buy_post_detect_sync_seconds,
                'entry_price': price,
                'entry_slippage_pct': entry_slippage_pct,
                'size': actual_size_bnb,
                'time': opened_at,
                'prob': prob,
                'pred_return': pred_return,
                'primary_score_rescue_used': bool(primary_score_rescue_used),
                **self._action_policy_route_audit_fields(action_policy_route),
                'tx_hash': tx_hash,
                'is_real_trade': TradingConfig.ENABLE_TRADING
            }
            if signal_time is not None:
                trade_payload['signal_time'] = signal_time
                trade_payload['signal_to_open_seconds'] = signal_to_open_seconds
            self._log_trade_to_file(trade_payload)
            audit_payload = {
                "action": "POSITION_OPENED",
                "token": token_address,
                "symbol": symbol,
                "signal_price": signal_price,
                "entry_signal_time": signal_time,
                "entry_due_time": entry_submit_time or signal_time,
                "entry_submit_time": entry_submit_time,
                "entry_submit_seconds": entry_submit_seconds,
                "entry_wait_seconds": signal_to_open_seconds,
                "entry_fill_lag_seconds": entry_fill_lag_seconds,
                "buy_preflight_seconds": buy_preflight_seconds,
                "token_status_check_seconds": token_status_check_seconds,
                "buy_fast_status_used": buy_fast_status_used,
                "token_status_source": token_status_source,
                "lifecycle_status_staleness_seconds": lifecycle_status_staleness_seconds,
                "lifecycle_status_chain_lag_seconds": lifecycle_status_chain_lag_seconds,
                **lifecycle_price_fields,
                "buy_tx_submit_rpc_seconds": buy_tx_submit_rpc_seconds,
                "buy_token_detect_seconds": buy_token_detect_seconds,
                "buy_detect_poll_count": buy_detect_poll_count,
                "buy_confirm_poll_interval_seconds": buy_confirm_poll_interval_used,
                "buy_post_detect_sync_seconds": buy_post_detect_sync_seconds,
                "entry_price": price,
                "entry_slippage_pct": entry_slippage_pct,
                "size_bnb": actual_size_bnb,
                "prob": prob,
                "pred_return": pred_return,
                "primary_score_rescue_used": bool(primary_score_rescue_used),
                **self._action_policy_route_audit_fields(action_policy_route),
                "tx_hash": tx_hash,
                "is_real_trade": TradingConfig.ENABLE_TRADING,
            }
            if signal_time is not None:
                audit_payload["signal_time"] = signal_time
                audit_payload["signal_to_open_seconds"] = signal_to_open_seconds
            self._log_signal_audit(audit_payload)
            self._save_state()
            if (
                TradingConfig.ENABLE_TRADING
                and self.entry_price_protection_pct is not None
                and entry_slippage_pct is not None
                and entry_slippage_pct > float(self.entry_price_protection_pct)
            ):
                logger.warning(
                    "⛔ Post-fill entry slippage protection exiting %s: signal=%s entry=%s slippage=%.4f protection=%s",
                    symbol,
                    signal_price,
                    price,
                    entry_slippage_pct,
                    self.entry_price_protection_pct,
                )
                self._log_signal_audit({
                    "action": "ENTRY_PRICE_PROTECTION_POST_FILL_EXIT",
                    "token": token_address,
                    "symbol": symbol,
                    "signal_price": signal_price,
                    "entry_price": price,
                    "entry_slippage_pct": entry_slippage_pct,
                    "entry_price_protection_pct": self.entry_price_protection_pct,
                    "prob": prob,
                    "pred_return": pred_return,
                    "primary_score_rescue_used": bool(primary_score_rescue_used),
                    "tx_hash": tx_hash,
                    "is_real_trade": TradingConfig.ENABLE_TRADING,
                    **lifecycle_price_fields,
                })
                await self._close_position(token_address, reason="ENTRY_SLIPPAGE_PROTECTION")
        finally:
            self.pending_buys.remove(token_address)

    async def _partial_sell(self, token_address, sell_ratio, reason):
        """部分卖出持仓"""
        if token_address not in self.positions:
            return False
        pos = self.positions[token_address]

        # Mark attempt
        pos['last_sell_attempt'] = datetime.now()

        lifecycle = self.collector.token_lifecycle.get(token_address)
        current_price = lifecycle['price_current'] if lifecycle else pos['entry_price']
        tx_hash = None

        if TradingConfig.ENABLE_TRADING:
            async with self.trader_lock:
                logger.info(f"📉 Executing Partial Sell ({sell_ratio*100:.0f}%): {pos['symbol']} ({token_address}) | Reason: {reason}")
                try:
                    abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
                    token_contract = self.executor.w3.eth.contract(address=token_address, abi=abi)
                    token_balance = await token_contract.functions.balanceOf(self.executor.wallet_address).call()

                    if token_balance > 0:
                        # 卖出指定比例
                        sell_amount = int(token_balance * sell_ratio)
                        tx_hash = await self.executor.sell_token(token_address, sell_amount)
                    else:
                        logger.warning(f"⚠️ Token balance is 0 for {pos['symbol']}, cannot partial sell.")
                        return False
                except Exception as e:
                    logger.error(f"❌ Error in partial sell {pos['symbol']}: {e}")
                    return False

            if not tx_hash:
                logger.error(f"❌ Partial Sell Failed for {pos['symbol']}. Keeping position.")
                return False

        # 计算部分卖出的收益，纸面模式也使用实际入场价，避免追价后收益虚高。
        try:
            entry_price = pos.get('entry_price', pos.get('signal_price', 0))
            pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0.0
            sold_value = pos['size_bnb'] * sell_ratio
            gross_value = sold_value * (1 + pnl_pct)

            # Paper trading时简化计算，实盘时同步余额
            if TradingConfig.ENABLE_TRADING:
                old_balance = self.balance
                await self._sync_balance(force=True)  # 强制同步，忽略冷却
                net_return_bnb = self.balance - old_balance
                net_profit = net_return_bnb - sold_value  # 毛收入 - 成本 = 净利润
            else:
                # Paper trading: 不含滑点
                net_return_bnb = gross_value - sold_value
                net_profit = net_return_bnb  # paper trading 已经是净利润
                self.balance += gross_value

            # 更新持仓大小
            pos['size_bnb'] *= (1 - sell_ratio)

            icon = "✅" if net_profit > 0 else "❌"
            logger.info(f"{icon} PARTIAL SELL {pos['symbol']} ({sell_ratio*100:.0f}%) | Reason: {reason} | Profit: {net_profit:.4f} BNB | Bal: {self.balance:.4f} BNB")

            self._log_trade_to_file({
                'action': 'PARTIAL_SELL',
                'token': token_address,
                'symbol': pos['symbol'],
                'sell_ratio': sell_ratio,
                'entry_price': pos['entry_price'],
                'exit_price': current_price,
                'net_profit': net_profit,
                'balance': self.balance,
                'reason': reason,
                'primary_score_rescue_used': bool(pos.get('primary_score_rescue_used', False)),
                'time': datetime.now(),
                'tx_hash': tx_hash,
                'is_real_trade': TradingConfig.ENABLE_TRADING
            })
            self._log_signal_audit({
                "action": "POSITION_PARTIAL_CLOSED",
                "token": token_address,
                "symbol": pos['symbol'],
                "sell_ratio": sell_ratio,
                "signal_price": pos.get('signal_price', pos['entry_price']),
                "entry_price": pos['entry_price'],
                "exit_price": current_price,
                "net_profit": net_profit,
                "balance": self.balance,
                "reason": reason,
                "primary_score_rescue_used": bool(pos.get('primary_score_rescue_used', False)),
                "time": datetime.now(),
                "tx_hash_sell": tx_hash,
                "is_real_trade": TradingConfig.ENABLE_TRADING,
            })
            self._save_state()
            return True
        except Exception as e:
            logger.error(f"Error processing partial sell stats for {pos['symbol']}: {e}")
            return False

    async def _do_sell(self, token_address, pos, reason=None) -> object:
        """执行实际卖出操作。返回 tx_hash(成功)、None(balance=0已移除)、False(失败)"""
        logger.info(f"📉 Executing Real Sell: {pos['symbol']} ({token_address})")
        if not self._is_valid_token_address(token_address):
            logger.warning(f"⚠️ Invalid token address for {pos['symbol']}: {token_address}. Removing from bot state.")
            self.positions.pop(token_address, None)
            self.closed_tokens.add(token_address)
            self._save_state()
            return None
        try:
            abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
            token_contract = self.executor.w3.eth.contract(address=token_address, abi=abi)
            token_balance = await token_contract.functions.balanceOf(self.executor.wallet_address).call()
            if token_balance > 0:
                tx_hash = await self.executor.sell_token(token_address, token_balance)
                if not tx_hash:
                    logger.error(f"❌ Real Sell Failed for {pos['symbol']}. Keeping position (will retry).")
                    self._log_signal_audit({
                        "action": "SELL_EXECUTION_FAILED",
                        "token": token_address,
                        "symbol": pos.get("symbol"),
                        "signal_price": pos.get("signal_price", pos.get("entry_price")),
                        "entry_price": pos.get("entry_price"),
                        "primary_score_rescue_used": bool(pos.get("primary_score_rescue_used", False)),
                    })
                    return False
                return tx_hash
            else:
                logger.warning(f"⚠️ Token balance is 0 for {pos['symbol']}, removing position.")
                removed_at = datetime.now()
                entry_time = pos.get("entry_time")
                hold_duration = None
                if isinstance(entry_time, datetime):
                    hold_duration = (removed_at - entry_time).total_seconds()
                self.positions.pop(token_address, None)
                self.closed_tokens.add(token_address)
                self._log_signal_audit({
                    "action": "POSITION_ZERO_BALANCE_REMOVED",
                    "token": token_address,
                    "symbol": pos.get("symbol"),
                    "reason": reason,
                    "signal_price": pos.get("signal_price", pos.get("entry_price")),
                    "entry_price": pos.get("entry_price"),
                    "balance": self.balance,
                    "hold_duration": hold_duration,
                    "primary_score_rescue_used": bool(pos.get("primary_score_rescue_used", False)),
                    "is_real_trade": TradingConfig.ENABLE_TRADING,
                    "time": removed_at,
                })
                self._save_state()
                return None
        except Exception as e:
            logger.error(f"❌ Error selling {pos['symbol']}: {e}")
            return False

    async def _close_position(self, token_address, reason):
        if token_address not in self.positions:
             return
        # 防止并发卖出（price_sync_loop 和 analysis_loop 同时触发）
        if token_address in self._selling_tokens:
            return
        self._selling_tokens.add(token_address)
        try:
            await self._close_position_inner(token_address, reason)
        finally:
            self._selling_tokens.discard(token_address)

    async def _close_position_inner(self, token_address, reason):
        if token_address not in self.positions:
             return
        pos = self.positions[token_address]

        # Mark attempt with retry counter
        pos['last_sell_attempt'] = datetime.now()
        pos['sell_retry_count'] = pos.get('sell_retry_count', 0) + 1
        logger.info(f"🔄 卖出尝试 #{pos['sell_retry_count']} for {pos.get('symbol','?')} | reason={reason}")

        lifecycle = self.collector.token_lifecycle.get(token_address)
        current_price = lifecycle['price_current'] if lifecycle else pos['entry_price']
        sell_trigger_price = current_price
        exit_price_source = "lifecycle" if lifecycle else "entry_price_fallback"
        tx_hash = None
        sell_started_at = datetime.now()

        if TradingConfig.ENABLE_TRADING:
            # 清仓模式下跳过 trader_lock（后台任务已被取消）
            if self._shutting_down:
                tx_hash = await self._do_sell(token_address, pos, reason=reason)
            else:
                async with self.trader_lock:
                    tx_hash = await self._do_sell(token_address, pos, reason=reason)
            if tx_hash is None and token_address not in self.positions:
                return  # balance=0 已被移除
            if tx_hash is False:
                return  # 卖出失败，保留持仓
            execution_price = await self._sell_execution_price_from_receipt(token_address, tx_hash)
            if execution_price is not None:
                current_price = execution_price
                exit_price_source = "receipt"

        # Sell successful (or paper trading), remove position immediately
        if token_address in self.positions:
            self.positions.pop(token_address)
        self.closed_tokens.add(token_address)

        try:
            closed_at = datetime.now()
            old_balance = self.balance
            if TradingConfig.ENABLE_TRADING:
                await self._sync_balance(force=True)  # 强制同步，忽略冷却
                net_return_bnb = self.balance - old_balance
            else:
                # Paper trading: 使用实际入场价计算收益，和实盘成交价口径一致
                entry_price = pos.get('entry_price', pos.get('signal_price', 0))
                pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0.0
                gross_value = pos['size_bnb'] * (1 + pnl_pct)
                net_return_bnb = gross_value - pos['size_bnb']
                self.balance += gross_value

            net_profit = net_return_bnb - pos['size_bnb'] if TradingConfig.ENABLE_TRADING else net_return_bnb
            icon = "✅" if net_profit > 0 else "❌"
            logger.info(f"{icon} SELL {pos['symbol']} | Reason: {reason} | Net Profit: {net_profit:.4f} BNB | Bal: {self.balance:.4f} BNB")
            sell_execution_seconds = max(0.0, (closed_at - sell_started_at).total_seconds())
            self._log_trade_to_file({
                'action': 'CLOSE',
                'token': token_address,
                'symbol': pos['symbol'],
                'signal_price': pos.get('signal_price', pos['entry_price']),
                'entry_price': pos['entry_price'],
                'sell_trigger_price': sell_trigger_price,
                'exit_price': current_price,
                'exit_price_source': exit_price_source,
                'sell_started_at': sell_started_at,
                'sell_execution_seconds': sell_execution_seconds,
                'net_profit': net_profit,
                'balance': self.balance,
                'reason': reason,
                'time': closed_at,
                'hold_duration': (closed_at - pos['entry_time']).total_seconds(),
                'tx_hash_sell': tx_hash,
                'primary_score_rescue_used': bool(pos.get('primary_score_rescue_used', False)),
                'is_real_trade': TradingConfig.ENABLE_TRADING
            })
            self._log_signal_audit({
                "action": "POSITION_CLOSED",
                "token": token_address,
                "symbol": pos['symbol'],
                "reason": reason,
                "signal_price": pos.get('signal_price', pos['entry_price']),
                "entry_price": pos['entry_price'],
                "sell_trigger_price": sell_trigger_price,
                "exit_price": current_price,
                "exit_price_source": exit_price_source,
                "sell_started_at": sell_started_at,
                "sell_execution_seconds": sell_execution_seconds,
                "net_profit": net_profit,
                "balance": self.balance,
                "hold_duration": (closed_at - pos['entry_time']).total_seconds(),
                "tx_hash_sell": tx_hash,
                "primary_score_rescue_used": bool(pos.get('primary_score_rescue_used', False)),
                "is_real_trade": TradingConfig.ENABLE_TRADING,
            })
            self._save_state()
        except Exception as e:
            logger.error(f"Error processing post-sell stats for {pos['symbol']}: {e}")

    def _save_state(self):
        try:
            state = {
                'balance': self.balance,
                'positions': self.positions,
                'closed_tokens': sorted(self.closed_tokens),
            }
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, default=str, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _load_state(self):
        """恢复持仓状态 (余额从链上同步)"""
        if not self.state_file.exists(): return
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)

            # 只恢复持仓,不恢复余额 (余额在start()时从链上同步)
            positions = state.get('positions', {})
            self.closed_tokens = set(state.get('closed_tokens', []))
            for addr, pos in positions.items():
                if isinstance(pos.get('entry_time'), str):
                    pos['entry_time'] = datetime.fromisoformat(pos['entry_time'])
                if isinstance(pos.get('last_log_time'), str):
                    pos['last_log_time'] = datetime.fromisoformat(pos['last_log_time'])
                if isinstance(pos.get('last_sell_attempt'), str):
                    pos['last_sell_attempt'] = datetime.fromisoformat(pos['last_sell_attempt'])
                # 兼容老状态：若无止盈参考锚点，默认用实际成交价
                if 'tp_base_price' not in pos:
                    pos['tp_base_price'] = pos.get('entry_price', 0)
                if 'peak_price' not in pos:
                    pos['peak_price'] = pos.get('tp_base_price', pos.get('entry_price', 0))
                if 'initial_size_bnb' not in pos:
                    pos['initial_size_bnb'] = pos.get('size_bnb', 0)
            self.positions = positions

            if self.positions:
                logger.info(f"📂 Loaded {len(self.positions)} positions from saved state")

        except Exception as e:
            logger.error(f"Failed to load state: {e}")

    async def sell_all_positions(self, timeout: int = 45):
        """清仓所有持仓，带总超时保护"""
        self.active = False
        self._shutting_down = True  # 标记清仓模式，_close_position 跳过 trader_lock

        # 显式取消所有后台任务，避免它们持有 trader_lock
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
        # 等待后台任务真正结束（最多2秒）
        if self._background_tasks:
            await asyncio.wait(self._background_tasks, timeout=2)
        self._background_tasks.clear()

        if not self.positions:
            logger.info("📭 No open positions, clean exit.")
            return

        # 打印所有持仓明细
        logger.warning(f"🚨 EMERGENCY LIQUIDATION: Selling {len(self.positions)} positions!")
        for addr, pos in self.positions.items():
            lifecycle = self.collector.token_lifecycle.get(addr)
            current_price = lifecycle['price_current'] if lifecycle else pos.get('entry_price', 0)
            entry_price = float(pos.get('entry_price', pos.get('signal_price', 0)) or 0)
            pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            held = (datetime.now() - pos['entry_time']).total_seconds()
            icon = "📦"
            logger.warning(f"  {icon} {pos.get('symbol','?')} | Size: {pos.get('size_bnb',0):.4f} BNB | PnL: {pnl_pct:+.1%} | Held: {held:.0f}s | {addr}")

        per_token_timeout = max(12, timeout // max(len(self.positions), 1))

        async def _safe_close(token):
            try:
                await asyncio.wait_for(
                    self._close_position(token, reason="APP_STOP_LIQUIDATION"),
                    timeout=per_token_timeout
                )
            except asyncio.TimeoutError:
                logger.error(f"⏰ Sell timeout for {token}, skipping")
            except Exception as e:
                logger.error(f"❌ Sell error for {token}: {e}")

        try:
            tasks = [_safe_close(token) for token in list(self.positions.keys())]
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"⏰ Total cleanup timeout ({timeout}s), saving state and exiting")

        # 汇报清仓结果
        if self.positions:
            logger.warning(f"⚠️ {len(self.positions)} positions NOT sold (will retry on next restart):")
            for addr, pos in self.positions.items():
                logger.warning(f"  🔴 {pos.get('symbol','?')} | {pos.get('size_bnb',0):.4f} BNB | {addr}")
        else:
            logger.info("✅ All positions liquidated successfully.")

    async def _sync_positions_with_chain(self):
        """Sync local state positions with actual on-chain wallet balances"""
        if not TradingConfig.ENABLE_TRADING or not self.positions:
            return

        logger.info("🔄 Syncing positions with on-chain data...")
        to_remove = []
        abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]

        for token_address, pos in self.positions.items():
            try:
                if not self._is_valid_token_address(token_address):
                    logger.warning(f"⚠️ Invalid restored token address for {pos['symbol']}: {token_address}. Removing from bot state.")
                    to_remove.append(token_address)
                    continue
                token_contract = self.executor.w3.eth.contract(address=token_address, abi=abi)
                balance = await token_contract.functions.balanceOf(self.executor.wallet_address).call()

                if balance == 0:
                    logger.warning(f"⚠️ Inconsistent State: {pos['symbol']} balance is 0. Removing from bot state.")
                    to_remove.append(token_address)
                else:
                    logger.info(f"✅ Verified Position: {pos['symbol']} | Balance: {balance}")
            except Exception as e:
                logger.error(f"❌ Failed to verify position {pos['symbol']}: {e}")

        if to_remove:
            for token in to_remove:
                self.positions.pop(token)
                self.closed_tokens.add(token)
            self._save_state()
            logger.info(f"🧹 Removed {len(to_remove)} invalid positions.")

    def _ensure_lifecycle(self, token_address: str):
        """确保恢复的持仓在 collector 中有 lifecycle 条目（重启后需要）"""
        if token_address in self.collector.token_lifecycle:
            return
        pos = self.positions.get(token_address)
        if not pos:
            return
        self.collector.token_lifecycle[token_address] = {
            'token_address': token_address,
            'creator': '',
            'name': pos.get('symbol', 'UNKNOWN'),
            'symbol': pos.get('symbol', 'UNKNOWN'),
            'total_supply': 0.0,
            'launch_fee': 0.0,
            'launch_time': 0,
            'create_timestamp': pos['entry_time'].timestamp(),
            'create_block': 0,
            'price_current': pos.get('entry_price', 0),
            'price_current_source': 'restored_state',
            'price_current_local_update': datetime.now().timestamp(),
            'price_first': pos.get('entry_price', 0),
            'price_max': pos.get('entry_price', 0),
            'price_min': pos.get('entry_price', 0),
            'last_update': datetime.now().timestamp(),
            'buys': [], 'sells': [], 'price_history': [],
            'total_buy_volume_bnb': 0, 'total_sell_volume_bnb': 0,
            'total_buy_count': 0, 'total_sell_count': 0,
            'unique_buyers': set(), 'unique_sellers': set(),
        }
        logger.info(f"📂 Created lifecycle stub for restored position: {pos.get('symbol')}")

    def _normalize_helper_price(self, raw_price: float, reference_price: float = 0.0) -> float:
        """归一化 Helper 返回的 lastPrice，兼容不同精度缩放。"""
        if raw_price <= 0:
            return 0.0

        candidates = [raw_price, raw_price / 1e9, raw_price / 1e18]
        candidates = [c for c in candidates if c > 0]

        # 优先用参考价选择最接近的缩放结果，避免误除以 1e9/1e18
        if reference_price and reference_price > 0:
            return min(candidates, key=lambda c: abs(np.log10(c / reference_price)))

        # 无参考价时按数值量级兜底
        if raw_price > 1e12:
            return raw_price / 1e18
        if raw_price > 1e3:
            return raw_price / 1e9
        return raw_price

    async def _collector_loop(self):
        """批量消费 listener 事件队列，更新 collector 并触发逐笔分析事件。"""
        logger.info("📥 Collector loop started")
        if self._collector_event_queue is None:
            self._collector_event_queue = asyncio.Queue(maxsize=self.collector_event_queue_size)

        while self.active:
            try:
                event_name, event_data = await asyncio.wait_for(
                    self._collector_event_queue.get(),
                    timeout=self.collector_loop_sleep
                )
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Collector loop wait error: {e}")
                await asyncio.sleep(self.collector_loop_sleep)
                continue

            try:
                batch = [(event_name, event_data)]
                for _ in range(self.collector_batch_size - 1):
                    try:
                        batch.append(self._collector_event_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                for evt_name, evt_data in batch:
                    try:
                        if evt_name == 'TokenCreate':
                            self.collector.on_token_create(evt_data)
                        elif 'Purchase' in evt_name:
                            self.collector.on_token_purchase(evt_data)
                        elif 'Sale' in evt_name:
                            self.collector.on_token_sale(evt_data)
                        elif evt_name == 'TradeStop':
                            self.collector.on_trade_stop(evt_data)

                        self.collector_events_processed += 1
                        token = evt_data.get('args', {}).get('token')
                        if token:
                            await self._enqueue_analysis_token(token)
                    except Exception as e:
                        logger.error(f"Collector batch event error {evt_name}: {e}")

                await asyncio.sleep(0)
            except Exception as e:
                logger.error(f"Collector loop process error: {e}")

    async def _collector_flush_loop(self):
        """周期性刷盘不活跃生命周期，控制长期运行内存增长。"""
        logger.info("🧹 Collector flush loop started")
        while self.active:
            try:
                await asyncio.sleep(self.collector_flush_interval_seconds)
                if not self.active:
                    break

                now = int(datetime.now().timestamp())
                flushed = self.collector.flush_eligible_tokens(
                    current_time=now,
                    min_age_seconds=self.collector_flush_min_age_seconds,
                    inactivity_seconds=self.collector_flush_inactivity_seconds,
                )
                if flushed > 0:
                    stats = self.collector.get_stats()
                    logger.info(
                        f"🧹 Collector flush: flushed={flushed} | "
                        f"in_memory={stats.get('tokens_in_memory')} | "
                        f"total_flushed={stats.get('tokens_flushed')}"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Collector flush loop error: {e}")

    async def _analysis_loop(self):
        """后台循环：逐笔消费分析事件并执行ML分析。"""
        logger.info("🔬 Analysis loop started")
        while self.active:
            try:
                token = await self._analysis_event_queue.get()
                if hasattr(self, "_queued_analysis_tokens"):
                    self._queued_analysis_tokens.discard(token)
                try:
                    await self._process_token_logic(token)
                except Exception as e:
                    logger.error(f"Analysis error: {e}")
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in analysis loop: {e}")

    async def _price_sync_loop(self):
        """Background task to sync prices via RPC (Ensure PnL accuracy)"""
        logger.info("🔄 Price sync loop started")
        while self.active:
            try:
                if self.positions:
                    tokens = list(self.positions.keys())

                    # 确保恢复的持仓有 lifecycle（重启后首次需要）
                    for token in tokens:
                        self._ensure_lifecycle(token)

                    tasks = [self.executor.check_token_status(t) for t in tokens]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for token, status in zip(tokens, results):
                        if isinstance(status, dict) and status.get('price', 0) > 0:
                            if token in self.collector.token_lifecycle:
                                raw_price = float(status['price'])
                                existing_price = self.collector.token_lifecycle[token].get('price_current', 0)
                                pos = self.positions.get(token)
                                tp_base_price = pos.get('tp_base_price', pos.get('entry_price', 0)) if pos else 0
                                reference_price = existing_price if existing_price > 0 else tp_base_price

                                normalized_price = self._normalize_helper_price(raw_price, reference_price)

                                if normalized_price > 0:
                                    # meme币高波动下允许大幅跳变，只对极端数量级变化做日志提示
                                    if existing_price > 0:
                                        ratio = normalized_price / existing_price
                                        if ratio < 1e-6 or ratio > 1e6:
                                            logger.warning(
                                                f"⚠️ Extreme price jump from helper: {token[:10]}... "
                                                f"raw={raw_price:.6g}, normalized={normalized_price:.10g}, "
                                                f"prev={existing_price:.10g}, ratio={ratio:.2g}"
                                            )
                                    self.collector.token_lifecycle[token]['price_current'] = normalized_price
                                    self.collector.token_lifecycle[token]['price_current_source'] = 'helper'
                                    self.collector.token_lifecycle[token]['price_current_local_update'] = datetime.now().timestamp()
                        elif isinstance(status, Exception):
                            pass

                        # 无论是否拿到最新价格，都执行持仓逻辑（保证 TIME_EXIT 不会卡住）
                        await self._process_token_logic(token)

            except Exception as e:
                logger.error(f"Error in price sync loop: {e}")

            await asyncio.sleep(1) # 1s refresh rate

    async def start(self):
        logger.info(f"🤖 Starting MemeBot")

        # 同步链上余额
        await self._sync_balance()

        # 验证持仓
        await self._sync_positions_with_chain()

        # 显示启动信息
        logger.info(f"💰 Balance: {self.balance:.4f} BNB | Positions: {len(self.positions)}")
        logger.info(
            "📊 Strategy: Prob >= %s, Stop Loss: %s%%, Hold Time: %ss, Min Policy Hold: %ss",
            self.prob_threshold,
            self.stop_loss * 100,
            self.hold_time_seconds,
            self.min_policy_hold_seconds,
        )
        logger.info(
            "📌 Strategy source: "
            f"prob={self.strategy_param_sources.get('prob_threshold', 'default')}, "
            f"pred_return={self.strategy_param_sources.get('min_pred_return', 'default')}, "
            f"age={self.strategy_param_sources.get('max_age_seconds', 'default')}"
        )

        # 启动后台循环（保存引用以便 shutdown 时取消）
        self._background_tasks.append(asyncio.create_task(self._collector_loop()))
        self._background_tasks.append(asyncio.create_task(self._collector_flush_loop()))
        self._background_tasks.append(asyncio.create_task(self._analysis_loop()))
        self._background_tasks.append(asyncio.create_task(self._buy_worker_loop()))
        self._background_tasks.append(asyncio.create_task(self._price_sync_loop()))

        # 订阅事件
        await self.listener.subscribe_to_events()


async def _disconnect_w3_provider(w3) -> None:
    provider = getattr(w3, "provider", None)
    disconnect = getattr(provider, "disconnect", None)
    if disconnect is None:
        return
    result = disconnect()
    if asyncio.iscoroutine(result):
        await result


async def _cleanup_bot_runtime(bot, ws_manager=None, *, sell_timeout: int = 35, cleanup_timeout: int = 40):
    """Close trading state and network providers during runtime shutdown."""
    logger.info(f"🧹 Cleaning up ({cleanup_timeout}s timeout)...")
    try:
        await asyncio.wait_for(bot.sell_all_positions(timeout=sell_timeout), timeout=cleanup_timeout)
    except (asyncio.TimeoutError, Exception) as e:
        logger.error(f"⚠️ Cleanup incomplete: {e}")

    bot._save_state()

    close_log_providers = getattr(getattr(bot, "listener", None), "close_log_providers", None)
    if close_log_providers is not None:
        try:
            await close_log_providers()
        except Exception as exc:
            logger.debug(f"Failed to close listener HTTP providers: {exc}")

    executor_close = getattr(getattr(bot, "executor", None), "close", None)
    if executor_close is not None:
        try:
            await executor_close()
        except Exception as exc:
            logger.debug(f"Failed to close trade executor provider: {exc}")

    if ws_manager is None:
        try:
            await _disconnect_w3_provider(getattr(bot, "w3", None))
        except Exception as exc:
            logger.debug(f"Failed to close main web3 provider: {exc}")

    if ws_manager:
        try:
            await ws_manager.disconnect()
        except Exception:
            pass

    logger.info("✅ Cleanup complete")


def _model_manifest_manual_override_keys() -> set:
    env_to_config_key = {
        'BUY_NEAR_THRESHOLD_MIN_PROB': 'buy_near_threshold_min_prob',
        'BUY_NEAR_MIN_PRED_RETURN': 'buy_near_min_pred_return',
        'BUY_NEAR_MIN_ENTRY_VOLUME_30S': 'buy_near_min_entry_volume_30s',
        'BUY_NEAR_MIN_ENTRY_PRICE_VOLATILITY': 'buy_near_min_entry_price_volatility',
        'BUY_NEAR_MIN_AGE_SECONDS': 'buy_near_min_age_seconds',
        'BUY_PRIMARY_SCORE_RESCUE_MIN_PROB': 'buy_primary_score_rescue_min_prob',
        'BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN': 'buy_primary_score_rescue_min_pred_return',
        'BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S': 'buy_primary_score_rescue_min_entry_volume_30s',
        'BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY': 'buy_primary_score_rescue_min_entry_price_volatility',
        'BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS': 'buy_primary_score_rescue_min_age_seconds',
        'MAX_CONCURRENT_POSITIONS': 'max_concurrent_positions',
    }
    return {
        config_key
        for env_key, config_key in env_to_config_key.items()
        if os.getenv(env_key, '').strip()
    }


def _validate_live_trading_config():
    from config.config import Config

    Config.validate_rpc_config()
    TradingConfig.validate()


if __name__ == "__main__":
    from web3 import AsyncWeb3
    from web3.providers.rpc import AsyncHTTPProvider
    from dotenv import load_dotenv
    from config.config import Config
    load_dotenv()
    _validate_live_trading_config()

    async def main():
        ws_manager = None
        listener_mode = Config.get_listener_mode()

        if listener_mode != 'http_only':
            ws_url = Config.get_listener_ws_url()
            ws_manager = WSConnectionManager(ws_url)
            if not await ws_manager.connect():
                return
            w3 = ws_manager.get_web3()
        else:
            log_http_endpoints = Config.get_log_http_pool()
            w3 = AsyncWeb3(
                AsyncHTTPProvider(
                    log_http_endpoints[0],
                    request_kwargs=Config.get_http_request_kwargs(),
                )
            )
            logger.warning(f"⚠️ Running bot in http_only mode via {log_http_endpoints[0]}")

        log_http_endpoints = Config.get_log_http_pool()
        contract_config = Config.get_contract_config()
        config = {
            'w3': w3, 'ws_manager': ws_manager,
            'contract_address': "0x5c952063c7fc8610FFDB798152D69F0B9550762b",
            'contract_abi': Config._load_contract_abi(),
            'log_http_endpoints': log_http_endpoints,
            'max_lag_skip_blocks': contract_config.get('max_lag_skip_blocks', 0),
            'lag_skip_keep_recent_blocks': contract_config.get('lag_skip_keep_recent_blocks', 200),
            'log_provider_cooldown_seconds': contract_config.get('log_provider_cooldown_seconds', 45.0),
            'listener_poll_interval_seconds': contract_config.get('listener_poll_interval_seconds', 0.5),
            'model_dir': _runtime_model_dir(), 'initial_balance': 10.0,
            'model_manifest_authoritative': True,
            'model_manifest_manual_override_keys': _model_manifest_manual_override_keys(),
            'min_entry_unique_buyers': TradingConfig.MIN_ENTRY_UNIQUE_BUYERS,
            'min_entry_buy_count': TradingConfig.MIN_ENTRY_BUY_COUNT,
            'min_entry_volume_30s': TradingConfig.MIN_ENTRY_VOLUME_30S,
            'min_entry_price_volatility': TradingConfig.MIN_ENTRY_PRICE_VOLATILITY,
            'buy_near_threshold_min_prob': TradingConfig.BUY_NEAR_THRESHOLD_MIN_PROB,
            'buy_near_min_pred_return': TradingConfig.BUY_NEAR_MIN_PRED_RETURN,
            'buy_near_min_entry_volume_30s': TradingConfig.BUY_NEAR_MIN_ENTRY_VOLUME_30S,
            'buy_near_min_entry_price_volatility': TradingConfig.BUY_NEAR_MIN_ENTRY_PRICE_VOLATILITY,
            'buy_near_min_age_seconds': TradingConfig.BUY_NEAR_MIN_AGE_SECONDS,
            'buy_primary_score_rescue_min_prob': TradingConfig.BUY_PRIMARY_SCORE_RESCUE_MIN_PROB,
            'buy_primary_score_rescue_min_pred_return': TradingConfig.BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN,
            'buy_primary_score_rescue_min_entry_volume_30s': TradingConfig.BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S,
            'buy_primary_score_rescue_min_entry_price_volatility': TradingConfig.BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY,
            'buy_primary_score_rescue_min_age_seconds': TradingConfig.BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS,
            'buy_action_policy_router_enabled': TradingConfig.BUY_ACTION_POLICY_ROUTER_ENABLED,
            'buy_action_policy_router_train_rejected_reports': TradingConfig.BUY_ACTION_POLICY_ROUTER_TRAIN_REJECTED_REPORTS,
            'buy_action_policy_router_train_accepted_reports': TradingConfig.BUY_ACTION_POLICY_ROUTER_TRAIN_ACCEPTED_REPORTS,
            'buy_action_policy_router_min_confidence': TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_CONFIDENCE,
            'buy_action_policy_router_max_depth': TradingConfig.BUY_ACTION_POLICY_ROUTER_MAX_DEPTH,
            'buy_action_policy_router_min_samples_leaf': TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_SAMPLES_LEAF,
            'buy_action_policy_router_min_common_features': TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_COMMON_FEATURES,
            'buy_action_policy_router_min_live_features': TradingConfig.BUY_ACTION_POLICY_ROUTER_MIN_LIVE_FEATURES,
            'buy_action_policy_continue_hold_activation_pct': TradingConfig.BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT,
            'buy_action_policy_continue_hold_release_pct': TradingConfig.BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT,
            'runner_reserve_enabled': TradingConfig.RUNNER_RESERVE_ENABLED,
            'runner_reserve_activation_multiple': TradingConfig.RUNNER_RESERVE_ACTIVATION_MULTIPLE,
            'runner_reserve_partial_exit_ratio': TradingConfig.RUNNER_RESERVE_PARTIAL_EXIT_RATIO,
            'runner_reserve_stop_drawdown': TradingConfig.RUNNER_RESERVE_STOP_DRAWDOWN,
            'runner_reserve_floor_return': TradingConfig.RUNNER_RESERVE_FLOOR_RETURN,
            'runner_reserve_max_hold_seconds': TradingConfig.RUNNER_RESERVE_MAX_HOLD_SECONDS,
            'runner_reserve_max_sell_pressure_30s': TradingConfig.RUNNER_RESERVE_MAX_SELL_PRESSURE_30S,
            'runner_reserve_min_flow_event_count_30s': TradingConfig.RUNNER_RESERVE_MIN_FLOW_EVENT_COUNT_30S,
            'post_graduation_retention_enabled': TradingConfig.POST_GRADUATION_RETENTION_ENABLED,
            'post_graduation_retention_shadow_enabled': TradingConfig.POST_GRADUATION_RETENTION_SHADOW_ENABLED,
            'post_graduation_retention_max_hold_seconds': TradingConfig.POST_GRADUATION_RETENTION_MAX_HOLD_SECONDS,
            'post_graduation_retention_max_data_age_seconds': TradingConfig.POST_GRADUATION_RETENTION_MAX_DATA_AGE_SECONDS,
            'post_graduation_retention_min_liquidity_usd': TradingConfig.POST_GRADUATION_RETENTION_MIN_LIQUIDITY_USD,
            'post_graduation_retention_min_volume_liquidity_ratio_5m': TradingConfig.POST_GRADUATION_RETENTION_MIN_VOLUME_LIQUIDITY_RATIO_5M,
            'post_graduation_retention_min_new_traders_1h': TradingConfig.POST_GRADUATION_RETENTION_MIN_NEW_TRADERS_1H,
            'post_graduation_retention_max_sell_pressure_5m': TradingConfig.POST_GRADUATION_RETENTION_MAX_SELL_PRESSURE_5M,
            'post_graduation_retention_max_drawdown': TradingConfig.POST_GRADUATION_RETENTION_MAX_DRAWDOWN,
            'max_concurrent_positions': TradingConfig.MAX_CONCURRENT_POSITIONS,
            'position_size': TradingConfig.POSITION_SIZE,
            'fixed_stake_bnb': TradingConfig.FIXED_STAKE_BNB,
            'max_entry_size_bnb': TradingConfig.MAX_ENTRY_SIZE_BNB,
            # 可选手动覆盖：'prob_threshold' / 'min_pred_return' / 'max_age_seconds' / 'entry_ranking_mode'
            # 可选手动覆盖：'stop_loss' / 'hold_time_seconds' / 'min_policy_hold_seconds'
            # 可选手动覆盖：'position_size' / 'max_entry_size_bnb' / 'trailing_start_pct' / 'trailing_stop_pct' / 'rug_sell_pressure'
            # 可选过滤开关：'use_pred_return_filter' (True/False)
            # 默认关闭的动作策略路由：'buy_action_policy_router_enabled' 及相关 train report 路径
        }
        bot = MemeBot(config)
        try:
            await bot.start()
        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("🛑 Bot stopped by user (Ctrl+C)")
        finally:
            await _cleanup_bot_runtime(bot, ws_manager=ws_manager)

    import signal
    def _sigterm_handler(signum, frame):
        """将 SIGTERM 转为 KeyboardInterrupt，触发 asyncio cleanup"""
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Exit confirmed")

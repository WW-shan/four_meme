"""
Trading Configuration
加载和管理交易相关配置参数
"""

import os
import math
from dotenv import load_dotenv

load_dotenv()


def _optional_nonnegative_float_env(name: str):
    raw = os.getenv(name, '').strip()
    if not raw:
        return None
    return max(0.0, float(raw))


def _optional_float_env(name: str):
    raw = os.getenv(name, '').strip()
    if not raw:
        return None
    return float(raw)


def _optional_probability_env(name: str):
    raw = os.getenv(name, '').strip()
    if not raw:
        return None
    return float(raw)


def _float_env(name: str, default: float):
    raw = os.getenv(name, '').strip()
    if not raw:
        return float(default)
    return float(raw)


def _int_env(name: str, default: int):
    raw = os.getenv(name, '').strip()
    if not raw:
        return int(default)
    return int(raw)


def _path_list_env(name: str):
    raw = os.getenv(name, '').strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(',') if item.strip()]


class TradingConfig:
    """交易配置"""

    # ========== 钱包配置 ==========
    PRIVATE_KEY = os.getenv('PRIVATE_KEY', '')

    # ========== 链 ==========
    # The executor signs raw transactions, so the chain id must be explicit instead of
    # baked into every build_transaction call. Defaults to BSC.
    CHAIN_ID = int(os.getenv('MEME_CHAIN_ID', '56'))

    # ========== 交易开关 ==========
    ENABLE_TRADING = os.getenv('ENABLE_TRADING', 'false').lower() == 'true'
    ENABLE_BACKTEST = os.getenv('ENABLE_BACKTEST', 'false').lower() == 'true'

    # ========== 买入策略 ==========
    BASE_GAS_PRICE_GWEI = float(os.getenv('BASE_GAS_PRICE_GWEI', '0.08'))  # BSC标准gas (0.08 Gwei)
    MAX_GAS_PRICE_GWEI = float(os.getenv('MAX_GAS_PRICE_GWEI', '0.1'))     # 最高不超过0.1 Gwei
    GAS_MULTIPLIER = float(os.getenv('GAS_MULTIPLIER', '1.1'))  # 稍微加价10%
    BUY_SLIPPAGE_PERCENT = int(os.getenv('BUY_SLIPPAGE_PERCENT', '15'))
    SELL_SLIPPAGE_PERCENT = int(os.getenv('SELL_SLIPPAGE_PERCENT', '15'))
    FOURMEME_TOKEN_DECIMALS = int(os.getenv('FOURMEME_TOKEN_DECIMALS', '18'))
    # Only used when no expected price is available; the live buy path always
    # passes a verified price and therefore computes a real minimum instead.
    BUY_MIN_AMOUNT_FLOOR = int(os.getenv('BUY_MIN_AMOUNT_FLOOR', '1'))
    BUY_CONFIRM_POLL_INTERVAL_SECONDS = float(os.getenv('BUY_CONFIRM_POLL_INTERVAL_SECONDS', '0.25'))
    BUY_CONFIRM_TIMEOUT_SECONDS = int(os.getenv('BUY_CONFIRM_TIMEOUT_SECONDS', '120'))
    BUY_USE_LIFECYCLE_FAST_STATUS = os.getenv('BUY_USE_LIFECYCLE_FAST_STATUS', 'true').lower() == 'true'
    BUY_FAST_STATUS_MAX_STALENESS_SECONDS = float(os.getenv('BUY_FAST_STATUS_MAX_STALENESS_SECONDS', '3'))
    BUY_FAST_STATUS_MAX_CHAIN_LAG_SECONDS = float(os.getenv('BUY_FAST_STATUS_MAX_CHAIN_LAG_SECONDS', '8'))
    TX_RECEIPT_POLL_LATENCY_SECONDS = float(os.getenv('TX_RECEIPT_POLL_LATENCY_SECONDS', '0.25'))

    # ========== 卖出策略 (第一阶段) ==========
    TAKE_PROFIT_PERCENT = int(os.getenv('TAKE_PROFIT_PERCENT', '200'))
    TAKE_PROFIT_SELL_PERCENT = int(os.getenv('TAKE_PROFIT_SELL_PERCENT', '90'))
    STOP_LOSS_PERCENT = int(os.getenv('STOP_LOSS_PERCENT', '-50'))
    MAX_HOLD_TIME_SECONDS = int(os.getenv('MAX_HOLD_TIME_SECONDS', '300'))

    # ========== 卖出策略 (第二阶段 - 底仓) ==========
    KEEP_POSITION_FOR_MOONSHOT = os.getenv('KEEP_POSITION_FOR_MOONSHOT', 'true').lower() == 'true'
    MOONSHOT_PROFIT_PERCENT = int(os.getenv('MOONSHOT_PROFIT_PERCENT', '500'))
    MOONSHOT_STOP_LOSS_PERCENT = int(os.getenv('MOONSHOT_STOP_LOSS_PERCENT', '-30'))
    MOONSHOT_MAX_HOLD_HOURS = int(os.getenv('MOONSHOT_MAX_HOLD_HOURS', '24'))

    # ========== Runner reserve (explicit opt-in, default disabled) ==========
    RUNNER_RESERVE_ENABLED = os.getenv('RUNNER_RESERVE_ENABLED', 'false').lower() == 'true'
    RUNNER_RESERVE_ACTIVATION_MULTIPLE = _float_env('RUNNER_RESERVE_ACTIVATION_MULTIPLE', 2.0)
    RUNNER_RESERVE_PARTIAL_EXIT_RATIO = _float_env('RUNNER_RESERVE_PARTIAL_EXIT_RATIO', 0.70)
    RUNNER_RESERVE_STOP_DRAWDOWN = _float_env('RUNNER_RESERVE_STOP_DRAWDOWN', 0.30)
    RUNNER_RESERVE_FLOOR_RETURN = _float_env('RUNNER_RESERVE_FLOOR_RETURN', 0.0)
    RUNNER_RESERVE_MAX_HOLD_SECONDS = _int_env('RUNNER_RESERVE_MAX_HOLD_SECONDS', 86400)
    RUNNER_RESERVE_MAX_SELL_PRESSURE_30S = _float_env('RUNNER_RESERVE_MAX_SELL_PRESSURE_30S', 0.55)
    RUNNER_RESERVE_MIN_FLOW_EVENT_COUNT_30S = _int_env('RUNNER_RESERVE_MIN_FLOW_EVENT_COUNT_30S', 1)

    # ========== Post-graduation retention health (explicit opt-in) ==========
    # Shadow mode evaluates/logs DEX health but never changes a position.
    POST_GRADUATION_RETENTION_ENABLED = os.getenv('POST_GRADUATION_RETENTION_ENABLED', 'false').lower() == 'true'
    POST_GRADUATION_RETENTION_SHADOW_ENABLED = (
        os.getenv('POST_GRADUATION_RETENTION_SHADOW_ENABLED', 'false').lower() == 'true'
    )
    POST_GRADUATION_RETENTION_MAX_HOLD_SECONDS = _int_env(
        'POST_GRADUATION_RETENTION_MAX_HOLD_SECONDS', 4 * 86400
    )
    POST_GRADUATION_RETENTION_MAX_DATA_AGE_SECONDS = _float_env(
        'POST_GRADUATION_RETENTION_MAX_DATA_AGE_SECONDS', 120.0
    )
    POST_GRADUATION_RETENTION_MIN_LIQUIDITY_USD = _float_env(
        'POST_GRADUATION_RETENTION_MIN_LIQUIDITY_USD', 10_000.0
    )
    POST_GRADUATION_RETENTION_MIN_VOLUME_LIQUIDITY_RATIO_5M = _float_env(
        'POST_GRADUATION_RETENTION_MIN_VOLUME_LIQUIDITY_RATIO_5M', 0.20
    )
    POST_GRADUATION_RETENTION_MIN_NEW_TRADERS_1H = _float_env(
        'POST_GRADUATION_RETENTION_MIN_NEW_TRADERS_1H', 2.0
    )
    POST_GRADUATION_RETENTION_MAX_SELL_PRESSURE_5M = _float_env(
        'POST_GRADUATION_RETENTION_MAX_SELL_PRESSURE_5M', 0.60
    )
    POST_GRADUATION_RETENTION_MAX_DRAWDOWN = _float_env(
        'POST_GRADUATION_RETENTION_MAX_DRAWDOWN', 0.35
    )

    # ========== 风控参数 ==========
    MAX_DAILY_TRADES = int(os.getenv('MAX_DAILY_TRADES', '100'))
    MAX_DAILY_INVESTMENT_BNB = float(os.getenv('MAX_DAILY_INVESTMENT_BNB', '0.5'))
    MAX_CONCURRENT_POSITIONS = int(os.getenv('MAX_CONCURRENT_POSITIONS', '0'))
    POSITION_SIZE = max(0.0, float(os.getenv('POSITION_SIZE', '0.10')))
    FIXED_STAKE_BNB = _optional_nonnegative_float_env('FIXED_STAKE_BNB')
    MAX_ENTRY_SIZE_BNB = _optional_nonnegative_float_env('MAX_ENTRY_SIZE_BNB')

    # ========== 过滤条件 ==========
    FILTER_KEYWORDS_BLACKLIST = os.getenv('FILTER_KEYWORDS_BLACKLIST', 'scam,rug,test,dev,burn,locked,free,airdrop').split(',')
    FILTER_MIN_INITIAL_LIQUIDITY = float(os.getenv('FILTER_MIN_INITIAL_LIQUIDITY', '0.01'))
    MIN_ENTRY_UNIQUE_BUYERS = int(os.getenv('MIN_ENTRY_UNIQUE_BUYERS', '3'))
    MIN_ENTRY_BUY_COUNT = int(os.getenv('MIN_ENTRY_BUY_COUNT', '5'))
    MIN_ENTRY_VOLUME_30S = float(os.getenv('MIN_ENTRY_VOLUME_30S', '0'))
    MIN_ENTRY_PRICE_VOLATILITY = float(os.getenv('MIN_ENTRY_PRICE_VOLATILITY', '0'))
    BUY_NEAR_THRESHOLD_MIN_PROB = _optional_float_env('BUY_NEAR_THRESHOLD_MIN_PROB')
    BUY_NEAR_MIN_PRED_RETURN = _optional_float_env('BUY_NEAR_MIN_PRED_RETURN')
    BUY_NEAR_MIN_ENTRY_VOLUME_30S = _optional_float_env('BUY_NEAR_MIN_ENTRY_VOLUME_30S')
    BUY_NEAR_MIN_ENTRY_PRICE_VOLATILITY = _optional_float_env('BUY_NEAR_MIN_ENTRY_PRICE_VOLATILITY')
    BUY_NEAR_MIN_AGE_SECONDS = _optional_float_env('BUY_NEAR_MIN_AGE_SECONDS')
    BUY_PRIMARY_SCORE_RESCUE_MIN_PROB = _optional_probability_env('BUY_PRIMARY_SCORE_RESCUE_MIN_PROB')
    BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN = _optional_float_env('BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN')
    BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S = _optional_float_env('BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S')
    BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY = _optional_float_env('BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY')
    BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS = _optional_float_env('BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS')

    # Default-off action-policy router. This only makes a replay-accepted
    # continue-hold route available to runtime; enabling still requires reviewed
    # train report paths and a separate live switch.
    BUY_ACTION_POLICY_ROUTER_ENABLED = os.getenv('BUY_ACTION_POLICY_ROUTER_ENABLED', 'false').lower() == 'true'
    BUY_ACTION_POLICY_ROUTER_SHADOW_AUDIT_ENABLED = (
        os.getenv('BUY_ACTION_POLICY_ROUTER_SHADOW_AUDIT_ENABLED', 'false').lower() == 'true'
    )
    BUY_ACTION_POLICY_ROUTER_TRAIN_REJECTED_REPORTS = _path_list_env('BUY_ACTION_POLICY_ROUTER_TRAIN_REJECTED_REPORTS')
    BUY_ACTION_POLICY_ROUTER_TRAIN_ACCEPTED_REPORTS = _path_list_env('BUY_ACTION_POLICY_ROUTER_TRAIN_ACCEPTED_REPORTS')
    BUY_ACTION_POLICY_ROUTER_MIN_CONFIDENCE = _float_env('BUY_ACTION_POLICY_ROUTER_MIN_CONFIDENCE', 0.40)
    BUY_ACTION_POLICY_ROUTER_MAX_DEPTH = _int_env('BUY_ACTION_POLICY_ROUTER_MAX_DEPTH', 3)
    BUY_ACTION_POLICY_ROUTER_MIN_SAMPLES_LEAF = _int_env('BUY_ACTION_POLICY_ROUTER_MIN_SAMPLES_LEAF', 10)
    BUY_ACTION_POLICY_ROUTER_MIN_COMMON_FEATURES = _int_env('BUY_ACTION_POLICY_ROUTER_MIN_COMMON_FEATURES', 2)
    BUY_ACTION_POLICY_ROUTER_MIN_LIVE_FEATURES = _int_env('BUY_ACTION_POLICY_ROUTER_MIN_LIVE_FEATURES', 2)
    BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT = _float_env('BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT', 0.35)
    BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT = _float_env('BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT', 0.75)

    # ========== 热度追踪 ==========
    FILTER_ENABLE_TREND_TRACKING = os.getenv('FILTER_ENABLE_TREND_TRACKING', 'true').lower() == 'true'
    FILTER_TREND_WINDOW_MINUTES = int(os.getenv('FILTER_TREND_WINDOW_MINUTES', '5'))
    FILTER_TREND_THRESHOLD = int(os.getenv('FILTER_TREND_THRESHOLD', '3'))
    FILTER_TREND_PREFIX_LENGTH = int(os.getenv('FILTER_TREND_PREFIX_LENGTH', '4'))
    FILTER_CLUSTER_BUY_AMOUNT_BNB = float(os.getenv('FILTER_CLUSTER_BUY_AMOUNT_BNB', '0.015'))

    # ========== 地址过滤 ==========
    FILTER_ENABLE_ADDRESS_CHECK = os.getenv('FILTER_ENABLE_ADDRESS_CHECK', 'true').lower() == 'true'
    FILTER_MAX_TOKENS_PER_CREATOR_24H = int(os.getenv('FILTER_MAX_TOKENS_PER_CREATOR_24H', '5'))
    FILTER_MIN_CREATOR_TX_COUNT = int(os.getenv('FILTER_MIN_CREATOR_TX_COUNT', '10'))
    FILTER_MIN_CREATOR_BALANCE_BNB = float(os.getenv('FILTER_MIN_CREATOR_BALANCE_BNB', '0.01'))

    # ========== 代币基本信息过滤 ==========
    FILTER_MIN_NAME_LENGTH = int(os.getenv('FILTER_MIN_NAME_LENGTH', '2'))
    FILTER_MAX_NAME_LENGTH = int(os.getenv('FILTER_MAX_NAME_LENGTH', '30'))
    FILTER_MIN_SYMBOL_LENGTH = int(os.getenv('FILTER_MIN_SYMBOL_LENGTH', '2'))
    FILTER_MAX_SYMBOL_LENGTH = int(os.getenv('FILTER_MAX_SYMBOL_LENGTH', '10'))

    # ========== 代币供应量检查 ==========
    FILTER_MIN_TOTAL_SUPPLY = float(os.getenv('FILTER_MIN_TOTAL_SUPPLY', '1000000'))  # 100万
    FILTER_MAX_TOTAL_SUPPLY = float(os.getenv('FILTER_MAX_TOTAL_SUPPLY', '1000000000000'))  # 1万亿

    # ========== 流动性比例检查 ==========
    FILTER_MIN_LIQUIDITY_RATIO = float(os.getenv('FILTER_MIN_LIQUIDITY_RATIO', '0.00001'))  # launch_fee / total_supply

    # ========== 创建者发币间隔检查 ==========
    FILTER_MIN_CREATOR_TOKEN_INTERVAL_MINUTES = int(os.getenv('FILTER_MIN_CREATOR_TOKEN_INTERVAL_MINUTES', '30'))

    @classmethod
    def validate(cls) -> bool:
        """验证配置"""
        if cls.ENABLE_TRADING and not cls.PRIVATE_KEY:
            raise ValueError("ENABLE_TRADING=true requires PRIVATE_KEY to be set")

        if cls.FILTER_CLUSTER_BUY_AMOUNT_BNB <= 0:
            raise ValueError("FILTER_CLUSTER_BUY_AMOUNT_BNB must be positive")

        if cls.MAX_ENTRY_SIZE_BNB is not None and cls.MAX_ENTRY_SIZE_BNB <= 0:
            raise ValueError("MAX_ENTRY_SIZE_BNB must be positive")

        if cls.POSITION_SIZE <= 0:
            raise ValueError("POSITION_SIZE must be positive")

        if cls.RUNNER_RESERVE_ACTIVATION_MULTIPLE <= 1.0:
            raise ValueError("RUNNER_RESERVE_ACTIVATION_MULTIPLE must be greater than 1")

        if not 0.0 < cls.RUNNER_RESERVE_PARTIAL_EXIT_RATIO < 1.0:
            raise ValueError("RUNNER_RESERVE_PARTIAL_EXIT_RATIO must be between 0 and 1")

        if not 0.0 < cls.RUNNER_RESERVE_STOP_DRAWDOWN < 1.0:
            raise ValueError("RUNNER_RESERVE_STOP_DRAWDOWN must be between 0 and 1")

        if not math.isfinite(cls.RUNNER_RESERVE_FLOOR_RETURN) or cls.RUNNER_RESERVE_FLOOR_RETURN < -1.0:
            raise ValueError("RUNNER_RESERVE_FLOOR_RETURN must be finite and >= -1")

        if cls.RUNNER_RESERVE_MAX_HOLD_SECONDS <= 0:
            raise ValueError("RUNNER_RESERVE_MAX_HOLD_SECONDS must be positive")

        if not 0.0 <= cls.RUNNER_RESERVE_MAX_SELL_PRESSURE_30S <= 1.0:
            raise ValueError("RUNNER_RESERVE_MAX_SELL_PRESSURE_30S must be between 0 and 1")

        if cls.RUNNER_RESERVE_MIN_FLOW_EVENT_COUNT_30S < 0:
            raise ValueError("RUNNER_RESERVE_MIN_FLOW_EVENT_COUNT_30S must be non-negative")

        if cls.POST_GRADUATION_RETENTION_MAX_HOLD_SECONDS <= 0:
            raise ValueError("POST_GRADUATION_RETENTION_MAX_HOLD_SECONDS must be positive")

        if cls.POST_GRADUATION_RETENTION_MAX_DATA_AGE_SECONDS <= 0:
            raise ValueError("POST_GRADUATION_RETENTION_MAX_DATA_AGE_SECONDS must be positive")

        if (
            not math.isfinite(cls.POST_GRADUATION_RETENTION_MIN_LIQUIDITY_USD)
            or cls.POST_GRADUATION_RETENTION_MIN_LIQUIDITY_USD < 0
        ):
            raise ValueError("POST_GRADUATION_RETENTION_MIN_LIQUIDITY_USD must be finite and non-negative")

        if (
            not math.isfinite(cls.POST_GRADUATION_RETENTION_MIN_VOLUME_LIQUIDITY_RATIO_5M)
            or cls.POST_GRADUATION_RETENTION_MIN_VOLUME_LIQUIDITY_RATIO_5M < 0
        ):
            raise ValueError(
                "POST_GRADUATION_RETENTION_MIN_VOLUME_LIQUIDITY_RATIO_5M must be finite and non-negative"
            )

        if (
            not math.isfinite(cls.POST_GRADUATION_RETENTION_MIN_NEW_TRADERS_1H)
            or cls.POST_GRADUATION_RETENTION_MIN_NEW_TRADERS_1H < 0
        ):
            raise ValueError("POST_GRADUATION_RETENTION_MIN_NEW_TRADERS_1H must be finite and non-negative")

        if (
            not math.isfinite(cls.POST_GRADUATION_RETENTION_MAX_SELL_PRESSURE_5M)
            or not 0 <= cls.POST_GRADUATION_RETENTION_MAX_SELL_PRESSURE_5M <= 1
        ):
            raise ValueError("POST_GRADUATION_RETENTION_MAX_SELL_PRESSURE_5M must be between 0 and 1")

        if (
            not math.isfinite(cls.POST_GRADUATION_RETENTION_MAX_DRAWDOWN)
            or not 0 < cls.POST_GRADUATION_RETENTION_MAX_DRAWDOWN < 1
        ):
            raise ValueError("POST_GRADUATION_RETENTION_MAX_DRAWDOWN must be between 0 and 1")

        if cls.BUY_CONFIRM_POLL_INTERVAL_SECONDS <= 0:
            raise ValueError("BUY_CONFIRM_POLL_INTERVAL_SECONDS must be positive")
        if not 0 <= cls.BUY_SLIPPAGE_PERCENT < 100:
            raise ValueError("BUY_SLIPPAGE_PERCENT must be between 0 and 100")
        if not 0 <= cls.SELL_SLIPPAGE_PERCENT < 100:
            raise ValueError("SELL_SLIPPAGE_PERCENT must be between 0 and 100")
        if not 0 <= cls.FOURMEME_TOKEN_DECIMALS <= 36:
            raise ValueError("FOURMEME_TOKEN_DECIMALS must be between 0 and 36")
        if cls.BUY_MIN_AMOUNT_FLOOR < 0:
            raise ValueError("BUY_MIN_AMOUNT_FLOOR must be non-negative")
        if cls.CHAIN_ID <= 0:
            raise ValueError("MEME_CHAIN_ID must be a positive chain id")

        if cls.BUY_CONFIRM_TIMEOUT_SECONDS <= 0:
            raise ValueError("BUY_CONFIRM_TIMEOUT_SECONDS must be positive")

        if cls.BUY_FAST_STATUS_MAX_STALENESS_SECONDS <= 0:
            raise ValueError("BUY_FAST_STATUS_MAX_STALENESS_SECONDS must be positive")

        if cls.BUY_FAST_STATUS_MAX_CHAIN_LAG_SECONDS <= 0:
            raise ValueError("BUY_FAST_STATUS_MAX_CHAIN_LAG_SECONDS must be positive")

        if cls.TX_RECEIPT_POLL_LATENCY_SECONDS <= 0:
            raise ValueError("TX_RECEIPT_POLL_LATENCY_SECONDS must be positive")

        if cls.MIN_ENTRY_VOLUME_30S < 0:
            raise ValueError("MIN_ENTRY_VOLUME_30S must be non-negative")

        if cls.MIN_ENTRY_PRICE_VOLATILITY < 0:
            raise ValueError("MIN_ENTRY_PRICE_VOLATILITY must be non-negative")

        if cls.BUY_NEAR_THRESHOLD_MIN_PROB is not None and (
            not math.isfinite(cls.BUY_NEAR_THRESHOLD_MIN_PROB)
            or cls.BUY_NEAR_THRESHOLD_MIN_PROB <= 0
            or cls.BUY_NEAR_THRESHOLD_MIN_PROB > 1.0
        ):
            raise ValueError("BUY_NEAR_THRESHOLD_MIN_PROB must be positive and <= 1.0")

        if cls.BUY_NEAR_MIN_PRED_RETURN is not None and (
            not math.isfinite(cls.BUY_NEAR_MIN_PRED_RETURN)
            or cls.BUY_NEAR_MIN_PRED_RETURN < 0
        ):
            raise ValueError("BUY_NEAR_MIN_PRED_RETURN must be non-negative")

        if cls.BUY_NEAR_MIN_ENTRY_VOLUME_30S is not None and (
            not math.isfinite(cls.BUY_NEAR_MIN_ENTRY_VOLUME_30S)
            or cls.BUY_NEAR_MIN_ENTRY_VOLUME_30S < 0
        ):
            raise ValueError("BUY_NEAR_MIN_ENTRY_VOLUME_30S must be non-negative")

        if cls.BUY_NEAR_MIN_ENTRY_PRICE_VOLATILITY is not None and (
            not math.isfinite(cls.BUY_NEAR_MIN_ENTRY_PRICE_VOLATILITY)
            or cls.BUY_NEAR_MIN_ENTRY_PRICE_VOLATILITY < 0
        ):
            raise ValueError("BUY_NEAR_MIN_ENTRY_PRICE_VOLATILITY must be non-negative")

        if cls.BUY_NEAR_MIN_AGE_SECONDS is not None and (
            not math.isfinite(cls.BUY_NEAR_MIN_AGE_SECONDS)
            or cls.BUY_NEAR_MIN_AGE_SECONDS < 0
        ):
            raise ValueError("BUY_NEAR_MIN_AGE_SECONDS must be non-negative")

        if cls.BUY_PRIMARY_SCORE_RESCUE_MIN_PROB is not None and (
            not math.isfinite(cls.BUY_PRIMARY_SCORE_RESCUE_MIN_PROB)
            or cls.BUY_PRIMARY_SCORE_RESCUE_MIN_PROB <= 0
            or cls.BUY_PRIMARY_SCORE_RESCUE_MIN_PROB > 1.0
        ):
            raise ValueError("BUY_PRIMARY_SCORE_RESCUE_MIN_PROB must be positive and <= 1.0")

        if cls.BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN is not None and (
            not math.isfinite(cls.BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN)
            or cls.BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN < 0
        ):
            raise ValueError("BUY_PRIMARY_SCORE_RESCUE_MIN_PRED_RETURN must be non-negative")

        if cls.BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S is not None and (
            not math.isfinite(cls.BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S)
            or cls.BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S < 0
        ):
            raise ValueError("BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_VOLUME_30S must be non-negative")

        if cls.BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY is not None and (
            not math.isfinite(cls.BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY)
            or cls.BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY < 0
        ):
            raise ValueError("BUY_PRIMARY_SCORE_RESCUE_MIN_ENTRY_PRICE_VOLATILITY must be non-negative")

        if cls.BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS is not None and (
            not math.isfinite(cls.BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS)
            or cls.BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS < 0
        ):
            raise ValueError("BUY_PRIMARY_SCORE_RESCUE_MIN_AGE_SECONDS must be non-negative")

        if cls.BUY_ACTION_POLICY_ROUTER_ENABLED and (
            not cls.BUY_ACTION_POLICY_ROUTER_TRAIN_REJECTED_REPORTS
            or not cls.BUY_ACTION_POLICY_ROUTER_TRAIN_ACCEPTED_REPORTS
        ):
            raise ValueError(
                "BUY_ACTION_POLICY_ROUTER_ENABLED=true requires rejected and accepted train report paths"
            )

        if (
            not math.isfinite(cls.BUY_ACTION_POLICY_ROUTER_MIN_CONFIDENCE)
            or cls.BUY_ACTION_POLICY_ROUTER_MIN_CONFIDENCE <= 0
            or cls.BUY_ACTION_POLICY_ROUTER_MIN_CONFIDENCE > 1.0
        ):
            raise ValueError("BUY_ACTION_POLICY_ROUTER_MIN_CONFIDENCE must be positive and <= 1.0")

        for attr in (
            'BUY_ACTION_POLICY_ROUTER_MAX_DEPTH',
            'BUY_ACTION_POLICY_ROUTER_MIN_SAMPLES_LEAF',
            'BUY_ACTION_POLICY_ROUTER_MIN_COMMON_FEATURES',
            'BUY_ACTION_POLICY_ROUTER_MIN_LIVE_FEATURES',
        ):
            if int(getattr(cls, attr)) <= 0:
                raise ValueError(f"{attr} must be positive")

        if (
            not math.isfinite(cls.BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT)
            or cls.BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT < 0
        ):
            raise ValueError("BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT must be non-negative")

        if (
            not math.isfinite(cls.BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT)
            or cls.BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT < 0
        ):
            raise ValueError("BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT must be non-negative")

        if cls.BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT <= cls.BUY_ACTION_POLICY_CONTINUE_HOLD_ACTIVATION_PCT:
            raise ValueError("BUY_ACTION_POLICY_CONTINUE_HOLD_RELEASE_PCT must be greater than activation pct")

        return True

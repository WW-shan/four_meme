"""
训练集生成器 - 从历史数据生成训练样本
"""

import json
import logging
import math
import re
from bisect import bisect_left, bisect_right
from typing import Dict, List, Optional
from pathlib import Path
from datetime import datetime

from src.data.feature_extractor import extract_features, resolve_current_price

logger = logging.getLogger(__name__)

_INCREMENTAL_FILENAME_PATTERNS = (
    re.compile(r"^lifecycle_incremental_(?P<order>\d{8}_\d{6}|\d+)(?:_part(?P<part>\d+))?\.jsonl$"),
)
_SNAPSHOT_FILENAME_PATTERNS = (
    re.compile(r"^lifecycle_(?P<order>\d{8}_\d{6}|\d+)\.jsonl$"),
)
_LIFECYCLE_FILENAME_PATTERNS = _INCREMENTAL_FILENAME_PATTERNS + _SNAPSHOT_FILENAME_PATTERNS


def _filename_order_value(path: Path, patterns) -> Optional[tuple]:
    name = path.name
    for idx, pattern in enumerate(patterns):
        match = pattern.match(name)
        if match:
            raw_value = match.group("order")
            part_value = match.groupdict().get("part") or "0"
            return idx, int(raw_value.replace("_", "")), int(part_value), name
    return None


def _incremental_sort_key(path: Path):
    return _filename_order_value(path, _INCREMENTAL_FILENAME_PATTERNS)


def _snapshot_sort_key(path: Path):
    return _filename_order_value(path, _SNAPSHOT_FILENAME_PATTERNS)


def stable_lifecycle_order(files, *, log=None):
    paths = [Path(path) for path in files]
    if not paths:
        return []

    standard = []
    non_standard = []
    for path in paths:
        key = _filename_order_value(path, _LIFECYCLE_FILENAME_PATTERNS)
        if key is None:
            non_standard.append(path)
        else:
            standard.append((key, path))

    ordered_standard = [path for key, path in sorted(standard, key=lambda item: item[0])]
    if not non_standard:
        return ordered_standard

    active_logger = log or logger
    active_logger.info("Lifecycle ordering fallback to mtime for non-standard filenames")
    ordered_non_standard = sorted(non_standard, key=lambda p: (p.stat().st_mtime, p.name))
    return ordered_standard + ordered_non_standard


def _recent_event_count(rows: List[Dict], sample_time: int, window_seconds: int) -> int:
    upper = int(sample_time)
    cutoff = int(sample_time) - int(window_seconds)
    return sum(1 for row in rows if cutoff <= int(row.get('timestamp', 0)) <= upper)


def _decision_time_chain_lag_seconds(sample_time: int, rows: List[Dict]) -> Optional[float]:
    latest_timestamp = None
    for row in rows:
        try:
            timestamp = int(row.get('timestamp', 0) or 0)
        except (TypeError, ValueError):
            continue
        if timestamp <= 0 or timestamp > int(sample_time):
            continue
        latest_timestamp = timestamp if latest_timestamp is None else max(latest_timestamp, timestamp)
    if latest_timestamp is None:
        return None
    return float(max(0, int(sample_time) - latest_timestamp))


class DatasetBuilder:
    """从历史数据构建训练集"""

    def __init__(
        self,
        lifecycle_dir: str = "data/training",
        sample_intervals: Optional[List[int]] = None,
        future_windows: Optional[List[int]] = None,
        sample_mode: str = "trade_event",
        max_sample_age_seconds: int = 300,
        max_samples_per_token: Optional[int] = None,
        label_fee_bps: float = 0.0,
        label_slippage_bps: float = 0.0,
        label_stop_loss_pct: float = -50.0,
        label_target_return_pct: float = 80.0,
        label_entry_delay_seconds: int = 0,
        label_exit_delay_seconds: int = 0,
        label_live_downside_penalty_weight: float = 0.0,
        label_delay_robust_entry_delay_seconds: Optional[List[int]] = None,
        label_delay_robust_min_weight: float = 1.0,
        label_fixed_stake_bnb: Optional[float] = None,
        label_entry_fixed_cost_bnb: float = 0.0,
        label_exit_fixed_cost_bnb: float = 0.0,
        label_entry_price_protection_pct: Optional[float] = None,
        min_entry_unique_buyers: int = 3,
        min_entry_buy_count: int = 5,
        include_flow_features: bool = False,
    ):
        self.lifecycle_dir = Path(lifecycle_dir)
        self.samples: List[Dict] = []
        self.sample_intervals = self._normalize_sample_intervals(sample_intervals)
        self.future_windows = self._normalize_future_windows(future_windows)
        self.sample_mode = str(sample_mode or "trade_event").strip().lower()
        if self.sample_mode not in {"trade_event", "per_second"}:
            self.sample_mode = "trade_event"
        self.max_sample_age_seconds = max(1, int(max_sample_age_seconds))
        self.max_samples_per_token = int(max_samples_per_token) if max_samples_per_token else None
        self.label_fee_bps = max(0.0, float(label_fee_bps))
        self.label_slippage_bps = max(0.0, float(label_slippage_bps))
        self.label_stop_loss_pct = float(label_stop_loss_pct)
        self.label_target_return_pct = float(label_target_return_pct)
        self.label_entry_delay_seconds = max(0, int(label_entry_delay_seconds or 0))
        self.label_exit_delay_seconds = max(0, int(label_exit_delay_seconds or 0))
        self.label_live_downside_penalty_weight = max(0.0, float(label_live_downside_penalty_weight or 0.0))
        self.label_delay_robust_entry_delay_seconds = self._normalize_delay_seconds_list(
            label_delay_robust_entry_delay_seconds
        )
        self.label_delay_robust_min_weight = max(0.0, min(1.0, float(label_delay_robust_min_weight or 0.0)))
        self.label_fixed_stake_bnb = (
            None
            if label_fixed_stake_bnb is None
            else max(0.0, float(label_fixed_stake_bnb or 0.0))
        )
        self.label_entry_fixed_cost_bnb = max(0.0, float(label_entry_fixed_cost_bnb or 0.0))
        self.label_exit_fixed_cost_bnb = max(0.0, float(label_exit_fixed_cost_bnb or 0.0))
        self.label_entry_price_protection_pct = (
            None
            if label_entry_price_protection_pct is None
            else max(0.0, float(label_entry_price_protection_pct))
        )
        self.min_entry_unique_buyers = max(1, int(min_entry_unique_buyers or 1))
        self.min_entry_buy_count = max(1, int(min_entry_buy_count or 1))
        self.include_flow_features = bool(include_flow_features)

        # 过滤统计
        self.total_tokens = 0
        self.filtered_tokens = 0
        self.filter_reasons = {
            'early_whale_dominated': 0,
        }

    @staticmethod
    def _normalize_delay_seconds_list(values) -> List[int]:
        if values is None:
            return []
        raw_values = values
        if isinstance(values, str):
            raw_values = [part.strip() for part in values.split(",") if part.strip()]

        normalized = []
        for value in raw_values:
            try:
                normalized.append(max(0, int(value)))
            except (TypeError, ValueError):
                continue
        return sorted(set(normalized))

    @staticmethod
    def _normalize_sample_intervals(sample_intervals: Optional[List[int]]) -> List[int]:
        if not sample_intervals:
            return list(range(1, 301))

        normalized = sorted({int(x) for x in sample_intervals if int(x) > 0})
        return normalized if normalized else list(range(1, 301))

    @staticmethod
    def _normalize_future_windows(future_windows: Optional[List[int]]) -> List[int]:
        if not future_windows:
            return [300]

        normalized = sorted({int(x) for x in future_windows if int(x) > 0})
        return normalized if normalized else [300]

    @staticmethod
    def _limit_evenly(values: List[int], limit: Optional[int]) -> List[int]:
        if not limit or int(limit) <= 0 or len(values) <= int(limit):
            return list(values)
        count = int(limit)
        if count == 1:
            return [values[0]]
        positions = sorted({round(i * (len(values) - 1) / (count - 1)) for i in range(count)})
        return [values[int(position)] for position in positions]

    def load_lifecycle_paths(self, paths: List[str]) -> int:
        """按调用方给定顺序加载生命周期文件。"""
        lifecycle_files = [Path(path) for path in paths]
        return self._load_lifecycle_file_list(lifecycle_files)

    def load_lifecycle_files(self, file_pattern: str = "lifecycle_*.jsonl") -> int:
        """
        加载生命周期数据文件

        Args:
            file_pattern: 文件匹配模式

        Returns:
            加载的代币数量
        """
        lifecycle_files: List[Path]

        if file_pattern == "lifecycle_*.jsonl":
            incremental_files = [
                path for path in stable_lifecycle_order(self.lifecycle_dir.glob("lifecycle_incremental_*.jsonl"))
                if _incremental_sort_key(path) is not None
            ]
            snapshot_files = [
                path for path in sorted(
                    self.lifecycle_dir.glob("lifecycle_*.jsonl"),
                    key=lambda p: (_snapshot_sort_key(p) is None, _snapshot_sort_key(p) or (0, 0, p.name)),
                )
                if _snapshot_sort_key(path) is not None
            ]

            if incremental_files:
                logger.info(f"Using incremental lifecycle files: {len(incremental_files)}")
                lifecycle_files = incremental_files.copy()
            else:
                lifecycle_files = snapshot_files
                if lifecycle_files:
                    latest_file = lifecycle_files[-1]
                    lifecycle_files = [latest_file]
                    logger.info(f"Using latest lifecycle file only: {latest_file.name}")
        elif file_pattern == "lifecycle_incremental_*.jsonl":
            lifecycle_files = [
                path for path in stable_lifecycle_order(self.lifecycle_dir.glob(file_pattern))
                if _incremental_sort_key(path) is not None
            ]
        else:
            lifecycle_files = sorted(self.lifecycle_dir.glob(file_pattern))

        return self._load_lifecycle_file_list(lifecycle_files)

    def _load_lifecycle_file_list(self, lifecycle_files: List[Path]) -> int:
        loaded_tokens = 0

        logger.info(f"Found {len(lifecycle_files)} lifecycle files")

        merged_lifecycles: Dict[str, Dict] = {}
        ordered_token_addresses: List[str] = []

        for filepath in lifecycle_files:
            with filepath.open('r', encoding='utf-8') as f:
                for line in f:
                    try:
                        lifecycle = json.loads(line.strip())

                        # 标准化格式
                        lifecycle = self._normalize_lifecycle(lifecycle)

                        token_address = str(lifecycle.get('token_address') or '').strip().lower()
                        if not token_address:
                            continue
                        lifecycle['token_address'] = token_address

                        existing = merged_lifecycles.get(token_address)
                        if existing is None:
                            merged_lifecycles[token_address] = lifecycle
                            ordered_token_addresses.append(token_address)
                            continue

                        merged_lifecycles[token_address] = self._merge_lifecycle_rows(
                            existing,
                            lifecycle,
                        )
                    except Exception as e:
                        logger.error(f"Error loading lifecycle: {e}")
                        import traceback
                        traceback.print_exc()

        total_candidates = len(ordered_token_addresses)
        progress_step = max(1, total_candidates // 20) if total_candidates > 0 else 1

        for index, token_address in enumerate(ordered_token_addresses, start=1):
            lifecycle = merged_lifecycles[token_address]

            # 统计总代币数
            self.total_tokens += 1

            # 检查是否是早期大资金控制的代币
            if self._is_early_whale_dominated(lifecycle):
                self.filtered_tokens += 1
                self.filter_reasons['early_whale_dominated'] += 1
                logger.debug(f"Filtered token {lifecycle.get('token_address', 'unknown')}: early whale dominated")
                continue

            # 生成样本
            samples = self._generate_samples_from_lifecycle(lifecycle)
            self.samples.extend(samples)
            loaded_tokens += 1

            if index == 1 or index % progress_step == 0 or index == total_candidates:
                progress_pct = (index / total_candidates * 100.0) if total_candidates > 0 else 100.0
                logger.info(
                    "Dataset build progress: %.1f%% (%d/%d tokens) | loaded=%d filtered=%d samples=%d",
                    progress_pct,
                    index,
                    total_candidates,
                    loaded_tokens,
                    self.filtered_tokens,
                    len(self.samples),
                )

        # 输出过滤统计
        logger.info(f"Loaded {loaded_tokens} tokens, generated {len(self.samples)} samples")
        filtered_ratio = (self.filtered_tokens / self.total_tokens * 100.0) if self.total_tokens > 0 else 0.0
        logger.info(f"Filter stats: {self.filtered_tokens}/{self.total_tokens} tokens filtered ({filtered_ratio:.1f}%)")
        logger.info(f"Filter reasons: {self.filter_reasons}")

        return loaded_tokens

    def _is_early_whale_dominated(self, lifecycle: Dict) -> bool:
        """
        检测是否是早期被大资金控制的代币

        这类代币的特征:
        - 发币前几秒就有大资金集中买入
        - 交易笔数少,但单笔金额大
        - 早期没有卖出(大资金控盘,散户进不去)
        - 实盘中根本买不到,不应纳入训练集

        Returns:
            True: 需要过滤
            False: 正常代币
        """
        create_time = lifecycle['create_timestamp']
        early_window = 30  # 前30秒

        early_buys = [b for b in lifecycle['buys']
                      if b['timestamp'] - create_time <= early_window]
        early_sells = [s for s in lifecycle['sells']
                       if s['timestamp'] - create_time <= early_window]

        # 放宽条件: 只过滤极端情况
        if len(early_buys) == 0:
            return False  # 没有交易数据,保留

        # 计算早期买单的平均金额和总量
        early_volumes = [b['bnb_amount'] for b in early_buys]
        avg_early_buy = sum(early_volumes) / len(early_volumes) if early_volumes else 0
        total_early_volume = sum(early_volumes)

        # 只过滤明显的大资金控盘情况:
        # 1. 前30秒总买入 > 2 BNB 且交易笔数 <= 5 (超大单集中买入)
        if total_early_volume > 2.0 and len(early_buys) <= 5:
            return True

        # 2. 前3笔平均金额 > 0.5 BNB (单笔超大)
        if len(early_buys) >= 3 and avg_early_buy > 0.5:
            return True

        # 3. 前30秒有买入但完全没有卖出,且买入量较大 (大资金控盘特征)
        #    只有买没有卖 + 总量 > 1 BNB + 笔数 <= 8
        if len(early_sells) == 0 and total_early_volume > 1.0 and len(early_buys) <= 8:
            return True

        return False

    @staticmethod
    def _event_identity(event: Dict) -> str:
        """Return a stable identity for repeated snapshot events."""
        identity = {
            'timestamp': int(event.get('timestamp', 0) or 0),
            'account': str(event.get('account', '') or '').lower(),
            'token_amount': event.get('token_amount'),
            'bnb_amount': event.get('bnb_amount'),
            'ether_amount': event.get('ether_amount'),
            'price': event.get('price'),
            'type': str(event.get('type', '') or ''),
            'block_number': event.get('block_number', event.get('blockNumber')),
            'log_index': event.get('log_index', event.get('logIndex')),
            'transaction_hash': str(
                event.get('transaction_hash', event.get('transactionHash')) or ''
            ).lower(),
        }
        return json.dumps(identity, sort_keys=True, default=str, separators=(',', ':'))

    @classmethod
    def _merge_event_rows(cls, existing, incoming) -> List[Dict]:
        merged = {}
        for event in list(existing or []) + list(incoming or []):
            if not isinstance(event, dict):
                continue
            identity = cls._event_identity(event)
            if identity and identity not in merged:
                merged[identity] = dict(event)
        return sorted(
            merged.values(),
            key=lambda event: (
                int(event.get('timestamp', 0) or 0),
                int(event.get('block_number', event.get('blockNumber', -1)) or -1),
                int(event.get('log_index', event.get('logIndex', -1)) or -1),
                str(event.get('transaction_hash', event.get('transactionHash')) or ''),
            ),
        )

    @classmethod
    def _merge_lifecycle_rows(cls, existing: Dict, incoming: Dict) -> Dict:
        """Merge cumulative snapshots and reactivation fragments by token."""
        existing_activity = len(existing.get('buys', [])) + len(existing.get('sells', []))
        incoming_activity = len(incoming.get('buys', [])) + len(incoming.get('sells', []))
        merged = dict(incoming if incoming_activity >= existing_activity else existing)
        merged['buys'] = cls._merge_event_rows(existing.get('buys'), incoming.get('buys'))
        merged['sells'] = cls._merge_event_rows(existing.get('sells'), incoming.get('sells'))
        merged['price_history'] = cls._merge_event_rows(
            existing.get('price_history'), incoming.get('price_history')
        )
        merged['total_buy_volume_bnb'] = sum(
            float(event.get('bnb_amount', 0.0) or 0.0) for event in merged['buys']
        )
        merged['total_sell_volume_bnb'] = sum(
            float(event.get('bnb_amount', 0.0) or 0.0) for event in merged['sells']
        )
        merged['total_buy_count'] = len(merged['buys'])
        merged['total_sell_count'] = len(merged['sells'])
        merged['unique_buyers'] = sorted({
            str(event.get('account', '') or '')
            for event in merged['buys']
            if str(event.get('account', '') or '')
        })
        merged['unique_sellers'] = sorted({
            str(event.get('account', '') or '')
            for event in merged['sells']
            if str(event.get('account', '') or '')
        })
        trades = sorted(merged['buys'] + merged['sells'], key=lambda event: int(event.get('timestamp', 0) or 0))
        prices = [float(event.get('price', 0.0) or 0.0) for event in trades if float(event.get('price', 0.0) or 0.0) > 0.0]
        if prices:
            merged['price_first'] = float(merged['buys'][0].get('price', 0.0) or 0.0) if merged['buys'] else prices[0]
            merged['price_current'] = prices[-1]
            merged['price_max'] = max(prices)
            merged['price_min'] = min(prices)
        timestamps = [
            int(event.get('timestamp', 0) or 0)
            for event in trades + merged['price_history']
            if int(event.get('timestamp', 0) or 0) > 0
        ]
        create_timestamp = int(merged.get('create_timestamp', 0) or 0)
        if create_timestamp > 0:
            timestamps.append(create_timestamp)
        previous_update = int(merged.get('last_update', 0) or 0)
        if previous_update > 0:
            timestamps.append(previous_update)
        if timestamps:
            merged['last_update'] = max(timestamps)
        merged['graduated'] = bool(existing.get('graduated') or incoming.get('graduated'))
        graduate_times = [
            int(value)
            for value in (existing.get('graduate_time'), incoming.get('graduate_time'))
            if int(value or 0) > 0
        ]
        if graduate_times:
            merged['graduate_time'] = max(graduate_times)
        return merged

    def _normalize_lifecycle(self, lifecycle: Dict) -> Dict:
        """标准化生命周期数据格式 (适配新数据源)"""
        # 如果是新格式 (包含 created_at 且没有 buys/sells)
        if 'created_at' in lifecycle and 'buys' not in lifecycle:
            norm = lifecycle.copy()
            norm['create_timestamp'] = lifecycle['created_at']

            # 初始化 buys/sells
            norm['buys'] = []
            norm['sells'] = []

            # 缩放 supply 和 launch_fee 到 Wei (以匹配旧代码的除法逻辑)
            # 假设新数据是 readable 格式 (如 10亿, 0.01)
            norm['total_supply'] = float(lifecycle.get('total_supply', 0)) * 1e18
            norm['launch_fee'] = float(lifecycle.get('launch_fee', 0)) * 1e18

            # 处理 purchases -> buys
            for p in lifecycle.get('purchases', []):
                new_p = p.copy()
                # 关键: 计算价格
                # price = ether_amount / token_amount
                new_p['bnb_amount'] = p['ether_amount']
                if p['token_amount'] > 0:
                    new_p['price'] = p['ether_amount'] / p['token_amount']
                else:
                    new_p['price'] = 0
                norm['buys'].append(new_p)

            # 处理 sales -> sells
            for s in lifecycle.get('sales', []):
                new_s = s.copy()
                new_s['bnb_amount'] = s['ether_amount']
                if s['token_amount'] > 0:
                    new_s['price'] = s['ether_amount'] / s['token_amount']
                else:
                    new_s['price'] = 0
                norm['sells'].append(new_s)

            return norm

        return lifecycle

    def _generate_samples_from_lifecycle(self, lifecycle: Dict,
                                          sample_intervals: Optional[List[int]] = None) -> List[Dict]:
        """
        从单个代币生命周期生成多个训练样本

        Args:
            lifecycle: 代币生命周期数据
            sample_intervals: 采样时间点 (相对launch时间的秒数)

        Returns:
            训练样本列表
        """
        # 标准化数据格式 (适配新旧数据)
        lifecycle = self._normalize_lifecycle(lifecycle)

        # Known non-BNB quote assets must not be labelled with BNB-denominated
        # prices. Records without quote metadata remain unknown and are kept.
        quote_symbol = str(lifecycle.get('quote_symbol') or '').strip().upper()
        if quote_symbol and quote_symbol != 'BNB':
            return []

        if sample_intervals is None:
            sample_intervals = self._resolve_sample_intervals_for_lifecycle(lifecycle)

        samples = []
        create_time = lifecycle['create_timestamp']

        # 恢复 set
        lifecycle['unique_buyers'] = set(lifecycle.get('unique_buyers', []))
        lifecycle['unique_sellers'] = set(lifecycle.get('unique_sellers', []))
        lifecycle['buys'] = sorted(lifecycle.get('buys', []), key=lambda trade: int(trade.get('timestamp', 0) or 0))
        lifecycle['sells'] = sorted(lifecycle.get('sells', []), key=lambda trade: int(trade.get('timestamp', 0) or 0))
        all_trades_sorted = sorted(
            lifecycle['buys'] + lifecycle['sells'],
            key=lambda trade: int(trade.get('timestamp', 0) or 0),
        )
        buy_timestamps = [int(trade.get('timestamp', 0) or 0) for trade in lifecycle['buys']]
        sell_timestamps = [int(trade.get('timestamp', 0) or 0) for trade in lifecycle['sells']]
        trade_timestamps = [int(trade.get('timestamp', 0) or 0) for trade in all_trades_sorted]

        for interval in sample_intervals:
            sample_time = create_time + interval

            # 检查是否有足够的历史数据
            buy_end = bisect_right(buy_timestamps, sample_time)
            past_buys = lifecycle['buys'][:buy_end]
            if not past_buys:
                continue
            sell_end = bisect_right(sell_timestamps, sample_time)
            past_sells = lifecycle['sells'][:sell_end]

            for future_window in self.future_windows:
                future_end_time = sample_time + future_window
                if 'last_update' in lifecycle and lifecycle['last_update'] < future_end_time:
                    continue
                future_start_index = bisect_right(trade_timestamps, sample_time)
                future_end_index = bisect_right(trade_timestamps, future_end_time)
                future_trades_sorted = all_trades_sorted[future_start_index:future_end_index]
                if not future_trades_sorted:
                    continue

                sample = self._create_sample_with_window(
                    lifecycle=lifecycle,
                    sample_time=sample_time,
                    future_window=future_window,
                    past_buys=past_buys,
                    past_sells=past_sells,
                    future_trades_sorted=future_trades_sorted,
                )
                if sample:
                    samples.append(sample)

        return samples

    def _resolve_sample_intervals_for_lifecycle(self, lifecycle: Dict) -> List[int]:
        """根据采样模式生成该 token 的采样时间点（相对创建秒）。"""
        if self.sample_mode == "per_second":
            return self.sample_intervals

        create_time = int(lifecycle.get("create_timestamp", 0) or 0)
        if create_time <= 0:
            return self.sample_intervals

        trade_timestamps = [
            int(t.get("timestamp", 0) or 0)
            for t in (lifecycle.get("buys", []) + lifecycle.get("sells", []))
        ]
        intervals = []
        for ts in trade_timestamps:
            if ts <= create_time:
                continue
            age = ts - create_time
            if age <= self.max_sample_age_seconds:
                intervals.append(int(age))

        normalized = sorted(set(intervals))
        normalized = self._limit_evenly(normalized, self.max_samples_per_token)
        # trade_event 模式下不回退到按秒采样，避免混入旧逻辑样本
        return normalized

    def _sanitize_numeric_dict(self, data: Dict) -> Dict:
        sanitized = {}
        for key, value in data.items():
            if isinstance(value, bool):
                sanitized[key] = value
                continue

            if isinstance(value, (int, float)):
                number = float(value)
                if not math.isfinite(number):
                    number = 0.0
                sanitized[key] = number
                continue

            sanitized[key] = value
        return sanitized

    def _deduplicate_samples(self):
        if not self.samples:
            return

        dedup_map = {}
        for sample in self.samples:
            meta = sample.get('meta', {})
            token_address = str(meta.get('token_address', ''))
            sample_time = int(meta.get('sample_time', 0) or 0)
            future_window = int(sample.get('label', {}).get('future_window_seconds', meta.get('future_window', 0)) or 0)
            dedup_key = (token_address, sample_time, future_window)
            dedup_map[dedup_key] = sample

        deduped = list(dedup_map.values())
        removed = len(self.samples) - len(deduped)
        if removed > 0:
            logger.info(f"Deduplicated samples: removed {removed} duplicates")
        self.samples = deduped

    # 样本最低活跃度要求 (训练/测试通用)
    MIN_UNIQUE_BUYERS = 3   # legacy default: 至少3个独立买家
    MIN_BUY_COUNT = 5       # legacy default: 至少5笔买入

    def _create_sample_with_window(
        self,
        lifecycle: Dict,
        sample_time: int,
        future_window: int,
        *,
        past_buys: Optional[List[Dict]] = None,
        past_sells: Optional[List[Dict]] = None,
        future_trades_sorted: Optional[List[Dict]] = None,
    ) -> Optional[Dict]:
        """创建单个训练样本 (带未来窗口信息)"""

        # 只使用 sample_time 之前的数据
        if past_buys is None:
            past_buys = [b for b in lifecycle['buys'] if b['timestamp'] <= sample_time]
        if past_sells is None:
            past_sells = [s for s in lifecycle['sells'] if s['timestamp'] <= sample_time]

        if not past_buys:
            return None

        # === 活跃度过滤: 排除单人币/低活跃度时间点（不限制最低时间） ===
        unique_buyers = len(set(b['account'] for b in past_buys))
        if unique_buyers < self.min_entry_unique_buyers:
            return None
        if len(past_buys) < self.min_entry_buy_count:
            return None

        # 计算特征
        features = self._extract_features(lifecycle, past_buys, past_sells, sample_time)
        chain_lag_seconds = _decision_time_chain_lag_seconds(sample_time, past_buys + past_sells)
        if chain_lag_seconds is not None:
            features['lifecycle_status_chain_lag_seconds'] = chain_lag_seconds

        # 添加未来窗口作为特征 (帮助模型理解预测时间范围)
        features['future_window'] = future_window

        # 计算标签
        label = self._calculate_label_with_window(
            lifecycle,
            sample_time,
            future_window,
            past_buys=past_buys,
            past_sells=past_sells,
            future_trades_sorted=future_trades_sorted,
        )

        if label is None:
            return None

        return {
            'features': features,
            'label': label,
            'meta': {
                'token_address': lifecycle['token_address'],
                'symbol': lifecycle['symbol'],
                'sample_time': sample_time,
                'sample_interval': sample_time - lifecycle['create_timestamp'],
                'future_window': future_window,
                'flow_event_count_10s': _recent_event_count(past_buys, sample_time, 10) + _recent_event_count(past_sells, sample_time, 10),
                'flow_event_count_30s': _recent_event_count(past_buys, sample_time, 30) + _recent_event_count(past_sells, sample_time, 30),
                'flow_event_count_60s': _recent_event_count(past_buys, sample_time, 60) + _recent_event_count(past_sells, sample_time, 60),
            }
        }

    def _extract_features(self, lifecycle: Dict,
                          past_buys: List[Dict],
                          past_sells: List[Dict],
                          sample_time: int) -> Dict:
        """提取特征 (增强版)"""
        return extract_features(
            lifecycle=lifecycle,
            past_buys=past_buys,
            past_sells=past_sells,
            sample_time=sample_time,
            include_flow_features=self.include_flow_features,
        )

    def _calculate_label_with_window(
        self,
        lifecycle: Dict,
        sample_time: int,
        future_window: int,
        *,
        past_buys: Optional[List[Dict]] = None,
        past_sells: Optional[List[Dict]] = None,
        future_trades_sorted: Optional[List[Dict]] = None,
    ) -> Optional[Dict]:
        """
        计算标签（通用多目标版本）

        基础连续标签：
        - max_return_pct: 未来窗口内最大收益率
        - min_return_pct: 未来窗口内最小收益率
        - final_return_pct: 未来窗口结束时（最后一笔成交）收益率
        - future_window_seconds: 当前样本对应的未来窗口（秒）
        """

        # 当前价格
        if past_buys is None:
            past_buys = [b for b in lifecycle['buys'] if b['timestamp'] <= sample_time]
        if not past_buys:
            return None

        if past_sells is None:
            past_sells = [s for s in lifecycle['sells'] if s['timestamp'] <= sample_time]
        current_price = resolve_current_price(past_buys, past_sells)

        # 未来价格
        future_end_time = sample_time + future_window
        if future_trades_sorted is None:
            future_trades = [
                p for p in (lifecycle['buys'] + lifecycle['sells'])
                if sample_time < p['timestamp'] <= future_end_time
            ]
            future_trades_sorted = sorted(future_trades, key=lambda p: p['timestamp'])
        else:
            future_trades_sorted = list(future_trades_sorted)
        future_prices = [p['price'] for p in future_trades_sorted]

        if not future_prices:
            return None

        max_future_price = max(future_prices)
        min_future_price = min(future_prices)

        # 窗口结束时的价格（最后一笔成交价 = TIME_EXIT 时的实际卖出价）
        final_future_price = future_trades_sorted[-1]['price']

        fee_rate = self.label_fee_bps / 10000.0
        slippage_rate = self.label_slippage_bps / 10000.0
        entry_effective_price = current_price * (1.0 + slippage_rate) / max(1e-12, 1.0 - fee_rate)
        live_label = self._calculate_live_execution_label(
            future_trades_sorted,
            sample_time=sample_time,
            future_window=future_window,
            current_price=current_price,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
        )

        # 计算收益率
        if current_price > 0:
            max_return = ((max_future_price - current_price) / current_price) * 100
            min_return = ((min_future_price - current_price) / current_price) * 100
            final_return = ((final_future_price - current_price) / current_price) * 100
        else:
            max_return = 0
            min_return = 0
            final_return = 0

        adjusted_return_by_trade = []
        for trade in future_trades_sorted:
            future_price = float(trade.get('price', 0.0) or 0.0)
            if entry_effective_price > 0.0 and future_price > 0.0:
                exit_effective_price = future_price * max(0.0, 1.0 - slippage_rate) * max(0.0, 1.0 - fee_rate)
                adjusted_return = ((exit_effective_price - entry_effective_price) / entry_effective_price) * 100.0
            else:
                adjusted_return = 0.0
            adjusted_return_by_trade.append((trade, adjusted_return))

        best_return_before_stop = None
        target_hit_before_stop = False
        stop_hit_before_target = False
        time_to_target_seconds = 0
        time_to_stop_seconds = 0

        for trade, adjusted_return in adjusted_return_by_trade:
            if best_return_before_stop is None or adjusted_return > best_return_before_stop:
                best_return_before_stop = adjusted_return

            if not target_hit_before_stop and adjusted_return >= self.label_target_return_pct:
                target_hit_before_stop = True
                time_to_target_seconds = int(trade['timestamp'] - sample_time)

            if adjusted_return <= self.label_stop_loss_pct:
                if not target_hit_before_stop:
                    stop_hit_before_target = True
                time_to_stop_seconds = int(trade['timestamp'] - sample_time)
                break

        cost_adjusted_returns = [value for _trade, value in adjusted_return_by_trade]
        if cost_adjusted_returns:
            cost_adjusted_max_return = max(cost_adjusted_returns)
            cost_adjusted_min_return = min(cost_adjusted_returns)
            cost_adjusted_final_return = cost_adjusted_returns[-1]
        else:
            cost_adjusted_max_return = 0.0
            cost_adjusted_min_return = 0.0
            cost_adjusted_final_return = 0.0

        executable_return = float(best_return_before_stop) if best_return_before_stop is not None else 0.0

        label = {
            'max_return_pct': max_return,
            'min_return_pct': min_return,
            'final_return_pct': final_return,
            'cost_adjusted_max_return_pct': cost_adjusted_max_return,
            'cost_adjusted_min_return_pct': cost_adjusted_min_return,
            'cost_adjusted_final_return_pct': cost_adjusted_final_return,
            'executable_return_pct': executable_return,
            'future_window_seconds': int(future_window),
            'is_moon': 1 if max_return >= 200.0 else 0,
            'is_executable_target': 1 if target_hit_before_stop else 0,
            'target_hit_before_stop': 1 if target_hit_before_stop else 0,
            'stop_hit_before_target': 1 if stop_hit_before_target else 0,
            'time_to_target_seconds': int(time_to_target_seconds),
            'time_to_stop_seconds': int(time_to_stop_seconds),
            'label_fee_bps': float(self.label_fee_bps),
            'label_slippage_bps': float(self.label_slippage_bps),
            'label_stop_loss_pct': float(self.label_stop_loss_pct),
            'label_target_return_pct': float(self.label_target_return_pct),
        }
        label.update(live_label)
        label.update(
            self._calculate_delay_robust_live_label(
                future_trades_sorted,
                sample_time=sample_time,
                future_window=future_window,
                current_price=current_price,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
                current_live_label=live_label,
            )
        )

        return label

    @staticmethod
    def _trade_timestamp(trade: Dict) -> int:
        return int(trade.get('timestamp', 0) or 0)

    @staticmethod
    def _trade_price(trade: Dict) -> float:
        return float(trade.get('price', 0.0) or 0.0)

    @classmethod
    def _first_trade_at_or_after(cls, trades: List[Dict], due_time: int) -> Optional[Dict]:
        for trade in trades:
            if cls._trade_timestamp(trade) >= int(due_time):
                return trade
        return None

    @staticmethod
    def _trade_at_or_after_indexed(trades: List[Dict], timestamps: List[int], due_time: int) -> Optional[Dict]:
        index = bisect_left(timestamps, int(due_time))
        if index >= len(trades):
            return None
        return trades[index]

    def _calculate_live_execution_label(
        self,
        future_trades_sorted: List[Dict],
        *,
        sample_time: int,
        future_window: int,
        current_price: float,
        fee_rate: float,
        slippage_rate: float,
        entry_delay_seconds: Optional[int] = None,
    ) -> Dict:
        entry_delay = (
            self.label_entry_delay_seconds
            if entry_delay_seconds is None
            else max(0, int(entry_delay_seconds or 0))
        )
        future_timestamps = [self._trade_timestamp(trade) for trade in future_trades_sorted]
        entry_due_time = int(sample_time) + entry_delay
        if entry_delay == 0:
            entry_trade = {'timestamp': int(sample_time), 'price': float(current_price)}
        else:
            entry_trade = self._trade_at_or_after_indexed(future_trades_sorted, future_timestamps, entry_due_time)
        base = {
            'live_entry_delay_seconds': int(entry_delay),
            'live_exit_delay_seconds': int(self.label_exit_delay_seconds),
            'live_entry_available': 0,
            'live_entry_time': 0,
            'live_entry_wait_seconds': 0,
            'live_entry_price': 0.0,
            'live_entry_slippage_pct': 0.0,
            'live_entry_blocked_by_price_protection': 0,
            'live_cost_adjusted_max_return_pct': 0.0,
            'live_cost_adjusted_min_return_pct': 0.0,
            'live_cost_adjusted_final_return_pct': 0.0,
            'live_executable_return_pct': 0.0,
            'live_risk_adjusted_return_pct': 0.0,
            'live_target_hit_before_stop': 0,
            'live_stop_hit_before_target': 0,
            'live_time_to_target_seconds': 0,
            'live_time_to_stop_seconds': 0,
            'label_live_downside_penalty_weight': float(self.label_live_downside_penalty_weight),
            'label_fixed_stake_bnb': 0.0 if self.label_fixed_stake_bnb is None else float(self.label_fixed_stake_bnb),
            'label_entry_fixed_cost_bnb': float(self.label_entry_fixed_cost_bnb),
            'label_exit_fixed_cost_bnb': float(self.label_exit_fixed_cost_bnb),
            'label_entry_price_protection_pct': (
                0.0
                if self.label_entry_price_protection_pct is None
                else float(self.label_entry_price_protection_pct)
            ),
        }
        if entry_trade is None:
            return base

        entry_time = self._trade_timestamp(entry_trade)
        entry_raw_price = self._trade_price(entry_trade)
        entry_slippage_pct = (
            (entry_raw_price / float(current_price)) - 1.0
            if float(current_price or 0.0) > 0.0 and entry_raw_price > 0.0
            else 0.0
        )
        if (
            self.label_entry_price_protection_pct is not None
            and float(current_price or 0.0) > 0.0
            and entry_raw_price > float(current_price) * (1.0 + self.label_entry_price_protection_pct)
        ):
            base.update({
                'live_entry_time': int(entry_time),
                'live_entry_wait_seconds': int(entry_time - int(sample_time)),
                'live_entry_price': float(entry_raw_price),
                'live_entry_slippage_pct': float(entry_slippage_pct),
                'live_entry_blocked_by_price_protection': 1,
            })
            return base
        entry_effective_price = entry_raw_price * (1.0 + slippage_rate) / max(1e-12, 1.0 - fee_rate)
        if entry_effective_price <= 0.0:
            return base
        fixed_stake_bnb = max(1e-12, float(self.label_fixed_stake_bnb or 1.0))
        entry_fixed_cost_bnb = min(float(self.label_entry_fixed_cost_bnb), fixed_stake_bnb * 10.0)
        exit_fixed_cost_bnb = min(float(self.label_exit_fixed_cost_bnb), fixed_stake_bnb * 10.0)
        entry_total_cost_bnb = fixed_stake_bnb + entry_fixed_cost_bnb
        token_amount = fixed_stake_bnb / entry_effective_price

        live_returns = []
        best_return_before_stop = None
        target_hit_before_stop = False
        stop_hit_before_target = False
        time_to_target_seconds = 0
        time_to_stop_seconds = 0

        for index, candidate in enumerate(future_trades_sorted):
            candidate_time = future_timestamps[index]
            if candidate_time <= entry_time:
                continue
            due_time = candidate_time + self.label_exit_delay_seconds
            exit_trade = self._trade_at_or_after_indexed(future_trades_sorted, future_timestamps, due_time)
            if exit_trade is None:
                exit_trade = future_trades_sorted[-1]
            exit_price = self._trade_price(exit_trade)
            if exit_price <= 0.0:
                adjusted_return = 0.0
            else:
                exit_effective_price = exit_price * max(0.0, 1.0 - slippage_rate) * max(0.0, 1.0 - fee_rate)
                exit_value_bnb = max(0.0, (token_amount * exit_effective_price) - exit_fixed_cost_bnb)
                adjusted_return = ((exit_value_bnb - entry_total_cost_bnb) / entry_total_cost_bnb) * 100.0
            live_returns.append((exit_trade, adjusted_return))

            if best_return_before_stop is None or adjusted_return > best_return_before_stop:
                best_return_before_stop = adjusted_return
            if not target_hit_before_stop and adjusted_return >= self.label_target_return_pct:
                target_hit_before_stop = True
                time_to_target_seconds = int(self._trade_timestamp(exit_trade) - int(sample_time))
            if adjusted_return <= self.label_stop_loss_pct:
                if not target_hit_before_stop:
                    stop_hit_before_target = True
                time_to_stop_seconds = int(self._trade_timestamp(exit_trade) - int(sample_time))
                break

        returns_only = [value for _trade, value in live_returns]
        live_executable_return = float(best_return_before_stop) if best_return_before_stop is not None else 0.0
        live_min_return = float(min(returns_only)) if returns_only else 0.0
        live_risk_adjusted_return = live_executable_return + (
            self.label_live_downside_penalty_weight * min(0.0, live_min_return)
        )
        base.update(
            {
                'live_entry_available': 1,
                'live_entry_time': int(entry_time),
                'live_entry_wait_seconds': int(entry_time - int(sample_time)),
                'live_entry_price': float(entry_raw_price),
                'live_entry_slippage_pct': float(entry_slippage_pct),
                'live_cost_adjusted_max_return_pct': float(max(returns_only)) if returns_only else 0.0,
                'live_cost_adjusted_min_return_pct': live_min_return,
                'live_cost_adjusted_final_return_pct': float(returns_only[-1]) if returns_only else 0.0,
                'live_executable_return_pct': live_executable_return,
                'live_risk_adjusted_return_pct': float(live_risk_adjusted_return),
                'live_target_hit_before_stop': 1 if target_hit_before_stop else 0,
                'live_stop_hit_before_target': 1 if stop_hit_before_target else 0,
                'live_time_to_target_seconds': int(time_to_target_seconds),
                'live_time_to_stop_seconds': int(time_to_stop_seconds),
            }
        )
        return base

    def _calculate_delay_robust_live_label(
        self,
        future_trades_sorted: List[Dict],
        *,
        sample_time: int,
        future_window: int,
        current_price: float,
        fee_rate: float,
        slippage_rate: float,
        current_live_label: Dict,
    ) -> Dict:
        delays = list(self.label_delay_robust_entry_delay_seconds)
        if not delays:
            return {}

        delay_labels = []
        for delay in delays:
            if delay == self.label_entry_delay_seconds:
                delay_label = current_live_label
            else:
                delay_label = self._calculate_live_execution_label(
                    future_trades_sorted,
                    sample_time=sample_time,
                    future_window=future_window,
                    current_price=current_price,
                    fee_rate=fee_rate,
                    slippage_rate=slippage_rate,
                    entry_delay_seconds=delay,
                )
            delay_labels.append(delay_label)

        returns = [float(label.get('live_risk_adjusted_return_pct', 0.0) or 0.0) for label in delay_labels]
        current_return = float(current_live_label.get('live_risk_adjusted_return_pct', 0.0) or 0.0)
        min_return = min(returns) if returns else current_return
        avg_return = sum(returns) / len(returns) if returns else current_return
        min_weight = float(self.label_delay_robust_min_weight)
        robust_return = (current_return * (1.0 - min_weight)) + (float(min_return) * min_weight)

        return {
            'live_delay_robust_return_pct': float(robust_return),
            'live_delay_robust_min_return_pct': float(min_return),
            'live_delay_robust_avg_return_pct': float(avg_return),
            'live_delay_robust_current_return_pct': float(current_return),
            'live_delay_robust_available_count': int(
                sum(1 for label in delay_labels if int(label.get('live_entry_available', 0) or 0) == 1)
            ),
            'live_delay_robust_blocked_count': int(
                sum(1 for label in delay_labels if int(label.get('live_entry_blocked_by_price_protection', 0) or 0) == 1)
            ),
            'label_delay_robust_entry_delay_count': int(len(delays)),
            'label_delay_robust_min_entry_delay_seconds': int(min(delays)),
            'label_delay_robust_max_entry_delay_seconds': int(max(delays)),
            'label_delay_robust_min_weight': float(min_weight),
        }

    def _classify_return(self, return_pct: float) -> int:
        """
        将收益率分类

        返回:
            0: 亏损 (< 0%)
            1: 小赚 (0-50%)
            2: 中赚 (50-100%)
            3: 大赚 (100-300%)
            4: 暴赚 (>300%)
        """
        if return_pct < 0:
            return 0
        elif return_pct < 50:
            return 1
        elif return_pct < 100:
            return 2
        elif return_pct < 300:
            return 3
        else:
            return 4

    def split_dataset(self, train_ratio: float = 0.8, val_ratio: float = 0.1, test_ratio: float = 0.1):
        """
        划分数据集

        Returns:
            train_samples, val_samples, test_samples
        """
        assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 1e-6, "比例之和必须为1"

        if not self.samples:
            return [], [], []

        # 以 token 为单位做时序切分，避免同一 token 跨集合泄漏
        token_first_time = {}
        token_samples: Dict[str, List[Dict]] = {}

        for sample in self.samples:
            meta = sample.get('meta', {})
            token_address = str(meta.get('token_address', ''))
            sample_time = int(meta.get('sample_time', 0) or 0)

            if token_address not in token_first_time or sample_time < token_first_time[token_address]:
                token_first_time[token_address] = sample_time

            token_samples.setdefault(token_address, []).append(sample)

        ordered_tokens = sorted(token_samples.keys(), key=lambda token: token_first_time.get(token, 0))
        total_tokens = len(ordered_tokens)
        train_token_end = int(total_tokens * train_ratio)
        val_token_end = train_token_end + int(total_tokens * val_ratio)

        train_tokens = set(ordered_tokens[:train_token_end])
        val_tokens = set(ordered_tokens[train_token_end:val_token_end])
        test_tokens = set(ordered_tokens[val_token_end:])

        train = []
        val = []
        test = []

        for token_address in ordered_tokens:
            token_bucket = token_samples[token_address]
            if token_address in train_tokens:
                train.extend(token_bucket)
            elif token_address in val_tokens:
                val.extend(token_bucket)
            elif token_address in test_tokens:
                test.extend(token_bucket)

        # 保持每个分片内部按时间有序
        train.sort(key=lambda s: int(s.get('meta', {}).get('sample_time', 0) or 0))
        val.sort(key=lambda s: int(s.get('meta', {}).get('sample_time', 0) or 0))
        test.sort(key=lambda s: int(s.get('meta', {}).get('sample_time', 0) or 0))

        logger.info(f"Dataset split: train={len(train)}, val={len(val)}, test={len(test)}")

        return train, val, test

    def save_dataset(self, output_dir: str = "data/datasets"):
        """保存数据集"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        # 保存前做一次去重和数值清理
        self._deduplicate_samples()
        sanitized_samples = []
        for sample in self.samples:
            sanitized_samples.append({
                'features': self._sanitize_numeric_dict(sample.get('features', {})),
                'label': self._sanitize_numeric_dict(sample.get('label', {})),
                'meta': sample.get('meta', {}),
            })
        self.samples = sanitized_samples

        # 划分数据集
        train, val, test = self.split_dataset()

        # 保存
        def save_split(samples, name):
            filepath = output_path / f"{name}_{timestamp}.jsonl"
            with filepath.open('w', encoding='utf-8') as f:
                for sample in samples:
                    json.dump(sample, f, ensure_ascii=False)
                    f.write('\n')
            logger.info(f"Saved {len(samples)} samples to {filepath}")

        save_split(train, 'train')
        save_split(val, 'val')
        save_split(test, 'test')

        # 保存元数据
        meta_file = output_path / f"metadata_{timestamp}.json"
        metadata = {
            'timestamp': timestamp,
            'total_samples': len(self.samples),
            'train_samples': len(train),
            'val_samples': len(val),
            'test_samples': len(test),
            'feature_names': list(self.samples[0]['features'].keys()) if self.samples else [],
            'label_names': list(self.samples[0]['label'].keys()) if self.samples else [],
            'dataset_config': {
                'lifecycle_dir': str(self.lifecycle_dir),
                'sample_mode': self.sample_mode,
                'max_sample_age_seconds': self.max_sample_age_seconds,
                'max_samples_per_token': self.max_samples_per_token,
                'sample_intervals': self.sample_intervals,
                'future_windows': self.future_windows,
                'label_fee_bps': self.label_fee_bps,
                'label_slippage_bps': self.label_slippage_bps,
                'label_stop_loss_pct': self.label_stop_loss_pct,
                'label_target_return_pct': self.label_target_return_pct,
                'label_entry_delay_seconds': self.label_entry_delay_seconds,
                'label_exit_delay_seconds': self.label_exit_delay_seconds,
                'label_live_downside_penalty_weight': self.label_live_downside_penalty_weight,
                'label_delay_robust_entry_delay_seconds': self.label_delay_robust_entry_delay_seconds,
                'label_delay_robust_min_weight': self.label_delay_robust_min_weight,
                'label_fixed_stake_bnb': self.label_fixed_stake_bnb,
                'label_entry_fixed_cost_bnb': self.label_entry_fixed_cost_bnb,
                'label_exit_fixed_cost_bnb': self.label_exit_fixed_cost_bnb,
                'label_entry_price_protection_pct': self.label_entry_price_protection_pct,
                'include_flow_features': self.include_flow_features,
                'min_entry_unique_buyers': self.min_entry_unique_buyers,
                'min_entry_buy_count': self.min_entry_buy_count,
            },
        }

        with meta_file.open('w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        logger.info(f"Dataset saved to {output_dir}")

    def get_stats(self) -> Dict:
        """获取数据集统计"""
        if not self.samples:
            return {'total_samples': 0}

        return_classes = []
        profitable_count = 0

        for sample in self.samples:
            label = sample.get('label', {})
            return_value = None
            if 'executable_return_pct' in label:
                return_value = float(label.get('executable_return_pct', 0.0))
            elif 'max_return_pct' in label:
                return_value = float(label.get('max_return_pct', 0.0))

            if 'return_class' in label:
                ret_class = int(label['return_class'])
            elif return_value is not None:
                ret_class = self._classify_return(return_value)
            else:
                ret_class = 0
            return_classes.append(ret_class)

            if 'is_profitable' in label:
                is_profitable = bool(label['is_profitable'])
            elif return_value is not None:
                is_profitable = return_value >= 0.0
            else:
                is_profitable = False

            if is_profitable:
                profitable_count += 1

        class_counts = {i: return_classes.count(i) for i in range(5)}

        return {
            'total_samples': len(self.samples),
            'profitable_samples': profitable_count,
            'profitable_ratio': profitable_count / len(self.samples),
            'return_class_distribution': class_counts,
        }

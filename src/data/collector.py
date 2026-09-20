from src.data.fourmeme_quote import classify_quote
"""
数据收集器 - 整合事件数据并生成训练样本
"""

import json
import logging
from bisect import bisect_right
from typing import Dict, List, Optional
from pathlib import Path
from datetime import datetime

from src.data.feature_extractor import extract_features, resolve_current_price

logger = logging.getLogger(__name__)


class DataCollector:
    """收集和整合交易数据用于训练"""

    RUNTIME_STATE_VERSION = 2

    def __init__(self, output_dir: str = "data/training", incremental_run_id: Optional[str] = None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.incremental_run_id = incremental_run_id or datetime.now().strftime('%Y%m%d_%H%M%S')
        self.incremental_max_file_size_bytes = 128 * 1024 * 1024
        self.resume_incremental_file_limit = 8
        self.incremental_output_file = self._build_incremental_output_file()
        self.metadata_index_file = self.output_dir / "token_metadata.json"

        # 内存缓存: token_address -> 完整生命周期数据
        self.token_lifecycle: Dict[str, Dict] = {}

        # 轻量元信息缓存（支持刷盘后代币再次活跃时恢复跟踪）
        self.token_metadata: Dict[str, Dict] = {}
        self._metadata_dirty = False

        # 统计
        self.tokens_tracked = 0
        self.samples_generated = 0
        self.tokens_flushed = 0
        self.applied_cursor: Optional[Dict] = None
        self.last_processed_block = 0

    @staticmethod
    def _normalize_tx_hash(tx_hash: object) -> str:
        if isinstance(tx_hash, bytes):
            return tx_hash.hex()
        if isinstance(tx_hash, str):
            return tx_hash[2:] if tx_hash.startswith("0x") else tx_hash
        return ""

    @staticmethod
    def _normalize_int(value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _normalize_cursor(cls, cursor: Optional[Dict]) -> Optional[Dict]:
        if not cursor:
            return None

        block_number = cls._normalize_int(
            cursor.get("block_number", cursor.get("blockNumber")),
            default=-1,
        )
        if block_number < 0:
            return None

        log_index = cls._normalize_int(
            cursor.get("log_index", cursor.get("logIndex")),
            default=-1,
        )
        tx_hash = cls._normalize_tx_hash(
            cursor.get("tx_hash", cursor.get("transactionHash"))
        )

        return {
            "block_number": block_number,
            "log_index": log_index,
            "tx_hash": tx_hash,
        }

    @classmethod
    def _cursor_sort_key(cls, cursor: Optional[Dict]) -> tuple[int, int, str]:
        normalized = cls._normalize_cursor(cursor)
        if normalized is None:
            return (-1, -1, "")
        return (
            normalized["block_number"],
            normalized["log_index"],
            normalized["tx_hash"],
        )

    def _advance_applied_cursor(self, event_data: Dict) -> None:
        cursor = self._normalize_cursor(event_data)
        if cursor is None:
            return

        if self.applied_cursor is None or self._cursor_sort_key(cursor) > self._cursor_sort_key(self.applied_cursor):
            self.applied_cursor = cursor

    def get_applied_cursor(self) -> Optional[Dict]:
        if self.applied_cursor is None:
            return None
        return dict(self.applied_cursor)

    @classmethod
    def _extract_token_metadata_from_args(
        cls,
        token_address: str,
        args: Dict,
        event_data: Optional[Dict] = None,
    ) -> Dict:
        metadata = {
            'token': token_address,
            'creator': args.get('creator', ''),
            'name': args.get('name', ''),
            'symbol': args.get('symbol', ''),
            'totalSupply': args.get('totalSupply', 0),
            'launchFee': args.get('launchFee', 0),
            'launchTime': args.get('launchTime', 0),
        }

        if event_data:
            create_timestamp = cls._normalize_int(event_data.get('timestamp'), default=0)
            create_block = cls._normalize_int(event_data.get('blockNumber'), default=0)
            if create_timestamp > 0:
                metadata['createTimestamp'] = create_timestamp
            if create_block > 0:
                metadata['createBlock'] = create_block

        return metadata

    @classmethod
    def _extract_token_metadata_from_lifecycle(cls, lifecycle: Dict) -> Dict:
        token_address = lifecycle.get('token_address', '')
        metadata = cls._extract_token_metadata_from_args(
            token_address=token_address,
            args={
                'creator': lifecycle.get('creator', ''),
                'name': lifecycle.get('name', ''),
                'symbol': lifecycle.get('symbol', ''),
                'totalSupply': lifecycle.get('total_supply', 0),
                'launchFee': lifecycle.get('launch_fee', 0),
                'launchTime': lifecycle.get('launch_time', 0),
            },
        )
        create_timestamp = cls._normalize_int(lifecycle.get('create_timestamp'), default=0)
        create_block = cls._normalize_int(lifecycle.get('create_block'), default=0)
        if create_timestamp > 0:
            metadata['createTimestamp'] = create_timestamp
        if create_block > 0:
            metadata['createBlock'] = create_block
        return metadata

    def _store_token_metadata(self, token_address: str, metadata: Dict) -> None:
        if not token_address:
            return

        normalized_metadata = dict(metadata)
        normalized_metadata['token'] = token_address
        existing = self.token_metadata.get(token_address)
        if existing == normalized_metadata:
            return

        self.token_metadata[token_address] = normalized_metadata
        self._metadata_dirty = True

    def load_token_metadata_index(self) -> int:
        metadata_path = self.metadata_index_file
        if not metadata_path.exists():
            return 0

        try:
            with metadata_path.open('r', encoding='utf-8') as f:
                payload = json.load(f)
        except Exception as e:
            logger.error(f"Error loading token metadata index from {metadata_path}: {e}")
            return 0

        if isinstance(payload, dict) and 'tokens' in payload:
            raw_tokens = payload.get('tokens', {}) or {}
        elif isinstance(payload, dict):
            raw_tokens = payload
        else:
            logger.warning(f"Skipping token metadata index restore from {metadata_path}: invalid payload")
            return 0

        loaded = 0
        for token_address, metadata in raw_tokens.items():
            if not token_address or not isinstance(metadata, dict):
                continue
            self.token_metadata[token_address] = dict(metadata)
            self.token_metadata[token_address]['token'] = token_address
            loaded += 1

        self._metadata_dirty = False
        if loaded > 0:
            logger.info(f"Loaded token metadata index from {metadata_path}: tokens={loaded}")
        return loaded

    def save_token_metadata_index(self) -> Optional[Path]:
        if not self._metadata_dirty and self.metadata_index_file.exists():
            return self.metadata_index_file

        metadata_path = self.metadata_index_file
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = metadata_path.with_name(f"{metadata_path.name}.tmp")
        payload = {
            # Version 2 adds original create fields; v1 metadata remains loadable.
            'version': 2,
            'saved_at': datetime.now().isoformat(),
            'tokens': {
                token_address: dict(metadata)
                for token_address, metadata in sorted(self.token_metadata.items())
            },
        }

        try:
            with tmp_path.open('w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            tmp_path.replace(metadata_path)
            self._metadata_dirty = False
            return metadata_path
        except Exception as e:
            logger.error(f"Error saving token metadata index to {metadata_path}: {e}")
            return None

    def _build_incremental_output_file(self, part_index: int = 0) -> Path:
        suffix = "" if part_index == 0 else f"_part{part_index:03d}"
        return self.output_dir / f"lifecycle_incremental_{self.incremental_run_id}{suffix}.jsonl"

    def _rotate_incremental_output_file_if_needed(self) -> Path:
        output_file = self.incremental_output_file
        if not output_file.exists():
            return output_file

        try:
            current_size = output_file.stat().st_size
        except OSError:
            return output_file

        if current_size < self.incremental_max_file_size_bytes:
            return output_file

        part_index = 1
        while True:
            rotated_path = self._build_incremental_output_file(part_index)
            if not rotated_path.exists() or rotated_path.stat().st_size < self.incremental_max_file_size_bytes:
                self.incremental_output_file = rotated_path
                return self.incremental_output_file
            part_index += 1

    def _create_lifecycle_record(self, token_address: str, event_data: Dict, args: Dict) -> Dict:
        return {
            # 基本信息
            'token_address': token_address,
            'creator': args.get('creator', ''),
            'name': args.get('name', ''),
            'symbol': args.get('symbol', ''),
            'total_supply': float(args.get('totalSupply', 0)),
            'launch_fee': float(args.get('launchFee', 0)),
            'launch_time': args.get('launchTime', 0),
            'create_timestamp': event_data.get('timestamp', 0),
            'create_block': event_data.get('blockNumber', 0),

            # 交易数据
            'buys': [],  # [{timestamp, account, token_amount, bnb_amount, price}]
            'sells': [],

            # 价格历史
            'price_history': [],  # [{timestamp, price, type: buy/sell}]

            # 聚合统计
            'total_buy_volume_bnb': 0.0,
            'total_sell_volume_bnb': 0.0,
            'total_buy_count': 0,
            'total_sell_count': 0,
            'unique_buyers': set(),
            'unique_sellers': set(),

            # 时间窗口统计 (1min, 5min, 15min, 30min, 1h)
            'volume_1min': 0.0,
            'volume_5min': 0.0,
            'volume_15min': 0.0,
            'volume_30min': 0.0,
            'volume_1h': 0.0,

            # 价格指标
            'price_max': 0.0,
            'price_min': float('inf'),
            'price_current': 0.0,
            'price_first': 0.0,

            # 毕业状态
            'graduated': False,
            'graduate_time': None,

            # 更新时间
            'last_update': event_data.get('timestamp', 0),
            'last_update_local': datetime.now().timestamp(),
        }

    @staticmethod
    def _lifecycle_activity_count(lifecycle: Dict) -> int:
        return len(lifecycle.get('buys', [])) + len(lifecycle.get('sells', []))

    @classmethod
    def _event_sort_key(cls, event: Dict) -> tuple[int, int, int, str]:
        """Order an event by chain time and provenance when available."""
        timestamp = cls._normalize_int(event.get('timestamp'), default=0)
        block_number = cls._normalize_int(
            event.get('block_number', event.get('blockNumber')),
            default=-1,
        )
        log_index = cls._normalize_int(
            event.get('log_index', event.get('logIndex')),
            default=-1,
        )
        tx_hash = cls._normalize_tx_hash(
            event.get('transaction_hash', event.get('transactionHash'))
        )
        return timestamp, block_number, log_index, tx_hash

    @classmethod
    def _insert_ordered_event(cls, events: List[Dict], event: Dict) -> None:
        """Insert a live event without relying on arrival order."""
        key = cls._event_sort_key(event)
        keys = [cls._event_sort_key(item) for item in events]
        events.insert(bisect_right(keys, key), event)

    @classmethod
    def _normalize_event_sequences(cls, lifecycle: Dict) -> None:
        """Sort persisted sequences and repair the chain-side last update."""
        chain_timestamps = []
        for key in ('buys', 'sells', 'price_history'):
            values = [item for item in lifecycle.get(key, []) if isinstance(item, dict)]
            values.sort(key=cls._event_sort_key)
            lifecycle[key] = values
            chain_timestamps.extend(
                cls._normalize_int(item.get('timestamp'), default=0)
                for item in values
                if cls._normalize_int(item.get('timestamp'), default=0) > 0
            )
        create_timestamp = cls._normalize_int(lifecycle.get('create_timestamp'), default=0)
        previous_update = cls._normalize_int(lifecycle.get('last_update'), default=0)
        if create_timestamp > 0:
            chain_timestamps.append(create_timestamp)
        if previous_update > 0:
            chain_timestamps.append(previous_update)
        if chain_timestamps:
            lifecycle['last_update'] = max(chain_timestamps)

    @classmethod
    def _refresh_price_state(cls, lifecycle: Dict) -> None:
        """Derive current/first/high/low prices from chronological trades."""
        buys = [item for item in lifecycle.get('buys', []) if float(item.get('price', 0.0) or 0.0) > 0.0]
        sells = [item for item in lifecycle.get('sells', []) if float(item.get('price', 0.0) or 0.0) > 0.0]
        trades = sorted(buys + sells, key=cls._event_sort_key)
        if not trades:
            lifecycle['price_current'] = 0.0
            lifecycle['price_first'] = 0.0
            lifecycle['price_max'] = 0.0
            lifecycle['price_min'] = 0.0
            return
        prices = [float(item.get('price', 0.0) or 0.0) for item in trades]
        lifecycle['price_current'] = prices[-1]
        lifecycle['price_first'] = float(buys[0].get('price', 0.0) or 0.0) if buys else prices[0]
        lifecycle['price_max'] = max(prices)
        lifecycle['price_min'] = min(prices)

    @classmethod
    def _event_provenance(cls, event_data: Dict) -> Dict:
        """Keep block/log identity so same-second swaps remain distinguishable."""
        provenance = {
            'block_number': cls._normalize_int(event_data.get('blockNumber'), default=-1),
            'log_index': cls._normalize_int(event_data.get('logIndex'), default=-1),
            'transaction_hash': cls._normalize_tx_hash(event_data.get('transactionHash')),
        }
        received_at = event_data.get('received_at')
        if received_at is not None:
            try:
                provenance['received_at'] = float(received_at)
            except (TypeError, ValueError):
                pass
        return provenance

    @classmethod
    def _should_replace_lifecycle(cls, existing: Dict, incoming: Dict) -> bool:
        existing_activity = cls._lifecycle_activity_count(existing)
        incoming_activity = cls._lifecycle_activity_count(incoming)
        if incoming_activity != existing_activity:
            return incoming_activity > existing_activity

        existing_last_update = int(existing.get('last_update', 0) or 0)
        incoming_last_update = int(incoming.get('last_update', 0) or 0)
        return incoming_last_update >= existing_last_update

    def _normalize_persisted_lifecycle(self, lifecycle: Dict) -> Dict:
        """Normalize on-disk lifecycle records to the collector's in-memory schema."""
        if 'created_at' in lifecycle and 'buys' not in lifecycle:
            norm = lifecycle.copy()
            norm['create_timestamp'] = lifecycle.get('created_at', 0)
            norm['create_block'] = lifecycle.get('create_block', 0)
            norm['buys'] = []
            norm['sells'] = []
            norm['price_history'] = []
            norm['total_buy_volume_bnb'] = 0.0
            norm['total_sell_volume_bnb'] = 0.0
            norm['total_buy_count'] = 0
            norm['total_sell_count'] = 0
            norm['unique_buyers'] = []
            norm['unique_sellers'] = []
            norm['volume_1min'] = 0.0
            norm['volume_5min'] = 0.0
            norm['volume_15min'] = 0.0
            norm['volume_30min'] = 0.0
            norm['volume_1h'] = 0.0
            norm['price_max'] = 0.0
            norm['price_min'] = float('inf')
            norm['price_current'] = 0.0
            norm['price_first'] = 0.0
            norm['graduated'] = lifecycle.get('graduated', False)
            norm['graduate_time'] = lifecycle.get('graduate_time')
            norm['last_update'] = norm['create_timestamp']
            norm['total_supply'] = float(lifecycle.get('total_supply', 0)) * 1e18
            norm['launch_fee'] = float(lifecycle.get('launch_fee', 0)) * 1e18

            for purchase in lifecycle.get('purchases', []):
                token_amount = float(purchase.get('token_amount', 0))
                bnb_amount = float(purchase.get('ether_amount', 0))
                price = (bnb_amount / token_amount) if token_amount > 0 else 0.0
                normalized_purchase = {
                    'timestamp': int(purchase.get('timestamp', 0) or 0),
                    'account': purchase.get('account', ''),
                    'token_amount': token_amount,
                    'bnb_amount': bnb_amount,
                    'price': price,
                }
                norm['buys'].append(normalized_purchase)
                norm['price_history'].append({
                    'timestamp': normalized_purchase['timestamp'],
                    'price': price,
                    'type': 'buy',
                })

            for sale in lifecycle.get('sales', []):
                token_amount = float(sale.get('token_amount', 0))
                bnb_amount = float(sale.get('ether_amount', 0))
                price = (bnb_amount / token_amount) if token_amount > 0 else 0.0
                normalized_sale = {
                    'timestamp': int(sale.get('timestamp', 0) or 0),
                    'account': sale.get('account', ''),
                    'token_amount': token_amount,
                    'bnb_amount': bnb_amount,
                    'price': price,
                }
                norm['sells'].append(normalized_sale)
                norm['price_history'].append({
                    'timestamp': normalized_sale['timestamp'],
                    'price': price,
                    'type': 'sell',
                })

            if norm['buys']:
                prices = [buy['price'] for buy in norm['buys']]
                norm['total_buy_volume_bnb'] = sum(buy['bnb_amount'] for buy in norm['buys'])
                norm['total_buy_count'] = len(norm['buys'])
                norm['unique_buyers'] = sorted({buy['account'] for buy in norm['buys'] if buy.get('account')})
                norm['price_first'] = prices[0]
                norm['price_max'] = max(prices)
                norm['price_min'] = min(prices)
                norm['price_current'] = prices[-1]

            if norm['sells']:
                sell_prices = [sale['price'] for sale in norm['sells']]
                norm['total_sell_volume_bnb'] = sum(sale['bnb_amount'] for sale in norm['sells'])
                norm['total_sell_count'] = len(norm['sells'])
                norm['unique_sellers'] = sorted({sale['account'] for sale in norm['sells'] if sale.get('account')})
                norm['price_max'] = max(norm['price_max'], max(sell_prices))
                if norm['price_min'] == float('inf'):
                    norm['price_min'] = min(sell_prices)
                else:
                    norm['price_min'] = min(norm['price_min'], min(sell_prices))
                norm['price_current'] = sell_prices[-1]

            timestamps = [
                int(item.get('timestamp', 0) or 0)
                for item in (norm['buys'] + norm['sells'])
                if int(item.get('timestamp', 0) or 0) > 0
            ]
            if timestamps:
                norm['last_update'] = max(timestamps)

            if norm['price_min'] == float('inf'):
                norm['price_min'] = 0.0

            self._normalize_event_sequences(norm)
            self._refresh_price_state(norm)
            return norm

        norm = lifecycle.copy()
        norm.setdefault('create_timestamp', norm.get('created_at', 0))
        norm.setdefault('create_block', 0)
        norm.setdefault('buys', [])
        norm.setdefault('sells', [])
        norm.setdefault('price_history', [])
        norm.setdefault('total_buy_volume_bnb', 0.0)
        norm.setdefault('total_sell_volume_bnb', 0.0)
        norm.setdefault('total_buy_count', len(norm['buys']))
        norm.setdefault('total_sell_count', len(norm['sells']))
        norm.setdefault('unique_buyers', [])
        norm.setdefault('unique_sellers', [])
        norm.setdefault('volume_1min', 0.0)
        norm.setdefault('volume_5min', 0.0)
        norm.setdefault('volume_15min', 0.0)
        norm.setdefault('volume_30min', 0.0)
        norm.setdefault('volume_1h', 0.0)
        norm.setdefault('price_max', 0.0)
        norm.setdefault('price_min', 0.0)
        norm.setdefault('price_current', 0.0)
        norm.setdefault('price_first', 0.0)
        norm.setdefault('graduated', False)
        norm.setdefault('graduate_time', None)
        if 'last_update' not in norm:
            timestamps = [
                int(item.get('timestamp', 0) or 0)
                for item in (norm['buys'] + norm['sells'])
                if int(item.get('timestamp', 0) or 0) > 0
            ]
            norm['last_update'] = max(timestamps) if timestamps else int(norm.get('create_timestamp', 0) or 0)
        self._normalize_event_sequences(norm)
        self._refresh_price_state(norm)
        return norm

    def _deserialize_lifecycle(self, lifecycle: Dict) -> Dict:
        restored = self._normalize_persisted_lifecycle(lifecycle)
        restored['unique_buyers'] = set(restored.get('unique_buyers', []))
        restored['unique_sellers'] = set(restored.get('unique_sellers', []))
        return restored

    def _select_resume_lifecycle_files(self) -> List[Path]:
        incremental_files = sorted(self.output_dir.glob("lifecycle_incremental_*.jsonl"))
        snapshot_files = sorted(self.output_dir.glob("lifecycle_[0-9]*.jsonl"))

        if incremental_files:
            if self.resume_incremental_file_limit > 0:
                incremental_files = incremental_files[-self.resume_incremental_file_limit:]
            selected_files = incremental_files.copy()
            if snapshot_files:
                selected_files.append(snapshot_files[-1])
            return selected_files

        return snapshot_files[-1:] if snapshot_files else []

    def load_token_metadata_from_lifecycle_files(self) -> int:
        """Bootstrap token metadata from persisted lifecycle files for restart recovery."""
        lifecycle_files = self._select_resume_lifecycle_files()
        if not lifecycle_files:
            return 0

        loaded_tokens: set[str] = set()
        for filepath in lifecycle_files:
            try:
                with filepath.open('r', encoding='utf-8') as f:
                    for line in f:
                        if not line.strip():
                            continue

                        payload = json.loads(line)
                        token_address = payload.get('token_address')
                        if not token_address:
                            continue

                        metadata = self._extract_token_metadata_from_lifecycle(payload)
                        self._store_token_metadata(token_address, metadata)
                        loaded_tokens.add(token_address)
            except Exception as e:
                logger.error(f"Error loading lifecycle metadata from {filepath}: {e}")

        if loaded_tokens:
            logger.info(
                f"Bootstrapped metadata for {len(loaded_tokens)} tokens from "
                f"{len(lifecycle_files)} lifecycle files"
            )

        return len(loaded_tokens)

    def restore_runtime_state(self, state_file: Path) -> Optional[Dict]:
        """Restore active in-memory lifecycles and applied cursor from a checkpoint file."""
        state_path = Path(state_file)
        if not state_path.exists():
            return None

        try:
            with state_path.open('r', encoding='utf-8') as f:
                state = json.load(f)
        except Exception as e:
            logger.error(f"Error restoring runtime state from {state_path}: {e}")
            return None

        version = self._normalize_int(state.get('version'), default=0)
        if version not in {1, self.RUNTIME_STATE_VERSION}:
            logger.warning(
                f"Skipping runtime state restore from {state_path}: "
                f"unsupported version {state.get('version')}"
            )
            return None

        self.tokens_tracked = max(self.tokens_tracked, int(state.get('tokens_tracked', 0) or 0))
        self.tokens_flushed = max(self.tokens_flushed, int(state.get('tokens_flushed', 0) or 0))
        self.last_processed_block = max(0, int(state.get('last_processed_block', 0) or 0))

        restored_tokens = 0
        for lifecycle_payload in state.get('active_lifecycles', []):
            lifecycle = self._deserialize_lifecycle(lifecycle_payload)
            token_address = lifecycle.get('token_address')
            if not token_address:
                continue

            existing = self.token_lifecycle.get(token_address)
            if existing is None or self._should_replace_lifecycle(existing, lifecycle):
                self.token_lifecycle[token_address] = lifecycle

            self._store_token_metadata(token_address, self._extract_token_metadata_from_lifecycle(lifecycle))
            restored_tokens += 1

        resume_cursor = None
        if version == self.RUNTIME_STATE_VERSION:
            resume_cursor = self._normalize_cursor(state.get('applied_cursor'))
            self.applied_cursor = resume_cursor
            if self.last_processed_block <= 0 and resume_cursor is not None:
                self.last_processed_block = int(resume_cursor.get('block_number', 0) or 0)
            if self.last_processed_block > 0:
                if resume_cursor is None or self.last_processed_block > int(resume_cursor.get('block_number', -1)):
                    resume_cursor = {
                        'block_number': self.last_processed_block,
                        'log_index': -1,
                        'tx_hash': '',
                    }
        else:
            logger.warning(
                f"Restored legacy runtime state from {state_path} without resumable applied_cursor; "
                "listener will continue from current chain head"
            )

        self.save_token_metadata_index()

        logger.info(
            f"Restored runtime state from {state_path}: "
            f"active_tokens={restored_tokens}, resume_cursor={resume_cursor}, applied_cursor={self.applied_cursor}"
        )
        return dict(resume_cursor) if resume_cursor is not None else None

    def save_runtime_state(
        self,
        state_file: Path,
        applied_cursor: Optional[Dict] = None,
        last_processed_block: Optional[int] = None,
    ) -> Optional[Path]:
        """Persist active in-memory lifecycles and collector checkpoint atomically."""
        state_path = Path(state_file)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = state_path.with_name(f"{state_path.name}.tmp")
        normalized_cursor = self._normalize_cursor(applied_cursor) or self.get_applied_cursor()
        normalized_last_processed_block = max(
            self.last_processed_block,
            int(last_processed_block or 0),
            int((normalized_cursor or {}).get('block_number', 0) or 0),
        )
        self.last_processed_block = normalized_last_processed_block

        payload = {
            'version': self.RUNTIME_STATE_VERSION,
            'saved_at': datetime.now().isoformat(),
            'tokens_tracked': int(self.tokens_tracked),
            'tokens_flushed': int(self.tokens_flushed),
            'last_processed_block': int(normalized_last_processed_block),
            'applied_cursor': normalized_cursor,
            'active_lifecycles': [
                self._serialize_lifecycle(lifecycle)
                for lifecycle in self.token_lifecycle.values()
            ],
        }

        try:
            with tmp_path.open('w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            tmp_path.replace(state_path)
            return state_path
        except Exception as e:
            logger.error(f"Error saving runtime state to {state_path}: {e}")
            return None

    def _seed_lifecycle_from_metadata(self, token_address: str, event_data: Dict) -> bool:
        metadata = self.token_metadata.get(token_address)
        if not metadata:
            return False

        self._store_token_metadata(token_address, metadata)
        lifecycle = self._create_lifecycle_record(
            token_address=token_address,
            event_data=event_data,
            args=metadata,
        )
        # Rehydration starts a fresh activity segment, but age gates must use
        # the original token creation point rather than the reactivation event.
        create_timestamp = self._normalize_int(metadata.get('createTimestamp'), default=0)
        if create_timestamp <= 0:
            create_timestamp = self._normalize_int(metadata.get('create_timestamp'), default=0)
        if create_timestamp <= 0:
            create_timestamp = self._normalize_int(metadata.get('launchTime'), default=0)
        if create_timestamp > 0:
            lifecycle['create_timestamp'] = create_timestamp

        create_block = self._normalize_int(metadata.get('createBlock'), default=0)
        if create_block <= 0:
            create_block = self._normalize_int(metadata.get('create_block'), default=0)
        if create_block > 0:
            lifecycle['create_block'] = create_block

        self.token_lifecycle[token_address] = lifecycle
        logger.debug(f"Rehydrated token from metadata: {metadata.get('symbol', 'Unknown')} ({token_address[:10]}...)")
        return True

    def on_token_create(self, event_data: Dict) -> bool:
        """处理TokenCreate事件"""
        try:
            args = event_data.get('args', {})
            token_address = args.get('token', '')

            if not token_address:
                return False

            # 记录轻量元信息，支持后续重新跟踪
            self._store_token_metadata(
                token_address,
                self._extract_token_metadata_from_args(token_address, args, event_data=event_data),
            )

            # 初始化代币生命周期数据
            self.token_lifecycle[token_address] = self._create_lifecycle_record(
                token_address=token_address,
                event_data=event_data,
                args=args,
            )

            self.tokens_tracked += 1
            self._advance_applied_cursor(event_data)
            logger.debug(f"Tracking new token: {args.get('symbol', 'Unknown')} ({token_address[:10]}...)")
            return True

        except Exception as e:
            logger.error(f"Error in on_token_create: {e}")
            return False

    def on_token_purchase(self, event_data: Dict) -> bool:
        """处理TokenPurchase事件"""
        try:
            args = event_data.get('args', {})
            token_address = args.get('token', '')

            if token_address not in self.token_lifecycle:
                if not self._seed_lifecycle_from_metadata(token_address, event_data):
                    return False

            lifecycle = self.token_lifecycle[token_address]
            timestamp = event_data.get('timestamp', 0)

            # 提取交易数据
            account = args.get('account', '')
            token_amount = float(args.get('amount', 0))
            bnb_amount = float(args.get('cost', 0))

            if token_amount > 0:
                price = (bnb_amount / 1e18) / (token_amount / 1e18)

                # 记录买入，按链上顺序插入以容忍重试/恢复乱序。
                buy = {
                    'timestamp': timestamp,
                    'account': account,
                    'token_amount': token_amount / 1e18,
                    'bnb_amount': bnb_amount / 1e18,
                    'price': price
                }
                buy.update(self._event_provenance(event_data))
                self._insert_ordered_event(lifecycle['buys'], buy)

                # 更新价格历史
                price_point = {
                    'timestamp': timestamp,
                    'price': price,
                    'type': 'buy'
                }
                price_point.update(self._event_provenance(event_data))
                self._insert_ordered_event(lifecycle['price_history'], price_point)

                # 更新统计
                lifecycle['total_buy_volume_bnb'] += bnb_amount / 1e18
                lifecycle['total_buy_count'] += 1
                lifecycle['unique_buyers'].add(account)

                # 更新价格指标
                lifecycle['price_current'] = price
                lifecycle['price_current_source'] = 'event'
                lifecycle['price_current_local_update'] = datetime.now().timestamp()
                lifecycle['price_max'] = max(lifecycle['price_max'], price)
                lifecycle['price_min'] = min(lifecycle['price_min'], price)
                if lifecycle['price_first'] == 0:
                    lifecycle['price_first'] = price

                lifecycle['last_update'] = max(
                    int(lifecycle.get('last_update', 0) or 0),
                    self._normalize_int(timestamp, default=0),
                )
                lifecycle['last_update_local'] = datetime.now().timestamp()

                # 更新时间窗口统计
                self._refresh_price_state(lifecycle)
                self._update_time_window_stats(lifecycle, lifecycle['last_update'], bnb_amount / 1e18)
                self._advance_applied_cursor(event_data)
                return True

            return False

        except Exception as e:
            logger.error(f"Error in on_token_purchase: {e}")
            return False

    def on_token_sale(self, event_data: Dict) -> bool:
        """处理TokenSale事件"""
        try:
            args = event_data.get('args', {})
            token_address = args.get('token', '')

            if token_address not in self.token_lifecycle:
                if not self._seed_lifecycle_from_metadata(token_address, event_data):
                    return False

            lifecycle = self.token_lifecycle[token_address]
            timestamp = event_data.get('timestamp', 0)

            # 提取交易数据
            account = args.get('account', '')
            token_amount = float(args.get('amount', 0))
            bnb_amount = float(args.get('cost', 0))

            if token_amount > 0:
                price = (bnb_amount / 1e18) / (token_amount / 1e18)

                # 记录卖出，按链上顺序插入以容忍重试/恢复乱序。
                sale = {
                    'timestamp': timestamp,
                    'account': account,
                    'token_amount': token_amount / 1e18,
                    'bnb_amount': bnb_amount / 1e18,
                    'price': price
                }
                sale.update(self._event_provenance(event_data))
                self._insert_ordered_event(lifecycle['sells'], sale)

                # 更新价格历史
                price_point = {
                    'timestamp': timestamp,
                    'price': price,
                    'type': 'sell'
                }
                price_point.update(self._event_provenance(event_data))
                self._insert_ordered_event(lifecycle['price_history'], price_point)

                # 更新统计
                lifecycle['total_sell_volume_bnb'] += bnb_amount / 1e18
                lifecycle['total_sell_count'] += 1
                lifecycle['unique_sellers'].add(account)

                # 更新价格指标
                lifecycle['price_current'] = price
                lifecycle['price_current_source'] = 'event'
                lifecycle['price_current_local_update'] = datetime.now().timestamp()
                lifecycle['price_max'] = max(lifecycle['price_max'], price)
                lifecycle['price_min'] = min(lifecycle['price_min'], price)

                lifecycle['last_update'] = max(
                    int(lifecycle.get('last_update', 0) or 0),
                    self._normalize_int(timestamp, default=0),
                )
                lifecycle['last_update_local'] = datetime.now().timestamp()

                # 更新时间窗口统计
                self._refresh_price_state(lifecycle)
                self._update_time_window_stats(lifecycle, lifecycle['last_update'], bnb_amount / 1e18)
                self._advance_applied_cursor(event_data)
                return True

            return False

        except Exception as e:
            logger.error(f"Error in on_token_sale: {e}")
            return False

    def on_trade_stop(self, event_data: Dict) -> bool:
        """处理TradeStop事件 (代币毕业)"""
        try:
            args = event_data.get('args', {})
            # LiquidityAdded carries the token as `base`; TradeStop carries it as
            # `token`. Both are graduation signals and must never be read as trades.
            token_address = args.get('token') or args.get('base') or ''

            if token_address not in self.token_lifecycle:
                if not self._seed_lifecycle_from_metadata(token_address, event_data):
                    return False

            lifecycle = self.token_lifecycle[token_address]
            lifecycle['graduated'] = True
            lifecycle['graduate_time'] = event_data.get('timestamp', 0)
            quote = args.get('quote')
            if quote:
                lifecycle['quote_asset'] = str(quote)
                lifecycle['quote_symbol'] = classify_quote(quote)
            lifecycle['last_update'] = max(
                int(lifecycle.get('last_update', 0) or 0),
                self._normalize_int(event_data.get('timestamp'), default=0),
            )
            lifecycle['last_update_local'] = datetime.now().timestamp()

            logger.info(f"Token graduated: {lifecycle['symbol']} ({token_address[:10]}...)")
            self._advance_applied_cursor(event_data)
            return True

        except Exception as e:
            logger.error(f"Error in on_trade_stop: {e}")
            return False

    def _update_time_window_stats(self, lifecycle: Dict, current_time: int, volume: float):
        """更新时间窗口统计"""
        # 清理过期的交易记录并计算窗口成交量
        windows = {
            'volume_1min': 60,
            'volume_5min': 300,
            'volume_15min': 900,
            'volume_30min': 1800,
            'volume_1h': 3600
        }

        for window_key, seconds in windows.items():
            cutoff_time = current_time - seconds

            # 计算窗口内的买入成交量
            window_volume = sum(
                buy['bnb_amount'] for buy in lifecycle['buys']
                if buy['timestamp'] >= cutoff_time
            )

            lifecycle[window_key] = window_volume

    def generate_training_sample(self, token_address: str,
                                  sample_time: int,
                                  future_window_seconds: int = 300,
                                  include_flow_features: bool = False) -> Optional[Dict]:
        """
        生成训练样本

        Args:
            token_address: 代币地址
            sample_time: 采样时间点 (用于计算特征)
            future_window_seconds: 未来窗口 (用于计算标签) 默认5分钟
            include_flow_features: 是否包含短窗口卖压/净流量特征

        Returns:
            训练样本 {features: {...}, label: {...}}
        """
        if token_address not in self.token_lifecycle:
            return None

        lifecycle = self.token_lifecycle[token_address]

        # 只使用 sample_time 之前的数据计算特征
        past_buys = [b for b in lifecycle['buys'] if b['timestamp'] <= sample_time]
        past_sells = [s for s in lifecycle['sells'] if s['timestamp'] <= sample_time]

        if not past_buys:
            return None  # 没有历史数据

        # 计算未来收益 (标签)
        future_end_time = sample_time + future_window_seconds
        future_prices = [p['price'] for p in lifecycle['price_history']
                        if sample_time < p['timestamp'] <= future_end_time]

        current_price = resolve_current_price(past_buys, past_sells)  # 当前价格（最后一笔成交）
        if current_price <= 0:
            return None

        if future_prices:
            max_future_price = max(future_prices)
            min_future_price = min(future_prices)
            max_return = ((max_future_price - current_price) / current_price) * 100
            min_return = ((min_future_price - current_price) / current_price) * 100
        else:
            # 未来没有价格数据,可能代币没交易了
            max_return = 0
            min_return = 0

        # 计算特征
        features = self._extract_features(
            lifecycle,
            past_buys,
            past_sells,
            sample_time,
            include_flow_features=include_flow_features,
        )

        # 标签
        label = {
            'max_return_pct': max_return,
            'min_return_pct': min_return,
            'profitable': max_return > 10,  # 10%以上算盈利
            'high_return': max_return > 50,  # 50%以上算高收益
            'stop_loss': min_return < -30,  # -30%以下算需要止损
        }

        return {
            'features': features,
            'label': label,
            'meta': {
                'token_address': token_address,
                'symbol': lifecycle['symbol'],
                'sample_time': sample_time,
                'current_price': current_price,
                'flow_event_count_10s': self._recent_event_count(past_buys, sample_time, 10) + self._recent_event_count(past_sells, sample_time, 10),
                'flow_event_count_30s': self._recent_event_count(past_buys, sample_time, 30) + self._recent_event_count(past_sells, sample_time, 30),
                'flow_event_count_60s': self._recent_event_count(past_buys, sample_time, 60) + self._recent_event_count(past_sells, sample_time, 60),
            }
        }

    @staticmethod
    def _recent_event_count(rows: List[Dict], sample_time: int, window_seconds: int) -> int:
        upper = int(sample_time)
        cutoff = int(sample_time) - int(window_seconds)
        return sum(1 for row in rows if cutoff <= int(row.get('timestamp', 0)) <= upper)

    def _extract_features(self, lifecycle: Dict,
                          past_buys: List[Dict],
                          past_sells: List[Dict],
                          sample_time: int,
                          future_window: int = 300,
                          include_flow_features: bool = False) -> Dict:
        """提取特征 (增强版 - 与 DatasetBuilder 保持一致)"""
        return extract_features(
            lifecycle=lifecycle,
            past_buys=past_buys,
            past_sells=past_sells,
            sample_time=sample_time,
            include_flow_features=include_flow_features,
        )

    def _serialize_lifecycle(self, lifecycle: Dict) -> Dict:
        """将生命周期数据转换为可JSON序列化结构"""
        lifecycle_copy = lifecycle.copy()
        self._normalize_event_sequences(lifecycle_copy)
        self._refresh_price_state(lifecycle_copy)
        lifecycle_copy['unique_buyers'] = sorted(lifecycle['unique_buyers'])
        lifecycle_copy['unique_sellers'] = sorted(lifecycle['unique_sellers'])
        return lifecycle_copy

    def _append_lifecycles_to_file(self, output_file: Path, lifecycles: List[Dict]) -> int:
        """将生命周期数据追加写入JSONL文件"""
        if not lifecycles:
            return 0

        current_output = output_file
        written = 0
        for lifecycle in lifecycles:
            current_output = self._rotate_incremental_output_file_if_needed()
            with current_output.open('a', encoding='utf-8') as f:
                json.dump(self._serialize_lifecycle(lifecycle), f, ensure_ascii=False)
                f.write('\n')
            written += 1

        return written

    def flush_eligible_tokens(self, current_time: int, min_age_seconds: int, inactivity_seconds: int) -> int:
        """刷盘并移除满足条件的代币，降低内存占用"""
        try:
            flush_candidates: List[str] = []
            for token_address, lifecycle in self.token_lifecycle.items():
                create_timestamp = int(lifecycle.get('create_timestamp', 0) or 0)
                last_update = int(lifecycle.get('last_update', 0) or 0)
                if not create_timestamp or not last_update:
                    continue

                token_age = current_time - create_timestamp
                inactivity = current_time - last_update
                if token_age >= min_age_seconds and inactivity >= inactivity_seconds:
                    flush_candidates.append(token_address)

            if not flush_candidates:
                return 0

            lifecycles_to_flush = [self.token_lifecycle[token_address] for token_address in flush_candidates]
            flushed_count = self._append_lifecycles_to_file(self.incremental_output_file, lifecycles_to_flush)

            for token_address in flush_candidates:
                lifecycle = self.token_lifecycle.get(token_address)
                if lifecycle:
                    self._store_token_metadata(token_address, self._extract_token_metadata_from_lifecycle(lifecycle))
                self.token_lifecycle.pop(token_address, None)

            self.tokens_flushed += flushed_count
            return flushed_count

        except Exception as e:
            logger.error(f"Error flushing eligible tokens: {e}")
            return 0

    def flush_all_to_incremental(self) -> int:
        """将当前内存中的所有代币刷入增量文件并清空内存"""
        lifecycles = list(self.token_lifecycle.values())
        flushed_count = self._append_lifecycles_to_file(self.incremental_output_file, lifecycles)
        if flushed_count > 0:
            for lifecycle in lifecycles:
                token_address = lifecycle.get('token_address')
                if not token_address:
                    continue
                self._store_token_metadata(token_address, self._extract_token_metadata_from_lifecycle(lifecycle))
            self.token_lifecycle.clear()
            self.tokens_flushed += flushed_count
        return flushed_count

    def save_lifecycle_data(self):
        """保存所有代币生命周期数据（快照，不清内存）"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = self.output_dir / f"lifecycle_{timestamp}.jsonl"

            saved_count = 0
            with output_file.open('w', encoding='utf-8') as f:
                for lifecycle in self.token_lifecycle.values():
                    json.dump(self._serialize_lifecycle(lifecycle), f, ensure_ascii=False)
                    f.write('\n')
                    saved_count += 1

            logger.info(f"Saved {saved_count} token lifecycles to {output_file}")
            return output_file

        except Exception as e:
            logger.error(f"Error saving lifecycle data: {e}")
            return None

    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            'tokens_tracked': self.tokens_tracked,
            'tokens_in_memory': len(self.token_lifecycle),
            'samples_generated': self.samples_generated,
            'tokens_flushed': self.tokens_flushed,
            'incremental_output_file': str(self.incremental_output_file),
            'incremental_max_file_size_bytes': self.incremental_max_file_size_bytes,
            'resume_incremental_file_limit': self.resume_incremental_file_limit,
            'applied_cursor': self.get_applied_cursor(),
        }

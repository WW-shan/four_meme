"""
FourMeme Event Listener
Monitors and processes FourMeme platform events on BSC
"""

import asyncio
import logging
import re
import time
from typing import Dict, Set, Callable, Any, List, Optional
from web3 import AsyncWeb3
from web3.contract import AsyncContract
from web3.middleware import ExtraDataToPOAMiddleware
from config.config import Config
from src.data.fourmeme_log_decoder import (
    AUXILIARY_TRADE_EVENTS,
    EVENT_NAME_BY_TOPIC,
    canonical_trade_name,
    decode_fourmeme_log,
)

try:
    from web3.providers.rpc import AsyncHTTPProvider
except ModuleNotFoundError:
    AsyncHTTPProvider = None

logger = logging.getLogger(__name__)


class FourMemeListener:
    """Real-time event listener for FourMeme platform"""

    def __init__(self, w3: AsyncWeb3, config: Dict[str, Any], ws_manager: Any = None):
        self.w3 = w3
        self.config = config
        self.ws_manager = ws_manager
        self.contract_address = config.get('contract_address')
        self.contract_abi = config.get('contract_abi', [])
        self.contract: Optional[AsyncContract] = None

        # Event deduplication cache (last 1000 tx hashes)
        self.seen_txs: Set[str] = set()
        self.max_cache_size = 1000

        # Event handlers
        self.event_handlers: Dict[str, List[Callable]] = {}

        # Statistics
        self.events_processed = 0
        self.last_block_processed = 0
        self.blocks_skipped = 0  # 跳过的区块数
        self.max_block_lag = 0  # 最大落后区块数
        self.current_block_lag = 0  # 当前落后区块数
        self.last_check_time = time.time()  # 上次检查时间
        self.connection_errors = 0  # 连接错误次数
        self._last_ws_reconnect_attempt_at = 0.0
        self._ws_reconnect_cooldown_seconds = 1.0
        self.block_timestamp_cache: Dict[int, int] = {}
        self.max_block_timestamp_cache_size = 2048
        self.timestamp_cache_hits = 0
        self.timestamp_cache_misses = 0
        self.timestamp_block_fetches = 0
        self.timestamp_block_fetch_ms = 0.0
        self.last_range_logs_count = 0
        self.last_range_process_ms = 0.0
        self.last_range_timestamp_cache_hits = 0
        self.last_range_timestamp_cache_misses = 0
        self.last_range_timestamp_block_fetches = 0
        self.last_range_timestamp_block_fetch_ms = 0.0
        self.resume_cursor: Optional[Dict[str, Any]] = None
        self.resume_cursor_active = False

        raw_max_lag_skip_blocks = self.config.get('max_lag_skip_blocks', 0)
        try:
            self.max_lag_skip_blocks = max(0, int(raw_max_lag_skip_blocks))
        except (TypeError, ValueError):
            self.max_lag_skip_blocks = 0

        raw_lag_skip_keep_recent_blocks = self.config.get('lag_skip_keep_recent_blocks', 200)
        try:
            self.lag_skip_keep_recent_blocks = max(0, int(raw_lag_skip_keep_recent_blocks))
        except (TypeError, ValueError):
            self.lag_skip_keep_recent_blocks = 200

        raw_event_batch_size = self.config.get('event_batch_size', 200)
        try:
            self.event_batch_size = max(1, int(raw_event_batch_size))
        except (TypeError, ValueError):
            self.event_batch_size = 200

        raw_timestamp_prefetch_concurrency = self.config.get('timestamp_prefetch_concurrency', 16)
        try:
            self.timestamp_prefetch_concurrency = max(1, int(raw_timestamp_prefetch_concurrency))
        except (TypeError, ValueError):
            self.timestamp_prefetch_concurrency = 16

        # Dedicated HTTP providers for get_logs polling
        self.log_http_endpoints = self.config.get('log_http_endpoints', [])
        self.log_w3_pool: List[AsyncWeb3] = []
        self.log_provider_order: List[int] = []
        self.log_provider_errors: Dict[int, int] = {}
        self.log_provider_last_failure_at: Dict[int, float] = {}
        self.log_provider_switches = 0
        self.log_range_splits = 0
        self.log_last_provider_index: Optional[int] = None
        self.log_last_request_ms = 0.0
        self.log_last_effective_to_block: Optional[int] = None
        self.last_processed_range_end: Optional[int] = None

        raw_log_provider_cooldown = self.config.get('log_provider_cooldown_seconds', 45)
        try:
            self.log_provider_cooldown_seconds = max(0.0, float(raw_log_provider_cooldown))
        except (TypeError, ValueError):
            self.log_provider_cooldown_seconds = 45.0

        raw_log_provider_request_timeout = self.config.get('log_provider_request_timeout_seconds', 8)
        try:
            self.log_provider_request_timeout_seconds = max(0.1, float(raw_log_provider_request_timeout))
        except (TypeError, ValueError):
            self.log_provider_request_timeout_seconds = 8.0

        raw_poll_interval = self.config.get('listener_poll_interval_seconds', 0.5)
        try:
            self.listener_poll_interval_seconds = max(0.1, float(raw_poll_interval))
        except (TypeError, ValueError):
            self.listener_poll_interval_seconds = 0.5

        self._build_log_providers()

    def _build_log_providers(self):
        """Build dedicated AsyncWeb3 HTTP providers for get_logs."""
        endpoints = [endpoint.strip() for endpoint in self.log_http_endpoints if endpoint and endpoint.strip()]
        if not endpoints or AsyncHTTPProvider is None:
            self.log_w3_pool = []
            self.log_provider_order = []
            return

        self.log_w3_pool = []
        request_kwargs = Config.get_http_request_kwargs()
        for endpoint in endpoints:
            try:
                provider = AsyncHTTPProvider(endpoint, request_kwargs=request_kwargs)
                provider_w3 = AsyncWeb3(provider)
                provider_w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                self.log_w3_pool.append(provider_w3)
            except Exception as exc:
                logger.warning(f"Failed to initialize log HTTP provider {endpoint}: {exc}")

        if len(self.log_w3_pool) != len(endpoints):
            logger.warning(
                "Log HTTP provider pool partially available "
                f"({len(self.log_w3_pool)}/{len(endpoints)} initialized)"
            )

        self.log_provider_order = list(range(len(self.log_w3_pool)))
        self.log_provider_errors = {index: 0 for index in self.log_provider_order}
        self.log_provider_last_failure_at = {index: 0.0 for index in self.log_provider_order}

    async def close_log_providers(self) -> None:
        for provider_w3 in self.log_w3_pool:
            provider = getattr(provider_w3, 'provider', None)
            disconnect = getattr(provider, 'disconnect', None)
            if disconnect is None:
                continue
            try:
                result = disconnect()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.debug(f"Failed to close log provider session: {exc}")

    def _mark_log_provider_failure(self, provider_index: int, now: Optional[float] = None) -> None:
        """Record provider failure timestamp and increment error counter."""
        if provider_index in self.log_provider_errors:
            self.log_provider_errors[provider_index] += 1
        if provider_index in self.log_provider_last_failure_at:
            self.log_provider_last_failure_at[provider_index] = time.monotonic() if now is None else now

    @staticmethod
    def _normalize_block_timestamp(raw_timestamp: Any) -> Optional[int]:
        try:
            timestamp = int(raw_timestamp)
        except (TypeError, ValueError):
            return None
        return timestamp if timestamp > 0 else None

    @staticmethod
    def _normalize_tx_hash(tx_hash: Any) -> str:
        if isinstance(tx_hash, bytes):
            return tx_hash.hex()
        if isinstance(tx_hash, str):
            return tx_hash[2:] if tx_hash.startswith('0x') else tx_hash
        return ''

    @staticmethod
    def _normalize_log_index(log_index: Any) -> int:
        try:
            return int(log_index)
        except (TypeError, ValueError):
            return -1

    @classmethod
    def _normalize_cursor(cls, cursor: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not cursor:
            return None

        try:
            block_number = int(cursor.get('block_number', cursor.get('blockNumber')))
        except (TypeError, ValueError):
            return None

        if block_number < 0:
            return None

        return {
            'block_number': block_number,
            'log_index': cls._normalize_log_index(cursor.get('log_index', cursor.get('logIndex'))),
            'tx_hash': cls._normalize_tx_hash(cursor.get('tx_hash', cursor.get('transactionHash'))),
        }

    @classmethod
    def _event_position(cls, event_like: Dict[str, Any]) -> tuple[int, int, str]:
        try:
            block_number = int(event_like.get('blockNumber', event_like.get('block_number', -1)))
        except (TypeError, ValueError):
            block_number = -1

        return (
            block_number,
            cls._normalize_log_index(event_like.get('logIndex', event_like.get('log_index'))),
            cls._normalize_tx_hash(event_like.get('transactionHash', event_like.get('tx_hash'))),
        )

    async def _resolve_event_timestamp(
        self,
        event_log: Dict,
        block: Optional[Dict] = None,
        timestamp_w3: Optional[AsyncWeb3] = None,
    ) -> int:
        block_number = event_log.get('blockNumber')
        if block_number is None:
            return int(time.time())

        try:
            block_number = int(block_number)
        except (TypeError, ValueError):
            return int(time.time())

        cached_timestamp = self.block_timestamp_cache.get(block_number)
        if cached_timestamp is not None:
            self.timestamp_cache_hits += 1
            return cached_timestamp

        self.timestamp_cache_misses += 1

        if block is not None:
            block_timestamp = self._normalize_block_timestamp(block.get('timestamp'))
            if block_timestamp is not None:
                self.block_timestamp_cache[block_number] = block_timestamp
                if len(self.block_timestamp_cache) > self.max_block_timestamp_cache_size:
                    oldest_block = next(iter(self.block_timestamp_cache))
                    self.block_timestamp_cache.pop(oldest_block, None)
                return block_timestamp

        provider_w3 = timestamp_w3 or self.w3

        try:
            fetch_started_at = time.perf_counter()
            resolved_block = await provider_w3.eth.get_block(block_number)
            self.timestamp_block_fetches += 1
            self.timestamp_block_fetch_ms += (time.perf_counter() - fetch_started_at) * 1000
            block_timestamp = self._normalize_block_timestamp(resolved_block.get('timestamp'))
            if block_timestamp is not None:
                self.block_timestamp_cache[block_number] = block_timestamp
                if len(self.block_timestamp_cache) > self.max_block_timestamp_cache_size:
                    oldest_block = next(iter(self.block_timestamp_cache))
                    self.block_timestamp_cache.pop(oldest_block, None)
                return block_timestamp
        except Exception as exc:
            logger.debug(f"Failed to resolve block timestamp for block {block_number}: {exc}")

        return int(time.time())

    def _filter_logs_after_resume_cursor(self, logs: List[Dict], to_block: int) -> List[Dict]:
        if not self.resume_cursor_active or not self.resume_cursor:
            return logs

        resume_block = int(self.resume_cursor['block_number'])
        if to_block < resume_block:
            return logs

        filtered_logs = [
            log for log in logs
            if self._event_position(log) > self._event_position(self.resume_cursor)
        ]

        if to_block >= resume_block:
            self.resume_cursor_active = False

        return filtered_logs

    def _ordered_log_provider_indices(self, now: Optional[float] = None) -> List[int]:
        """Return provider indices ordered by static endpoint order, skipping cooldown providers."""
        if not self.log_provider_order:
            return []

        current = time.monotonic() if now is None else now
        ordered: List[int] = []

        for index in self.log_provider_order:
            last_failure_at = self.log_provider_last_failure_at.get(index, 0.0)
            if (
                self.log_provider_cooldown_seconds > 0
                and last_failure_at > 0
                and (current - last_failure_at) < self.log_provider_cooldown_seconds
            ):
                continue
            ordered.append(index)

        if ordered:
            return ordered

        return list(self.log_provider_order)

    async def _get_logs_via_provider(self, provider_index: Optional[int], from_block: int, to_block: int):
        """Fetch logs via selected provider, fallback to listener ws provider."""
        self.log_last_effective_to_block = None
        if to_block < from_block:
            self.log_last_provider_index = provider_index
            self.log_last_request_ms = 0.0
            return [], provider_index

        provider_w3 = self.w3 if provider_index is None else self.log_w3_pool[provider_index]

        effective_to_block = to_block
        try:
            provider_head = await asyncio.wait_for(
                provider_w3.eth.block_number,
                timeout=self.log_provider_request_timeout_seconds,
            )
            effective_to_block = min(to_block, int(provider_head))
            self.log_last_effective_to_block = effective_to_block
            if effective_to_block < from_block:
                self.log_last_provider_index = provider_index
                self.log_last_request_ms = 0.0
                logger.debug(
                    f"get_logs skipped for provider={provider_index if provider_index is not None else 'ws'} "
                    f"requested={from_block}-{to_block} provider_head={provider_head}"
                )
                return [], provider_index
        except Exception as exc:
            if isinstance(exc, asyncio.TimeoutError):
                raise
            logger.debug(
                f"Failed to fetch provider head before get_logs for provider="
                f"{provider_index if provider_index is not None else 'ws'}: {exc}"
            )

        payload = {
            'address': self.contract_address,
            'fromBlock': from_block,
            'toBlock': effective_to_block
        }

        start = time.perf_counter()
        if provider_index is None:
            logs = await asyncio.wait_for(
                provider_w3.eth.get_logs(payload),
                timeout=self.log_provider_request_timeout_seconds,
            )
            self.log_last_provider_index = None
            self.log_last_request_ms = (time.perf_counter() - start) * 1000
            return logs, None

        logs = await asyncio.wait_for(
            provider_w3.eth.get_logs(payload),
            timeout=self.log_provider_request_timeout_seconds,
        )
        self.log_last_provider_index = provider_index
        self.log_last_request_ms = (time.perf_counter() - start) * 1000
        return logs, provider_index


    @staticmethod
    def _is_timeout_or_rate_limit_error(error: Exception) -> bool:
        """Identify timeout/rate-limit style transient get_logs failures."""
        if isinstance(error, asyncio.TimeoutError):
            return True

        message = str(error).lower()
        markers = [
            'invalid block range',
            'eth_getlogs is limited',
            'maximum block range',
            'exceed maximum block range',
            'limit exceeded',
            '429',
            'rate limit',
            'too many requests',
            'timeout',
            'timed out',
            'time-out',
            'gateway timeout',
            'request timeout',
            'temporarily unavailable'
        ]
        return any(marker in message for marker in markers)

    @staticmethod
    def _range_limit_from_error(error: Exception) -> Optional[int]:
        """Extract a provider's explicit eth_getLogs range limit when present."""
        message = str(error).lower()
        match = re.search(r'(?:maximum block range|block range limit)[^0-9]{0,24}(\d+)', message)
        if not match:
            return None
        try:
            limit = int(match.group(1))
        except (TypeError, ValueError):
            return None
        return limit if limit > 1 else None

    def _load_contract(self):
        """Load contract instance"""
        if not self.contract_address:
            raise ValueError("Contract address not configured")

        # Ensure Checksum Address. A malformed address must fail loudly: silently keeping
        # the raw string would make every later contract call raise somewhere far away.
        try:
            self.contract_address = self.w3.to_checksum_address(self.contract_address)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid contract address {self.contract_address!r}: {exc}") from exc

        # Load ABI from config and combine with internal version to ensure
        # all possible event signatures are covered
        external_abi = self.config.get('contract_abi', [])
        internal_abi = self._get_minimal_abi()

        # Combine ABIs (filter duplicates by name)
        combined_abi = internal_abi.copy()
        existing_names = {item.get('name') for item in internal_abi if item.get('type') == 'event'}

        for item in external_abi:
            if item.get('type') == 'event' and item.get('name') not in existing_names:
                combined_abi.append(item)
            elif item.get('type') == 'function':
                combined_abi.append(item)

        self.contract_abi = combined_abi
        logger.info(f"Loaded combined ABI with {len(self.contract_abi)} entries")

        self.contract = self.w3.eth.contract(
            address=self.w3.to_checksum_address(self.contract_address),
            abi=self.contract_abi
        )

    def _get_minimal_abi(self) -> List[Dict]:
        """
        Minimal ABI with FourMeme TokenManager events
        Official events from TokenManager.lite.abi
        """
        return [
            {
                "anonymous": False,
                "inputs": [
                    {"indexed": False, "name": "creator", "type": "address"},
                    {"indexed": False, "name": "token", "type": "address"},
                    {"indexed": False, "name": "requestId", "type": "uint256"},
                    {"indexed": False, "name": "name", "type": "string"},
                    {"indexed": False, "name": "symbol", "type": "string"},
                    {"indexed": False, "name": "totalSupply", "type": "uint256"},
                    {"indexed": False, "name": "launchTime", "type": "uint256"},
                    {"indexed": False, "name": "launchFee", "type": "uint256"},
                ],
                "name": "TokenCreate",
                "type": "event"
            },
            {
                "anonymous": False,
                "inputs": [
                    {"indexed": False, "name": "token", "type": "address"},
                ],
                "name": "TradeStop",
                "type": "event"
            },
        ]

    def register_handler(self, event_type: str, handler: Callable):
        """Register a handler for specific event type"""
        if event_type not in self.event_handlers:
            self.event_handlers[event_type] = []
        self.event_handlers[event_type].append(handler)

    def _is_duplicate(self, tx_hash: str) -> bool:
        """Check if transaction has been processed"""
        if tx_hash in self.seen_txs:
            return True

        self.seen_txs.add(tx_hash)

        # LRU cache cleanup
        if len(self.seen_txs) > self.max_cache_size:
            # Remove oldest 100 entries
            oldest = list(self.seen_txs)[:100]
            for old_tx in oldest:
                self.seen_txs.discard(old_tx)

        return False

    async def _process_event(self, event_name: str, event_data: Dict):
        """Process a single event and call registered handlers"""
        try:
            tx_hash = event_data.get('transactionHash', b'')
            if isinstance(tx_hash, bytes):
                tx_hash = tx_hash.hex()
            log_index = event_data.get('logIndex', 0)

            # Use tx_hash + log_index for deduplication (one tx can have multiple events)
            dedup_key = f"{tx_hash}_{log_index}"

            if self._is_duplicate(dedup_key):
                logger.debug(f"Skipping duplicate event: {dedup_key}")
                return

            # Call all registered handlers
            handlers = self.event_handlers.get(event_name, [])
            for handler in handlers:
                try:
                    await handler(event_name, event_data)
                except Exception as e:
                    logger.error(f"Error in event handler for {event_name}: {e}")
                    import traceback
                    traceback.print_exc()

            self.events_processed += 1
        except Exception as e:
            logger.error(f"❌ ERROR in _process_event for {event_name}: {e}")
            import traceback
            traceback.print_exc()
            raise  # Re-raise so we can see it in the outer handler

    def _compute_chunk_size(self, gap: int) -> int:
        """Adaptive catch-up window sizing based on current block lag."""
        if gap > 1000:
            return 160
        if gap > 500:
            return 120
        if gap > 200:
            return 80
        if gap > 50:
            return 32
        return 8

    def _should_attempt_ws_reconnect(self, now: float) -> bool:
        """Return whether enough cooldown has elapsed for reconnect attempt."""
        if now - self._last_ws_reconnect_attempt_at < self._ws_reconnect_cooldown_seconds:
            return False
        self._last_ws_reconnect_attempt_at = now
        return True

    def _apply_lag_skip_if_needed(self, latest_block: int) -> None:
        """Optionally skip old backlog blocks when lag exceeds configured threshold."""
        if self.max_lag_skip_blocks <= 0:
            return

        gap = latest_block - self.last_block_processed
        if gap <= self.max_lag_skip_blocks:
            return

        skip_to = max(self.last_block_processed, latest_block - self.lag_skip_keep_recent_blocks)
        skipped = skip_to - self.last_block_processed
        if skipped <= 0:
            return

        self.blocks_skipped += skipped
        logger.warning(
            f"⚠️ Listener lagging {gap} blocks, skipping {skipped} blocks to {skip_to} "
            f"(keeping last {self.lag_skip_keep_recent_blocks})"
        )
        self.last_block_processed = skip_to

    @staticmethod
    def _is_ws_force_reconnect_error(error: Exception) -> bool:
        message = str(error).lower()
        return (
            'timeexhausted' in message
            or 'timed out waiting for response with request id' in message
            or 'keepalive ping timeout' in message
            or 'connectionclosederror' in message
            or 'no close frame received' in message
        )

    async def _attempt_ws_recovery(self, error: Exception) -> bool:
        if not self.ws_manager:
            return False
        if not self._should_attempt_ws_reconnect(time.monotonic()):
            return False

        force_reconnect = self._is_ws_force_reconnect_error(error)
        await self.ws_manager.ensure_connection(force_reconnect=force_reconnect)
        self.w3 = self.ws_manager.get_web3()
        self._load_contract()
        return True

    async def subscribe_to_events(
        self,
        resume_from_block: Optional[int] = None,
        resume_cursor: Optional[Dict[str, Any]] = None,
    ):
        """Subscribe to contract events via WebSocket"""
        if not self.contract:
            self._load_contract()

        logger.info(f"🎯 Subscribing to FourMeme events at {self.contract_address}")

        # Get current block to start from
        current_block = await self.w3.eth.block_number

        # Check historical scan settings
        scan_historical = self.config.get('scan_historical', Config.SCAN_HISTORICAL)
        historical_blocks = self.config.get('historical_blocks', Config.HISTORICAL_BLOCKS)

        self.resume_cursor = self._normalize_cursor(resume_cursor)
        self.resume_cursor_active = self.resume_cursor is not None

        resolved_resume_block: Optional[int] = None
        if self.resume_cursor is not None:
            resolved_resume_block = self.resume_cursor['block_number']
        elif resume_from_block is not None:
            try:
                resolved_resume_block = max(0, int(resume_from_block))
            except (TypeError, ValueError):
                resolved_resume_block = None

        if resolved_resume_block is not None:
            if self.resume_cursor is not None:
                self.last_block_processed = min(current_block, resolved_resume_block) - 1
                next_block = self.last_block_processed + 1
                if next_block <= current_block:
                    logger.info(
                        f"Resuming listener from applied cursor block {next_block} "
                        f"(chain head {current_block})"
                    )
                else:
                    logger.info(
                        f"Applied cursor already beyond chain head {current_block}; waiting for new blocks"
                    )
            else:
                self.last_block_processed = min(current_block, resolved_resume_block)
                next_block = self.last_block_processed + 1
                if self.last_block_processed < current_block:
                    logger.info(
                        f"Resuming listener from persisted checkpoint block {next_block} "
                        f"(chain head {current_block})"
                    )
                else:
                    logger.info(
                        f"Persisted listener checkpoint already at chain head {current_block}; "
                        "waiting for new blocks"
                    )
        elif scan_historical:
            start_block = max(0, current_block - historical_blocks)
            logger.info(f"📜 Scanning historical blocks {start_block} to {current_block} ({historical_blocks} blocks)...")
            # Use _process_block_range directly as it handles chunking and retries
            historical_ok = await self._process_block_range(start_block, current_block)
            if historical_ok:
                logger.info("✅ Historical scan complete")
                self.last_block_processed = current_block
            else:
                logger.warning(
                    "Historical scan completed with failures; "
                    "continuing from current head without advancing processed checkpoint"
                )
                self.last_block_processed = start_block - 1 if start_block > 0 else 0
        else:
            self.last_block_processed = current_block

        logger.info(f"✅ Event subscription active (starting from block {self.last_block_processed + 1})")

        # Poll for new blocks and events
        while True:
            try:
                # Get latest block number
                latest_block = await self.w3.eth.block_number

                # Process new blocks
                if latest_block > self.last_block_processed:
                    gap = latest_block - self.last_block_processed
                    self.current_block_lag = gap

                    # 记录最大落后
                    if gap > self.max_block_lag:
                        self.max_block_lag = gap

                    self._apply_lag_skip_if_needed(latest_block)
                    gap = latest_block - self.last_block_processed
                    if gap <= 0:
                        continue

                    if gap > 50:
                        logger.warning(f"⚠️ Listener {gap} blocks behind, catching up...")

                    # 自适应catch-up窗口：lag越大窗口越大，优先尽快追上头部
                    chunk = self._compute_chunk_size(gap)
                    to_block = min(latest_block, self.last_block_processed + chunk)

                    processed_ok = await self._process_block_range(
                        self.last_block_processed + 1,
                        to_block
                    )
                    if processed_ok:
                        processed_range_end = self.last_processed_range_end
                        if processed_range_end is None:
                            processed_range_end = to_block
                        self.last_block_processed = max(self.last_block_processed, processed_range_end)
                    else:
                        logger.warning(
                            f"Failed to process blocks {self.last_block_processed + 1}-{to_block}; "
                            "will retry on next poll"
                        )
                        await asyncio.sleep(0.2)
                        continue

                    # 如果还是落后，不进入 sleep，继续 catchup
                    if self.last_block_processed < latest_block:
                        continue
                    self.current_block_lag = 0
                else:
                    self.current_block_lag = 0

                # Wait before next check
                await asyncio.sleep(self.listener_poll_interval_seconds)

            except Exception as e:
                self.connection_errors += 1
                logger.error(f"Error polling events: {repr(e)}", exc_info=True)

                recovered = False
                try:
                    recovered = await self._attempt_ws_recovery(e)
                except Exception as conn_err:
                    logger.error(f"Failed to reconnect: {repr(conn_err)}", exc_info=True)

                if recovered:
                    continue

                await asyncio.sleep(5)

    async def _process_logs_in_batches(
        self,
        logs: List[Dict],
        timestamp_w3: Optional[AsyncWeb3] = None,
    ) -> None:
        """Process decoded logs in bounded batches to keep event loop responsive."""
        if not logs:
            return

        ordered_logs = sorted(logs, key=self._event_position)
        block_cache: Dict[int, Dict] = {}

        async def prefetch_block(block_number: int, semaphore: asyncio.Semaphore):
            async with semaphore:
                try:
                    fetch_started_at = time.perf_counter()
                    block = await timestamp_w3.eth.get_block(block_number)
                    self.timestamp_block_fetches += 1
                    self.timestamp_block_fetch_ms += (time.perf_counter() - fetch_started_at) * 1000
                    block_timestamp = self._normalize_block_timestamp(block.get('timestamp'))
                    if block_timestamp is not None:
                        self.block_timestamp_cache[block_number] = block_timestamp
                        if len(self.block_timestamp_cache) > self.max_block_timestamp_cache_size:
                            oldest_block = next(iter(self.block_timestamp_cache))
                            self.block_timestamp_cache.pop(oldest_block, None)
                    return block_number, block
                except Exception as exc:
                    logger.debug(f"Failed to prefetch block timestamp for block {block_number}: {exc}")
                    return block_number, None

        for start in range(0, len(ordered_logs), self.event_batch_size):
            batch = ordered_logs[start:start + self.event_batch_size]
            missing_block_numbers = []
            seen_batch_blocks = set()
            if timestamp_w3 is not None:
                for log in batch:
                    block_number = log.get('blockNumber')
                    try:
                        normalized_block_number = int(block_number)
                    except (TypeError, ValueError):
                        continue
                    if (
                        normalized_block_number in seen_batch_blocks
                        or normalized_block_number in block_cache
                        or normalized_block_number in self.block_timestamp_cache
                    ):
                        continue
                    seen_batch_blocks.add(normalized_block_number)
                    missing_block_numbers.append(normalized_block_number)

            if missing_block_numbers:
                semaphore = asyncio.Semaphore(self.timestamp_prefetch_concurrency)
                prefetch_results = await asyncio.gather(
                    *(prefetch_block(block_number, semaphore) for block_number in missing_block_numbers)
                )
                for block_number, block in prefetch_results:
                    if block is not None:
                        block_cache[block_number] = block

            for log in batch:
                block = None
                block_number = log.get('blockNumber')
                try:
                    normalized_block_number = int(block_number)
                except (TypeError, ValueError):
                    normalized_block_number = None

                if normalized_block_number is not None:
                    block = block_cache.get(normalized_block_number)

                await self._parse_and_process_event(log, block, timestamp_w3)
            if start + self.event_batch_size < len(ordered_logs):
                await asyncio.sleep(0)

    async def _process_block_range(self, from_block: int, to_block: int, retry_count: int = 0) -> bool:
        """Process events in a block range and return success/failure."""
        provider_sequence = self._ordered_log_provider_indices()
        self.last_processed_range_end = None
        range_started_at = time.perf_counter()

        if not provider_sequence:
            provider_sequence = [None]

        last_transient_exc: Optional[Exception] = None

        for sequence_index, provider_index in enumerate(provider_sequence):
            try:
                logs, selected_provider = await self._get_logs_via_provider(provider_index, from_block, to_block)
                effective_to_block = self.log_last_effective_to_block
                timestamp_w3 = self.w3 if selected_provider is None else self.log_w3_pool[selected_provider]
                if selected_provider is None:
                    logger.debug(
                        f"get_logs provider=ws blocks={from_block}-{to_block} req_ms={self.log_last_request_ms:.1f}"
                    )
                else:
                    logger.debug(
                        f"get_logs provider_index={selected_provider} endpoint={self.log_http_endpoints[selected_provider]} "
                        f"blocks={from_block}-{to_block} req_ms={self.log_last_request_ms:.1f}"
                    )

                if effective_to_block is None:
                    effective_to_block = to_block

                if effective_to_block < from_block:
                    if provider_index is not None and sequence_index < len(provider_sequence) - 1:
                        next_provider = provider_sequence[sequence_index + 1]
                        self.log_provider_switches += 1
                        logger.warning(
                            f"provider {provider_index} is behind for {from_block}-{to_block}; "
                            f"retrying with provider {next_provider}"
                        )
                        continue
                    self.last_processed_range_end = self.last_block_processed
                    return True

                range_log_count = 0
                hits_before = self.timestamp_cache_hits
                misses_before = self.timestamp_cache_misses
                fetches_before = self.timestamp_block_fetches
                fetch_ms_before = self.timestamp_block_fetch_ms

                if logs:
                    logs = self._filter_logs_after_resume_cursor(logs, to_block=effective_to_block)
                    range_log_count = len(logs)
                    logger.debug(f"Found {range_log_count} events in blocks {from_block}-{effective_to_block}")
                    await self._process_logs_in_batches(logs, timestamp_w3=timestamp_w3)

                self.last_processed_range_end = effective_to_block
                self.last_range_logs_count = range_log_count
                self.last_range_process_ms = (time.perf_counter() - range_started_at) * 1000
                self.last_range_timestamp_cache_hits = self.timestamp_cache_hits - hits_before
                self.last_range_timestamp_cache_misses = self.timestamp_cache_misses - misses_before
                self.last_range_timestamp_block_fetches = self.timestamp_block_fetches - fetches_before
                self.last_range_timestamp_block_fetch_ms = self.timestamp_block_fetch_ms - fetch_ms_before

                if (
                    range_log_count > 0
                    or self.last_range_process_ms >= 1000
                    or self.last_range_timestamp_block_fetches > 0
                ):
                    logger.info(
                        "catch-up diag | provider=%s | blocks=%s-%s | logs=%s | req_ms=%.1f | total_ms=%.1f | "
                        "ts_hits=%s | ts_misses=%s | ts_fetches=%s | ts_fetch_ms=%.1f",
                        'ws' if selected_provider is None else selected_provider,
                        from_block,
                        effective_to_block,
                        range_log_count,
                        self.log_last_request_ms,
                        self.last_range_process_ms,
                        self.last_range_timestamp_cache_hits,
                        self.last_range_timestamp_cache_misses,
                        self.last_range_timestamp_block_fetches,
                        self.last_range_timestamp_block_fetch_ms,
                    )

                if effective_to_block < to_block and provider_index is not None and sequence_index < len(provider_sequence) - 1:
                    next_provider = provider_sequence[sequence_index + 1]
                    self.log_provider_switches += 1
                    logger.warning(
                        f"provider {provider_index} only covered through block {effective_to_block} for requested "
                        f"range {from_block}-{to_block}; retrying with provider {next_provider}"
                    )
                    continue

                return True

            except Exception as exc:
                if provider_index is not None:
                    self._mark_log_provider_failure(provider_index)

                if not self._is_timeout_or_rate_limit_error(exc):
                    logger.error(f"Error processing blocks {from_block}-{to_block}: {repr(exc)}", exc_info=True)
                    return False

                last_transient_exc = exc

                if provider_index is not None and sequence_index < len(provider_sequence) - 1:
                    next_provider = provider_sequence[sequence_index + 1]
                    self.log_provider_switches += 1
                    logger.warning(
                        f"get_logs failed on provider {provider_index} for {from_block}-{to_block}; "
                        f"retrying once with provider {next_provider}: {exc}"
                    )

        if last_transient_exc is None:
            if self.last_processed_range_end is None:
                self.last_processed_range_end = to_block
            return True

        if from_block >= to_block:
            logger.warning(
                f"Skipping block {from_block} after provider retry exhaustion due to transient get_logs error: {last_transient_exc}"
            )
            return False

        message = str(last_transient_exc).lower()
        explicit_range_limit = self._range_limit_from_error(last_transient_exc)
        if explicit_range_limit is not None and (to_block - from_block + 1) > explicit_range_limit:
            logger.warning(
                f"Chunking blocks {from_block}-{to_block} into provider-limited ranges of "
                f"{explicit_range_limit} after get_logs range-limit error: {last_transient_exc}"
            )
            self.log_range_splits += 1
            chunk_start = from_block
            while chunk_start <= to_block:
                chunk_end = min(to_block, chunk_start + explicit_range_limit - 1)
                chunk_ok = await self._process_block_range(chunk_start, chunk_end, retry_count + 1)
                if not chunk_ok:
                    return False
                chunk_start = chunk_end + 1
            return True

        if 'block range limit exceeded' in message and (to_block - from_block) > 1:
            reduced_to_block = from_block + max(1, (to_block - from_block) // 2)
            logger.warning(
                f"Reducing block range {from_block}-{to_block} to {from_block}-{reduced_to_block} "
                f"after provider range-limit error: {last_transient_exc}"
            )
            first_ok = await self._process_block_range(from_block, reduced_to_block, retry_count + 1)
            second_ok = await self._process_block_range(reduced_to_block + 1, to_block, retry_count + 1)
            return first_ok and second_ok

        delay = min(0.2 * (2 ** retry_count), 2.0)
        await asyncio.sleep(delay)

        self.log_range_splits += 1
        mid = (from_block + to_block) // 2
        logger.warning(
            f"Splitting blocks {from_block}-{to_block} into {from_block}-{mid} and {mid + 1}-{to_block} "
            f"after get_logs transient failure: {last_transient_exc}"
        )

        left_ok = await self._process_block_range(from_block, mid, retry_count + 1)
        right_ok = await self._process_block_range(mid + 1, to_block, retry_count + 1)
        return left_ok and right_ok


    async def _parse_and_process_event(
        self,
        event_log: Dict,
        block: Optional[Dict] = None,
        timestamp_w3: Optional[AsyncWeb3] = None,
    ):
        """Parse raw event log and process"""
        try:
            # 记录事件被发现的时间
            event_timestamp = await self._resolve_event_timestamp(
                event_log,
                block=block,
                timestamp_w3=timestamp_w3,
            )

            # Known-topic fast path: decode strictly from the checked-in ABIs.
            # The ABI registry is the single source of truth for topic hashes and
            # layouts. Earlier hand-maintained tables mapped the LiquidityAdded
            # topic to TokenSale, contained one malformed 65-char topic, and
            # mis-decoded v1 trades because the v1 layout differs from v2.
            topic0 = event_log['topics'][0].hex() if event_log.get('topics') else 'no-topic'
            if isinstance(topic0, str) and topic0.startswith('0x'):
                topic0 = topic0[2:]
            topic0 = str(topic0).lower()

            if topic0 in EVENT_NAME_BY_TOPIC:
                decoded = decode_fourmeme_log(event_log)
                if decoded is None:
                    tx_hash = event_log.get('transactionHash', b'')
                    if isinstance(tx_hash, bytes):
                        tx_hash = tx_hash.hex()
                    logger.warning(
                        "Known Four.meme topic failed strict ABI decode; skipping "
                        f"tx={str(tx_hash)[:12]} topic={topic0[:12]}"
                    )
                    return

                raw_event_name, args = decoded
                if raw_event_name in AUXILIARY_TRADE_EVENTS:
                    logger.debug(f"Skipping auxiliary Four.meme event {raw_event_name}")
                    return

                event_name = canonical_trade_name(raw_event_name) or raw_event_name
                normalized_args = {}
                for key, value in args.items():
                    if isinstance(value, str) and value.startswith('0x'):
                        try:
                            value = self.w3.to_checksum_address(value)
                        except Exception:
                            pass
                    normalized_args[key] = value

                processed_log = {
                    'event_name': event_name,
                    'raw_event_name': raw_event_name,
                    'args': normalized_args,
                    'transactionHash': event_log.get('transactionHash'),
                    'logIndex': event_log.get('logIndex'),
                    'blockNumber': event_log.get('blockNumber'),
                    'timestamp': event_timestamp,
                    'received_at': time.time(),
                }
                await self._process_event(event_name, processed_log)
                return

            # Unknown topic: fallback to ABI decode path
            decoded_events = self.contract.events

            # FourMeme TokenManager2 events - 监控所有事件
            event_names = ['TokenCreate', 'TokenPurchase', 'TokenPurchaseV1', 'TokenPurchase2', 'TokenSale', 'TokenSaleV1', 'TokenSale2', 'TradeStop', 'LiquidityAdded']
            for event_name in event_names:
                try:
                    event = getattr(decoded_events, event_name, None)
                    if not event:
                        continue

                    processed_log = event().process_log(event_log)

                    # Convert to regular dict if needed
                    if not isinstance(processed_log, dict):
                        processed_log = dict(processed_log)

                    processed_log['event_name'] = event_name
                    # 优先使用 discovery_time，确保时序逻辑一致
                    processed_log['timestamp'] = event_timestamp
                    processed_log['blockNumber'] = event_log.get('blockNumber')
                    processed_log['transactionHash'] = event_log.get('transactionHash')
                    processed_log['logIndex'] = event_log.get('logIndex')
                    processed_log['received_at'] = time.time()

                except Exception as e:
                    # Log decoding errors for debugging
                    logger.debug(f"Failed to decode as {event_name}: {str(e)[:100]}")
                    continue

                # Decode succeeded - process the event
                await self._process_event(event_name, processed_log)
                return

            tx_hash = event_log.get('transactionHash', b'').hex()
            logger.warning(f"⚠️  Unrecognized event - Block: {event_log['blockNumber']}, Tx: {tx_hash[:10]}..., Topic: {topic0}")

        except Exception as e:
            logger.error(f"Error parsing event: {e}")

    async def poll_historical_events(self, from_block: int, to_block: int = None):
        """Poll historical events (useful for testing)"""
        if not self.contract:
            self._load_contract()

        if to_block is None:
            to_block = await self.w3.eth.block_number

        logger.info(f"Polling historical events from block {from_block} to {to_block}")

        # Use _process_block_range to handle large ranges and rate limits safely
        await self._process_block_range(from_block, to_block)

        logger.info("Finished polling historical events")

    def get_stats(self) -> Dict:
        """Get listener statistics"""
        return {
            'events_processed': self.events_processed,
            'last_block_processed': self.last_block_processed,
            'cache_size': len(self.seen_txs),
            'handlers_registered': sum(len(h) for h in self.event_handlers.values()),
            'blocks_skipped': self.blocks_skipped,
            'max_block_lag': self.max_block_lag,
            'current_block_lag': self.current_block_lag,
            'connection_errors': self.connection_errors,
            'uptime_seconds': int(time.time() - self.last_check_time),
            'log_last_provider_index': self.log_last_provider_index,
            'log_last_request_ms': round(self.log_last_request_ms, 2),
            'log_provider_switches': self.log_provider_switches,
            'log_range_splits': self.log_range_splits,
            'log_provider_errors': dict(self.log_provider_errors),
            'timestamp_cache_hits': self.timestamp_cache_hits,
            'timestamp_cache_misses': self.timestamp_cache_misses,
            'timestamp_block_fetches': self.timestamp_block_fetches,
            'timestamp_block_fetch_ms': round(self.timestamp_block_fetch_ms, 2),
            'last_range_logs_count': self.last_range_logs_count,
            'last_range_process_ms': round(self.last_range_process_ms, 2),
            'last_range_timestamp_cache_hits': self.last_range_timestamp_cache_hits,
            'last_range_timestamp_cache_misses': self.last_range_timestamp_cache_misses,
            'last_range_timestamp_block_fetches': self.last_range_timestamp_block_fetches,
            'last_range_timestamp_block_fetch_ms': round(self.last_range_timestamp_block_fetch_ms, 2),
        }

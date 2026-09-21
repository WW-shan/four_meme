"""
持续收集数据 - 后台运行
每小时自动保存一次数据
"""

import sys
from pathlib import Path

# Fix Windows console encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import asyncio
import json
import logging
import os
import signal
import time
from datetime import datetime
from dotenv import load_dotenv
from config.config import Config
from src.data import DataCollector
from src.core.ws_manager import WSConnectionManager
from src.core.listener import FourMemeListener

# Load environment variables
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('data/collection.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)



class ContinuousCollector:
    """持续数据收集器"""

    @staticmethod
    def _build_http_provider(endpoint, provider_cls):
        """Build the collector's HTTP provider with the configured local proxy."""
        return provider_cls(
            endpoint,
            request_kwargs=Config.get_http_request_kwargs(),
        )

    def __init__(self):
        self.collector = DataCollector()
        self.state_file = self.collector.output_dir / "collector_runtime_state.json"
        self.ws_manager = None
        self.listener = None
        self.running = True
        self.state_checkpoint_interval_seconds = 30
        self.flush_max_listener_lag_blocks = 16
        self.resume_max_age_seconds = 21600
        # On restart, a checkpoint further behind than this window is truncated to the chain
        # head, which silently drops that block range from collection. 0 disables the
        # truncation so a restart keeps backfilling from the checkpoint.
        self.resume_max_catchup_blocks = max(
            0, int(os.getenv('COLLECTOR_RESUME_MAX_CATCHUP_BLOCKS', '256'))
        )
        self.save_interval_hours = 1  # 每小时保存一次
        self.flush_check_interval_seconds = 60  # 每分钟检查一次可刷盘代币
        self.flush_inactivity_seconds = 15 * 60  # 超过15分钟无更新则刷盘
        self.flush_min_age_seconds = 15 * 60  # 创建满15分钟才允许刷盘
        self.last_stat_time = 0  # 上次显示统计的时间
        self.listener_task = None
        self.collector_task = None
        self.save_task = None
        self.stats_task = None
        self.flush_task = None
        self.checkpoint_task = None
        self.event_queue_size = 50000
        self._event_queue = None
        self.collector_batch_size = 500
        self.events_enqueued = 0
        self.events_processed = 0
        # Optional automatic signal scans. Empty by default: the scanner only records
        # launches until an operator asks for signals on specific events.
        self.scanner = None
        self.scanner_signal_events = self._parse_signal_events(os.getenv('SCANNER_SIGNAL_EVENTS', ''))
        self.signal_scan_max_inflight = max(1, int(os.getenv('SCANNER_SIGNAL_MAX_INFLIGHT', '2')))
        self.signal_scan_dedupe_seconds = float(os.getenv('SCANNER_SIGNAL_DEDUPE_SECONDS', '3600'))
        self.signal_min_unique_buyers = max(1, int(os.getenv('SCANNER_SIGNAL_MIN_UNIQUE_BUYERS', '3')))
        self._signal_scan_tasks = set()
        self._signal_scan_seen = {}
        self.signal_scans_blocked_by_mode = False
        # Candidate sweep: watch new tokens and push the ones that attract real buyers.
        self.candidate_sweep_seconds = max(0.0, float(os.getenv('SCANNER_CANDIDATE_SWEEP_SECONDS', '0')))
        self.candidate_max_per_hour = max(0, int(os.getenv('SCANNER_CANDIDATE_MAX_PER_HOUR', '20')))
        # The scanner follows recent trades, not token creation: measured on 2026-09-22, every
        # token with real money in a 30 minute window had been created hours earlier, so an
        # age filter on creation time excluded all of them.
        self.activity_window_seconds = max(
            60.0, float(os.getenv('SCANNER_CANDIDATE_ACTIVITY_WINDOW_SECONDS', '1800'))
        )
        self._recent_trades = {}
        self._recent_trade_meta = {}
        self._activity_seeded_blocks = 0
        # Counting wallets alone let $0.05 dust through while a token with 352 in buy volume
        # waited behind it. The floor is in the token's own quote asset: measured on
        # 2026-09-22, dust sat at <=0.1 and the real movers at >=12.
        self.candidate_min_buy_volume = max(
            0.0, float(os.getenv('SCANNER_CANDIDATE_MIN_BUY_VOLUME', '1.0'))
        )
        self.candidate_task = None
        self._candidate_alerted = {}

    @staticmethod
    def _parse_signal_events(raw: str) -> set:
        """Map operator-facing event names onto listener event names."""
        mapping = {'launch': 'TokenCreate', 'graduation': 'LiquidityAdded'}
        events = set()
        for part in str(raw or '').split(','):
            name = part.strip().lower()
            if not name:
                continue
            if name not in mapping:
                raise ValueError(
                    f"SCANNER_SIGNAL_EVENTS has unsupported value {name!r}; use launch, graduation or both"
                )
            events.add(mapping[name])
        return events

    def _should_skip_resume_due_to_checkpoint_age(self, state_payload: dict, now_ts: int | None = None) -> bool:
        if self.resume_max_age_seconds <= 0:
            return False

        saved_at_raw = (state_payload or {}).get("saved_at")
        if not saved_at_raw:
            return False

        try:
            saved_at = datetime.fromisoformat(str(saved_at_raw))
        except (TypeError, ValueError):
            logger.warning(
                f"checkpoint saved_at 无法解析 ({saved_at_raw!r})，直接从当前链头开始"
            )
            return True

        saved_at_ts = int(saved_at.timestamp())
        current_ts = int(time.time()) if now_ts is None else int(now_ts)
        checkpoint_age = max(0, current_ts - saved_at_ts)
        if checkpoint_age <= self.resume_max_age_seconds:
            return False

        logger.warning(
            f"checkpoint age {checkpoint_age}s > {self.resume_max_age_seconds}s，跳过历史恢复，直接从当前链头开始"
        )
        return True

    def _get_collector_listener_mode(self) -> str:
        mode = os.getenv('COLLECTOR_LISTENER_MODE', 'http_only').strip().lower()
        if mode not in {'hybrid', 'http_only'}:
            raise ValueError("Invalid COLLECTOR_LISTENER_MODE: expected 'hybrid' or 'http_only'")
        return mode

    def _bound_resume_cursor(self, resume_cursor: dict | None, current_block: int) -> dict | None:
        if not resume_cursor:
            return None

        if self.resume_max_catchup_blocks <= 0:
            return resume_cursor

        resume_block = int(resume_cursor.get("block_number", -1) or -1)
        if resume_block < 0:
            return None

        current_block = max(0, int(current_block or 0))
        min_resume_block = max(0, current_block - self.resume_max_catchup_blocks)
        if resume_block >= min_resume_block:
            return resume_cursor

        bounded_cursor = {
            "block_number": min_resume_block,
            "log_index": -1,
            "tx_hash": "",
        }
        logger.warning(
            "checkpoint 落后 %s blocks，超过最大回追窗口 %s；恢复点从 %s 截断到 %s",
            current_block - resume_block,
            self.resume_max_catchup_blocks,
            resume_block,
            min_resume_block,
        )
        return bounded_cursor

    async def start(self):
        """启动持续收集"""
        logger.info("="*70)
        logger.info("开始持续数据收集")
        logger.info(f"保存间隔: 每 {self.save_interval_hours} 小时")
        logger.info("按 Ctrl+C 停止")
        logger.info("="*70)

        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        try:
            # Validate role-separated RPC config at startup
            Config.validate_rpc_config()

            listener_mode = self._get_collector_listener_mode()
            log_http_endpoints = Config.get_log_http_pool()

            if listener_mode != 'http_only':
                # Get listener WebSocket URL (must be ws:// or wss://)
                ws_url = Config.get_listener_ws_url()

                logger.info(f"📡 连接节点: {ws_url}")
                logger.info("💡 推荐 RPC 角色分离配置(.env):")
                logger.info("   BSC_WSS_URL=wss://bsc.publicnode.com")
                logger.info("   BSC_LOG_HTTP_ENDPOINTS=https://four.rpc.48.club,https://rpc.ankr.com/bsc")
                logger.info("   BSC_TRADE_HTTP_RPC=https://bsc-dataseed.binance.org")

                # Initialize connection
                self.ws_manager = WSConnectionManager(ws_url)
                if not await self.ws_manager.connect():
                    logger.error("❌ 连接BSC节点失败")
                    logger.info("💡 请尝试更换RPC节点，推荐:")
                    for i, endpoint in enumerate(Config.FAST_RPC_ENDPOINTS[:5], 1):
                        logger.info(f"   {i}. {endpoint}")
                    return
                w3 = self.ws_manager.get_web3()
            else:
                from web3 import AsyncWeb3
                from web3.providers.rpc import AsyncHTTPProvider

                self.ws_manager = None
                fallback_endpoint = log_http_endpoints[0]
                logger.warning(f"⚠️ collector 使用 http_only 模式: 使用轮询节点 {fallback_endpoint}")
                w3 = AsyncWeb3(self._build_http_provider(fallback_endpoint, AsyncHTTPProvider))

            # 测试节点响应速度
            try:
                import time
                start = time.time()
                current_block = await w3.eth.block_number
                latency = (time.time() - start) * 1000
                logger.info(f"✅ 节点已连接 | 当前区块: {current_block} | 延迟: {latency:.0f}ms")

                if latency > 1000:
                    logger.warning(f"⚠️ 节点延迟较高 ({latency:.0f}ms)，建议更换更快的节点")
            except Exception as e:
                logger.warning(f"⚠️ 无法测试节点延迟: {e}")

            # 使用 Config 获取合约配置 (带完整 ABI)
            contract_config = Config.get_contract_config()

            # 初始化监听器
            config = {
                'contract_address': contract_config['contract_address'],
                'contract_abi': contract_config['contract_abi'],
                'log_http_endpoints': log_http_endpoints,
                'max_lag_skip_blocks': contract_config['max_lag_skip_blocks'],
                'lag_skip_keep_recent_blocks': contract_config['lag_skip_keep_recent_blocks'],
                'log_provider_cooldown_seconds': contract_config['log_provider_cooldown_seconds'],
            }
            self.listener = FourMemeListener(w3, config, self.ws_manager)

            # 注册事件处理器 - 使用统一的处理器
            self.listener.register_handler('TokenCreate', self._handle_event)
            self.listener.register_handler('TokenPurchase', self._handle_event)
            self.listener.register_handler('TokenSale', self._handle_event)
            self.listener.register_handler('TokenPurchaseV1', self._handle_event)
            self.listener.register_handler('TokenSaleV1', self._handle_event)
            self.listener.register_handler('TokenPurchase2', self._handle_event)
            self.listener.register_handler('TokenSale2', self._handle_event)
            self.listener.register_handler('TradeStop', self._handle_event)
            self.listener.register_handler('LiquidityAdded', self._handle_event)

            # Optional read-only scanner pipeline. Disabled unless explicitly enabled.
            if os.getenv('SCANNER_ENABLED', 'false').strip().lower() == 'true':
                from config.scanner_config import ScannerConfig
                from src.radar.pipeline import ScannerPipeline
                from src.radar.store import ScannerStore

                scanner_config = ScannerConfig.load(os.getenv('SCANNER_CONFIG') or None)

                # Optional Telegram signal channel. Read-only: it can only send messages,
                # never place an order, and it stays disabled unless explicitly enabled.
                notifier = None
                try:
                    import requests

                    from config.notify_config import NotifyConfig
                    from src.notify.telegram import TelegramSignalBot

                    NotifyConfig.validate()
                    notifier = TelegramSignalBot.from_config(session=requests.Session())
                    if notifier.ready:
                        logger.info(
                            "📣 Telegram signals enabled (actions=%s, dedupe=%.0fs)",
                            ",".join(notifier.actions), notifier.dedupe_seconds,
                        )
                    else:
                        logger.info("📣 Telegram signals off: %s", notifier.not_ready_reason())
                except Exception as exc:
                    # The switch is on, so a broken channel is an operator error, not noise.
                    level = logging.ERROR if os.getenv(
                        'TELEGRAM_SIGNAL_ENABLED', 'false'
                    ).strip().lower() in {'1', 'true', 'yes', 'y', 'on'} else logging.WARNING
                    logger.log(level, "Telegram signal channel unavailable: %s", exc)

                self.scanner = ScannerPipeline(
                    ScannerStore(os.getenv('SCANNER_DB', 'data/scanner/evidence.sqlite')),
                    scanner_config,
                    notifier=notifier,
                )
                self.listener.register_handler('TokenCreate', self._handle_scanner_event)
                self.listener.register_handler('LiquidityAdded', self._handle_scanner_event)
                # Trades drive the candidate window, so the scanner sees them for every token,
                # including tokens this process never saw created.
                for trade_event in ('TokenPurchase', 'TokenSale', 'TokenPurchaseV1', 'TokenSaleV1',
                                    'TokenPurchase2', 'TokenSale2'):
                    self.listener.register_handler(trade_event, self._handle_scanner_event)
                logger.info("🔎 Scanner pipeline enabled (read-only): %s", scanner_config.mode)
                self.signal_scans_blocked_by_mode = False
                if self.scanner_signal_events:
                    if scanner_config.mode != "safe":
                        # The decision layer refuses to buy on a learning-mode report, so
                        # scheduling scans here would only burn provider calls.
                        self.signal_scans_blocked_by_mode = True
                        logger.warning(
                            "📡 Automatic signal scans are configured but scanner mode is %r; "
                            "no buy can be authorised in that mode. Set SCANNER_CONFIG with "
                            "mode=safe to receive signals.",
                            scanner_config.mode,
                        )
                    else:
                        logger.info(
                            "📡 Automatic signal scans enabled for: %s",
                            ", ".join(sorted(self.scanner_signal_events)),
                        )
                if self.candidate_sweep_seconds > 0:
                    seeded = await self._seed_activity_from_chain()
                    recent_alerts = self._load_recent_alerts()
                    logger.info(
                        "🔎 Candidate sweep every %.0fs (max %s/hour, window %.0fmin, >= %s fresh "
                        "buyers, buy volume >= %s) | activity seeded from chain: %s events over %s "
                        "blocks | recent alerts carried over: %s",
                        self.candidate_sweep_seconds, self.candidate_max_per_hour or "unlimited",
                        self.activity_window_seconds / 60.0, self.signal_min_unique_buyers,
                        self.candidate_min_buy_volume, seeded, self._activity_seeded_blocks,
                        recent_alerts,
                    )

            restored_metadata = self.collector.load_token_metadata_index()
            if restored_metadata <= 0:
                restored_metadata = self.collector.load_token_metadata_from_lifecycle_files()
                if restored_metadata > 0:
                    self.collector.save_token_metadata_index()

            resume_cursor = self.collector.restore_runtime_state(self.state_file)
            current_block_for_resume = current_block
            if self.state_file.exists() and self.resume_max_age_seconds > 0:
                try:
                    with self.state_file.open('r', encoding='utf-8') as f:
                        state_payload = json.load(f)
                except Exception as e:
                    logger.error(f"读取 checkpoint age 失败: {e}")
                    state_payload = None

                if state_payload and self._should_skip_resume_due_to_checkpoint_age(state_payload):
                    resume_cursor = None

            resume_cursor = self._bound_resume_cursor(
                resume_cursor=resume_cursor,
                current_block=current_block_for_resume,
            )
            if restored_metadata or resume_cursor:
                logger.info(
                    f"恢复 collector 状态: metadata_tokens={restored_metadata}, "
                    f"resume_cursor={resume_cursor}"
                )

            # 启动监听和定时任务（持有任务句柄，便于退出时取消）
            if restored_metadata > 0 and not resume_cursor:
                logger.warning(
                    f"检测到 lifecycle 恢复数据，但缺少有效 runtime checkpoint: {self.state_file}. "
                    "如果这是迁服启动，请确认已同步 collector_runtime_state.json，否则 listener 会从当前链头继续。"
                )

            if resume_cursor:
                logger.info(
                    f"collector 启动恢复点: {resume_cursor} | 当前链头: {current_block_for_resume} | "
                    f"最大回追窗口: {self.resume_max_catchup_blocks} blocks"
                )

            self._event_queue = asyncio.Queue(maxsize=self.event_queue_size)

            self.listener_task = asyncio.create_task(
                self.listener.subscribe_to_events(
                    resume_cursor=resume_cursor
                )
            )
            self.collector_task = asyncio.create_task(self._collector_worker())
            self.save_task = asyncio.create_task(self._periodic_save())
            self.stats_task = asyncio.create_task(self._periodic_stats())  # 添加定期统计显示
            self.flush_task = asyncio.create_task(self._periodic_flush())
            self.checkpoint_task = asyncio.create_task(self._periodic_checkpoint())
            if self.candidate_sweep_seconds > 0:
                self.candidate_task = asyncio.create_task(self._candidate_sweep_loop())

            await asyncio.gather(
                self.listener_task,
                self.collector_task,
                self.save_task,
                self.stats_task,
                self.flush_task,
                self.checkpoint_task
            )

        except Exception as e:
            logger.error(f"收集过程出错: {e}")
            import traceback
            traceback.print_exc()

        finally:
            # 取消剩余任务，避免 listener 无限循环阻塞退出
            self.running = False

            background_tasks = [
                self.listener_task,
                self.save_task,
                self.stats_task,
                self.flush_task,
                self.checkpoint_task,
                self.candidate_task,
            ]
            pending_background_tasks = [task for task in background_tasks if task and not task.done()]
            for task in pending_background_tasks:
                task.cancel()

            if pending_background_tasks:
                await asyncio.gather(*pending_background_tasks, return_exceptions=True)

            # Let the collector worker drain any already-queued events before final flush.
            if self.collector_task and not self.collector_task.done():
                await asyncio.gather(self.collector_task, return_exceptions=True)

            # 停止前将剩余内存代币全部刷入增量文件
            try:
                flushed_remaining = self.collector.flush_all_to_incremental()
                if flushed_remaining > 0:
                    logger.info(f"退出刷盘: 已写入 {flushed_remaining} 个剩余代币")
            except Exception as flush_err:
                logger.error(f"退出刷盘失败: {flush_err}")

            # 最终保存快照（此时通常为空，用于保持现有输出行为）
            await self._save_data()
            self._persist_runtime_state()

            if self.listener:
                try:
                    await self.listener.close_log_providers()
                except Exception as log_provider_err:
                    logger.error(f"关闭 log providers 失败: {log_provider_err}")

            # 确保保存后断开连接
            if self.ws_manager:
                try:
                    await self.ws_manager.disconnect()
                except Exception as disconnect_err:
                    logger.error(f"断开连接失败: {disconnect_err}")

    def _get_last_processed_block(self) -> int:
        if not self.listener:
            return 0

        try:
            stats = self.listener.get_stats()
            return max(0, int(stats.get('last_block_processed', 0) or 0))
        except Exception as e:
            logger.error(f"读取 listener checkpoint 失败: {e}")
            return 0

    def _persist_runtime_state(self):
        metadata_path = self.collector.save_token_metadata_index()
        if metadata_path:
            logger.debug(f"已保存 token metadata index: {metadata_path}")

        applied_cursor = self.collector.get_applied_cursor()
        last_processed_block = self._get_last_processed_block()
        saved_path = self.collector.save_runtime_state(
            self.state_file,
            applied_cursor=applied_cursor,
            last_processed_block=last_processed_block,
        )
        if saved_path:
            logger.debug(
                f"已保存 collector checkpoint: {saved_path} "
                f"(applied_cursor={applied_cursor}, last_processed_block={last_processed_block})"
            )

    def _should_skip_flush_while_catching_up(self) -> bool:
        if not self.listener:
            return False

        try:
            stats = self.listener.get_stats()
        except Exception as e:
            logger.error(f"读取 listener lag 失败: {e}")
            return False

        current_block_lag = max(0, int(stats.get('current_block_lag', 0) or 0))
        if current_block_lag > self.flush_max_listener_lag_blocks:
            logger.info(
                f"listener 仍在追块，暂不做 inactivity flush: "
                f"block_lag={current_block_lag}, threshold={self.flush_max_listener_lag_blocks}"
            )
            return True

        return False

    async def _periodic_checkpoint(self):
        while self.running:
            try:
                await asyncio.sleep(self.state_checkpoint_interval_seconds)

                if not self.running:
                    break

                self._persist_runtime_state()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"保存 collector checkpoint 失败: {e}")

    async def _handle_scanner_event(self, event_name: str, event_data: dict):
        """Feed the activity window, persist launch evidence, optionally scan for a signal."""
        if self.scanner is None:
            return
        if event_name not in {"TokenCreate", "LiquidityAdded"}:
            self._record_trade_event(event_name, event_data)
            return
        await self.scanner.handle_event(event_name, event_data)
        if event_name not in self.scanner_signal_events:
            return
        from src.radar.events import launch_from_event

        launch = launch_from_event(event_name, event_data, chain=self.scanner.chain)
        if launch is None or not launch.token:
            return
        self._schedule_signal_scan(launch)

    def _record_trade(self, token: str, side: str, account: str, amount: float, ts: float) -> None:
        """Append one trade to the rolling window and drop what fell out of it."""
        token = str(token or "").lower()
        if not token or side not in {"buy", "sell"}:
            return
        trades = self._recent_trades.setdefault(token, [])
        trades.append({"ts": float(ts), "side": side, "account": str(account or "").lower(),
                       "amount": float(amount or 0.0)})
        cutoff = float(ts) - self.activity_window_seconds
        if trades[0]["ts"] < cutoff:
            self._recent_trades[token] = [trade for trade in trades if trade["ts"] >= cutoff]

    def _record_trade_event(self, event_name: str, event_data: dict) -> None:
        """Decode a listener trade event into the activity window."""
        args = event_data.get("args") or {}
        side = "buy" if "Purchase" in event_name else ("sell" if "Sale" in event_name else None)
        if side is None:
            return
        token = args.get("token") or args.get("base")
        try:
            ts = float(event_data.get("timestamp") or time.time())
        except (TypeError, ValueError):
            ts = time.time()
        try:
            amount = float(args.get("cost") or args.get("bnb_amount") or 0) / 1e18
        except (TypeError, ValueError):
            amount = 0.0
        self._record_trade(str(token or ""), side, str(args.get("account") or args.get("buyer") or ""),
                           amount, ts)

    def _schedule_signal_scan(self, launch):
        """Queue a signal scan, bounded so a busy block cannot pile up scans."""
        if self.scanner is None or self.scanner.notifier is None:
            return
        if self.signal_scans_blocked_by_mode:
            return
        token = launch.token.lower()
        now = time.time()
        last = self._signal_scan_seen.get(token)
        if last is not None and now - last < self.signal_scan_dedupe_seconds:
            return
        if len(self._signal_scan_tasks) >= self.signal_scan_max_inflight:
            logger.info(f"📡 Signal scan queue is full; skipping {token}")
            return
        self._signal_scan_seen[token] = now
        if len(self._signal_scan_seen) > 10_000:
            cutoff = now - self.signal_scan_dedupe_seconds
            self._signal_scan_seen = {k: v for k, v in self._signal_scan_seen.items() if v >= cutoff}
        task = asyncio.create_task(self._run_signal_scan(launch))
        self._signal_scan_tasks.add(task)
        task.add_done_callback(self._signal_scan_tasks.discard)

    @staticmethod
    def _lifecycle_index(lifecycles) -> dict:
        """Lowercase-keyed view of the lifecycle map.

        The collector stores addresses in the checksummed form the event carried, while the
        candidate watch set keys them lowercase; without this, every sweep lookup misses and
        no candidate is ever pushed.
        """
        return {str(key).lower(): value for key, value in (lifecycles or {}).items()}

    @staticmethod
    def _flow_stats_from_lifecycle(lifecycle: dict, *, min_unique_buyers: int) -> dict | None:
        """Coarse on-chain demand numbers from one lifecycle record.

        Not the wallet-flow model: it counts distinct wallets that bought and have not
        sold, and compares buy volume with sell volume. A wallet that already sold does
        not count as fresh demand. Both volumes are in the token's own quote asset, which
        is fine for a ratio but is not a USD number.
        """
        if not lifecycle:
            return None
        buys = lifecycle.get("buys") or []
        sells = lifecycle.get("sells") or []
        sold_by = {str(item.get("account", "")).lower() for item in sells if item.get("account")}
        buyers = {str(item.get("account", "")).lower() for item in buys if item.get("account")}
        fresh = buyers - sold_by
        buy_volume = sum(float(item.get("bnb_amount", 0.0) or 0.0) for item in buys)
        sell_volume = sum(float(item.get("bnb_amount", 0.0) or 0.0) for item in sells)
        created = lifecycle.get("create_timestamp")
        try:
            age = max(0.0, time.time() - float(created)) if created else None
        except (TypeError, ValueError):
            age = None
        return {
            "fresh_buyers": len(fresh),
            "buyers": len(buyers),
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "age_seconds": age,
            "funding_confirmed": len(fresh) >= int(min_unique_buyers) and buy_volume > sell_volume,
        }

    def _flow_stats_for(self, token_address: str) -> dict | None:
        lifecycle = self._lifecycle_index(
            getattr(self.collector, "token_lifecycle", None)
        ).get(str(token_address).lower())
        return self._flow_stats_from_lifecycle(
            lifecycle, min_unique_buyers=self.signal_min_unique_buyers
        )

    def _funding_confirmed_for(self, token_address: str) -> bool:
        stats = self._flow_stats_for(token_address)
        return bool(stats and stats["funding_confirmed"])

    async def _candidate_sweep_loop(self):
        """Re-check watched tokens and push the ones that attracted real buyers."""
        while self.running:
            try:
                await asyncio.sleep(self.candidate_sweep_seconds)
                if not self.running or self.scanner is None:
                    break
                summary = await asyncio.to_thread(self._sweep_candidates)
                detail = (" -> " + ", ".join(summary["tokens"])) if summary.get("tokens") else ""
                top = (" | top by volume: " + summary["top"]) if summary.get("top") else ""
                logger.info(
                    f"🔎 Candidate sweep: watched={summary['watched']} over_bar={summary['over_bar']} "
                    f"suppressed={summary['suppressed']} pushed={summary['pushed']}{detail}{top}"
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"候选扫链失败: {exc}")

    def _fetch_recent_trade_logs(self, from_block: int, to_block: int) -> list:
        """Read Four.meme trade logs straight from an HTTP endpoint, through the proxy."""
        import requests
        from web3 import Web3

        from src.data.fourmeme_log_decoder import TRADE_TOPICS

        endpoints = list(Config.get_log_http_pool() or [])
        if not endpoints:
            return []
        contract = Web3.to_checksum_address(Config.get_contract_config()["contract_address"])
        proxy = Config.get_local_proxy_url()
        session = requests.Session()
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})
        logs = []
        try:
            for start_block in range(from_block, to_block + 1, 200):
                # TRADE_TOPICS keys are bare hashes; eth_getLogs wants 0x-prefixed hex.
                topics = ["0x" + topic if not topic.startswith("0x") else topic
                          for topic in TRADE_TOPICS]
                payload = {
                    "jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
                    "params": [{
                        "fromBlock": hex(start_block), "toBlock": hex(min(start_block + 199, to_block)),
                        "address": contract, "topics": [topics],
                    }],
                }
                response = session.post(endpoints[0], json=payload, timeout=30)
                response.raise_for_status()
                body = response.json()
                if "error" in body:
                    raise RuntimeError(body["error"])
                logs.extend(body.get("result") or [])
        finally:
            session.close()
        return logs

    async def _seed_activity_from_chain(self) -> int:
        """Fill the activity window from the chain so a restart does not start blind.

        Live events only cover the current process, and the tokens carrying real money are
        often hours old, so replaying the window from the chain is the only honest way to
        start. Timestamps are approximated from block distance at ~3s per BSC block.
        """
        from src.data.fourmeme_log_decoder import decode_fourmeme_log

        if self.listener is None or self.listener.w3 is None:
            return 0
        try:
            head = int(await self.listener.w3.eth.block_number)
        except Exception as exc:
            logger.warning(f"🔎 Activity seed skipped, no chain head: {exc}")
            return 0
        from_block = max(0, head - int(self.activity_window_seconds / 3.0) - 60)
        try:
            logs = await asyncio.to_thread(self._fetch_recent_trade_logs, from_block, head)
        except Exception as exc:
            logger.warning(f"🔎 Activity seed failed: {exc}")
            return 0
        now = time.time()
        seeded = 0
        for log in logs:
            decoded = decode_fourmeme_log(log)
            if not decoded:
                continue
            name, args = decoded
            side = "buy" if "Purchase" in name else ("sell" if "Sale" in name else None)
            if side is None:
                continue
            try:
                block = int(log.get("blockNumber"), 16)
                amount = float(args.get("cost") or args.get("bnb_amount") or 0) / 1e18
            except (TypeError, ValueError):
                continue
            self._record_trade(str(args.get("token") or args.get("base") or ""), side,
                               str(args.get("account") or args.get("buyer") or ""), amount,
                               now - max(0, head - block) * 3.0)
            seeded += 1
        self._activity_seeded_blocks = max(0, head - from_block)
        return seeded

    def _load_recent_alerts(self, within_seconds: float = 3600.0) -> int:
        """Carry alert dedupe across a restart, so the channel does not repeat itself."""
        if self.scanner is None or self.scanner.store is None:
            return 0
        try:
            rows = self.scanner.store.rows("candidate_alert", limit=10_000)
        except Exception as exc:
            logger.warning(f"🔎 Could not read recent candidate alerts: {exc}")
            return 0
        cutoff = time.time() - within_seconds
        loaded = 0
        for row in rows:
            try:
                observed = float(row.get("observed_at") or 0.0)
            except (TypeError, ValueError):
                continue
            if observed < cutoff:
                continue
            token = str(row.get("entity") or "").lower()
            if token:
                self._candidate_alerted[token] = observed
                loaded += 1
        return loaded

    def _sweep_candidates(self) -> dict:
        """Alert on tokens that real money is buying right now. Runs in a worker thread.

        The window is what matters, not the token's age: every token with meaningful volume in
        the 2026-09-22 sample had been created hours earlier, so an age filter on creation time
        excluded all of them, and a creation-event watch list never saw them at all.
        """
        if self.scanner is None or self.scanner.notifier is None:
            return {"watched": 0, "over_bar": 0, "suppressed": 0, "pushed": 0, "tokens": [], "top": ""}
        now = time.time()
        cutoff = now - self.activity_window_seconds
        pushed_this_hour = sum(1 for ts in self._candidate_alerted.values() if now - ts < 3600)
        pushed = 0
        over_bar = 0
        suppressed = 0
        pushed_tokens = []
        seen = []
        for token in list(self._recent_trades):
            trades = [trade for trade in self._recent_trades.get(token, []) if trade["ts"] >= cutoff]
            if not trades:
                self._recent_trades.pop(token, None)
                continue
            buyers = {trade["account"] for trade in trades if trade["side"] == "buy" and trade["account"]}
            sellers = {trade["account"] for trade in trades if trade["side"] == "sell" and trade["account"]}
            fresh_buyers = buyers - sellers
            buy_volume = sum(trade["amount"] for trade in trades if trade["side"] == "buy")
            sell_volume = sum(trade["amount"] for trade in trades if trade["side"] == "sell")
            seen.append((buy_volume, token, len(fresh_buyers), len(trades)))
            if len(fresh_buyers) < self.signal_min_unique_buyers:
                continue
            if buy_volume < self.candidate_min_buy_volume or buy_volume <= sell_volume:
                if len(fresh_buyers) >= self.signal_min_unique_buyers:
                    logger.info(
                        f"🔎 Not alerting {token[:12]}: buy_volume={buy_volume:.2f} "
                        f"sell_volume={sell_volume:.2f} fresh_buyers={len(fresh_buyers)} "
                        f"trades={len(trades)}"
                    )
                continue
            over_bar += 1
            if token in self._candidate_alerted:
                suppressed += 1
                continue
            if self.candidate_max_per_hour and pushed_this_hour >= self.candidate_max_per_hour:
                logger.info("🔎 Candidate alert cap reached for this hour; skipping the rest")
                break
            stats = {
                "fresh_buyers": len(fresh_buyers),
                "buyers": len(buyers),
                "buy_volume": buy_volume,
                "sell_volume": sell_volume,
                "window_seconds": self.activity_window_seconds,
                "age_seconds": now - min(trade["ts"] for trade in trades),
                "funding_confirmed": True,
            }
            meta = self._recent_trade_meta.get(token) or {}
            if self.scanner.announce_candidate(
                token,
                stats=stats,
                symbol=str(meta.get("symbol") or "") or None,
                name=str(meta.get("name") or "") or None,
            ):
                self._candidate_alerted[token] = now
                pushed_this_hour += 1
                pushed += 1
                pushed_tokens.append(f"{token[:12]}({stats['fresh_buyers']}b/{buy_volume:.1f})")
        top = ", ".join(f"{token[:12]}({buyers}b/{volume:.1f})"
                        for volume, token, buyers, _ in sorted(seen, reverse=True)[:3])
        return {"watched": len(self._recent_trades), "over_bar": over_bar,
                "suppressed": suppressed, "pushed": pushed, "tokens": pushed_tokens, "top": top}

    async def _run_signal_scan(self, launch):
        """Run the read-only scan off the event loop: the safety fetchers use sync HTTP."""
        try:
            funding = self._funding_confirmed_for(launch.token)
            decision = await asyncio.to_thread(
                self.scanner.scan, launch.token, funding_confirmed=funding,
                symbol=None, name=None,
            )
            logger.info(
                f"📡 Signal scan {launch.token} -> {decision.action} "
                f"({','.join(decision.reason_codes) or 'no_reason_codes'})"
            )
        except Exception as exc:
            logger.warning(f"📡 Signal scan failed for {launch.token}: {exc}")

    async def _handle_event(self, event_name: str, event_data: dict):
        """监听器回调仅入队，避免在回调中做重处理。"""
        if self._event_queue is None:
            self._event_queue = asyncio.Queue(maxsize=self.event_queue_size)

        try:
            self._event_queue.put_nowait((event_name, event_data))
            self.events_enqueued += 1
        except asyncio.QueueFull:
            await self._event_queue.put((event_name, event_data))
            self.events_enqueued += 1

    async def _collector_worker(self):
        """批量消费事件队列并更新 collector。"""
        logger.info("📥 Collector worker started")
        if self._event_queue is None:
            self._event_queue = asyncio.Queue(maxsize=self.event_queue_size)

        while self.running or (self._event_queue is not None and not self._event_queue.empty()):
            try:
                event_name, event_data = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if not self.running and (self._event_queue is None or self._event_queue.empty()):
                    break
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"collector worker wait error: {e}")
                continue

            try:
                batch = [(event_name, event_data)]
                for _ in range(self.collector_batch_size - 1):
                    try:
                        applied = False
                        batch.append(self._event_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                for evt_name, evt_data in batch:
                    try:
                        if evt_name == 'TokenCreate':
                            applied = self.collector.on_token_create(evt_data)
                        elif 'Purchase' in evt_name:
                            applied = self.collector.on_token_purchase(evt_data)
                        elif 'Sale' in evt_name:
                            applied = self.collector.on_token_sale(evt_data)
                        elif evt_name == 'TradeStop':
                            applied = self.collector.on_trade_stop(evt_data)

                        if applied:
                            self.events_processed += 1
                    except Exception as e:
                        logger.error(f"处理事件失败 {evt_name}: {e}")

                await asyncio.sleep(0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"collector worker process error: {e}")

    async def _periodic_save(self):
        """定期保存数据"""
        save_count = 0
        while self.running:
            try:
                # 等待保存间隔
                await asyncio.sleep(self.save_interval_hours * 3600)

                if not self.running:
                    break

                # 保存数据
                await self._save_data()
                save_count += 1

                logger.info(f"已自动保存 {save_count} 次")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定期保存失败: {e}")

    async def _periodic_stats(self):
        """定期显示监控统计"""
        while self.running:
            try:
                # 每5分钟显示一次统计
                await asyncio.sleep(300)
                
                if not self.running or not self.listener:
                    break
                
                # 获取listener统计
                listener_stats = self.listener.get_stats()
                
                # 获取collector统计
                collector_stats = self.collector.get_stats()
                
                # 获取当前区块
                try:
                    current_block = await self.listener.w3.eth.block_number
                    block_lag = current_block - listener_stats['last_block_processed']
                except Exception as exc:
                    # A failed block_number call only affects this status line.
                    logger.debug(f"block_number unavailable for the status report: {exc}")
                    current_block = 0
                    block_lag = 0
                
                logger.info("="*70)
                logger.info("📊 监控状态报告")
                logger.info(f"  当前区块: {current_block}")
                logger.info(f"  已处理区块: {listener_stats['last_block_processed']}")
                logger.info(f"  区块落后: {block_lag} blocks")
                logger.info(f"  历史最大落后: {listener_stats['max_block_lag']} blocks")
                logger.info(f"  跳过区块数: {listener_stats['blocks_skipped']}")
                logger.info(f"  连接错误次数: {listener_stats['connection_errors']}")
                logger.info(f"  已处理事件: {listener_stats['events_processed']}")
                logger.info(f"  追踪代币数: {collector_stats['tokens_tracked']}")
                logger.info(f"  内存代币数: {collector_stats['tokens_in_memory']}")
                logger.info(f"  已刷盘代币数: {collector_stats['tokens_flushed']}")
                
                # 健康状态判断
                if block_lag > 100:
                    logger.warning(f"  ⚠️ 警告: 区块落后过多 ({block_lag} blocks)")
                if listener_stats['blocks_skipped'] > 0:
                    logger.warning(f"  ⚠️ 警告: 已跳过 {listener_stats['blocks_skipped']} 个区块")
                if listener_stats['connection_errors'] > 10:
                    logger.warning(f"  ⚠️ 警告: 连接错误次数较多 ({listener_stats['connection_errors']})")
                
                logger.info("="*70)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"显示统计失败: {e}")

    async def _periodic_flush(self):
        """定期将不活跃代币刷盘并从内存移除"""
        while self.running:
            try:
                await asyncio.sleep(self.flush_check_interval_seconds)

                if not self.running:
                    break

                if self._should_skip_flush_while_catching_up():
                    self._persist_runtime_state()
                    continue

                now = int(time.time())
                flushed = self.collector.flush_eligible_tokens(
                    current_time=now,
                    min_age_seconds=self.flush_min_age_seconds,
                    inactivity_seconds=self.flush_inactivity_seconds,
                )
                if flushed > 0:
                    stats = self.collector.get_stats()
                    logger.info(
                        f"内存清理: 本次刷盘 {flushed} 个代币 | "
                        f"内存代币={stats['tokens_in_memory']} | 已刷盘={stats['tokens_flushed']}"
                )
                self._persist_runtime_state()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定期刷盘失败: {e}")

    async def _save_data(self):
        """保存数据"""
        try:
            output_file = self.collector.save_lifecycle_data()
            stats = self.collector.get_stats()

            logger.info("-"*70)
            logger.info(f"快照已保存: {output_file}")
            logger.info(f"增量文件: {stats['incremental_output_file']}")
            logger.info(f"统计: 追踪代币={stats['tokens_tracked']}, "
                       f"内存代币={stats['tokens_in_memory']}, 已刷盘={stats['tokens_flushed']}")
            logger.info("-"*70)

            # 清理旧的lifecycle文件,只保留最新的2个
            self._cleanup_old_files()
            self._persist_runtime_state()

        except Exception as e:
            logger.error(f"保存数据失败: {e}")

    def _cleanup_old_files(self, keep_count=2):
        """清理旧的lifecycle文件,只保留最新的N个"""
        try:
            # DataCollector's output_dir defaults to 'data/training'
            collector_dir = Path(project_root) / 'data' / 'training'
            if not collector_dir.exists():
                return

            # 获取快照文件（不清理 incremental 文件）
            lifecycle_files = sorted(
                collector_dir.glob('lifecycle_[0-9]*.jsonl'),
                key=lambda x: x.stat().st_mtime,
                reverse=True  # 按修改时间降序排序
            )

            # 删除除最新N个之外的所有文件
            if len(lifecycle_files) > keep_count:
                files_to_delete = lifecycle_files[keep_count:]
                for file in files_to_delete:
                    file.unlink()
                    logger.info(f"已删除旧文件: {file.name}")

                logger.info(f"清理完成,保留了最新的 {keep_count} 个lifecycle文件")

        except Exception as e:
            logger.error(f"清理旧文件失败: {e}")

    def _signal_handler(self, signum, frame):
        """信号处理 (Ctrl+C)"""
        logger.info("\n接收到停止信号, 正在保存数据...")
        self.running = False

        # 主动取消任务，确保 listener 的无限循环不会阻塞退出
        for task in (
            self.listener_task,
            self.save_task,
            self.stats_task,
            self.flush_task,
            self.checkpoint_task,
            self.candidate_task,
        ):
            if task and not task.done():
                task.cancel()


async def main():
    """主函数"""
    collector = ContinuousCollector()
    await collector.start()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n程序已停止")

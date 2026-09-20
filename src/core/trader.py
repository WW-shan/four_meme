"""
Trade Executor
交易执行器 - 负责买入和卖出操作
"""

import logging
import asyncio
import os
import time
import contextlib
from typing import Optional
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider
from eth_account import Account
from config.config import Config
from config.trading_config import TradingConfig

logger = logging.getLogger(__name__)

# 常量定义
TOKEN_MANAGER_HELPER = "0xF251F83e40a78868FcfA3FA4599Dad6494E46034"
from src.data.fourmeme_quote import (
    KNOWN_QUOTE_ASSETS,
    NATIVE_QUOTE_ADDRESS,
    classify_quote,
)


def compute_buy_min_amount(
    buy_amount_bnb: float,
    expected_price: float,
    decimals: int,
    slippage_percent: int,
) -> Optional[int]:
    """Return the minimum base-token amount for buyMemeToken, or None if unpriceable."""
    try:
        if buy_amount_bnb <= 0 or expected_price <= 0 or decimals < 0:
            return None
        expected_tokens = (float(buy_amount_bnb) / float(expected_price)) * (10 ** int(decimals))
        minimum = int(expected_tokens * (100 - int(slippage_percent)) / 100)
        return max(1, minimum)
    except (TypeError, ValueError, OverflowError):
        return None


def compute_sell_min_out(expected_out: int, slippage_percent: int) -> Optional[int]:
    """Return the minimum quote amount for an AMM sell, or None if unpriceable."""
    try:
        expected_out = int(expected_out)
        if expected_out <= 0:
            return None
        return max(1, expected_out * (100 - int(slippage_percent)) // 100)
    except (TypeError, ValueError, OverflowError):
        return None
TOKEN_MANAGER_HELPER_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "token", "type": "address"}],
        "name": "getTokenInfo",
        "outputs": [
            {"internalType": "uint256", "name": "version", "type": "uint256"},
            {"internalType": "address", "name": "tokenManager", "type": "address"},
            {"internalType": "address", "name": "quote", "type": "address"},
            {"internalType": "uint256", "name": "lastPrice", "type": "uint256"},
            {"internalType": "uint256", "name": "tradingFeeRate", "type": "uint256"},
            {"internalType": "uint256", "name": "minTradingFee", "type": "uint256"},
            {"internalType": "uint256", "name": "launchTime", "type": "uint256"},
            {"internalType": "uint256", "name": "offers", "type": "uint256"},
            {"internalType": "uint256", "name": "maxOffers", "type": "uint256"},
            {"internalType": "uint256", "name": "funds", "type": "uint256"},
            {"internalType": "uint256", "name": "maxFunds", "type": "uint256"},
            {"internalType": "bool", "name": "liquidityAdded", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

# TOKEN_MANAGER ABI (用于卖出)
TOKEN_MANAGER_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "token", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"}
        ],
        "name": "sellToken",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "token", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"}
        ],
        "name": "saleToken",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

MEME_ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenManager", "type": "address"},
            {"internalType": "address", "name": "token", "type": "address"},
            {"internalType": "address", "name": "recipient", "type": "address"},
            {"internalType": "uint256", "name": "funds", "type": "uint256"},
            {"internalType": "uint256", "name": "minAmount", "type": "uint256"}
        ],
        "name": "buyMemeToken",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function"
    }
]

# PancakeSwap V2 Router (毕业代币通过DEX卖出)
PANCAKE_ROUTER_V2 = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
PANCAKE_ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"}
        ],
        "name": "swapExactTokensForETHSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]


class TradeExecutor:
    """交易执行器 - 使用独立 HTTP RPC 连接，不依赖 WSS"""

    # HTTP RPC 节点列表（用于发交易）
    # 优先读取 BSC_TRADE_HTTP_RPC（逗号分隔多个节点）
    # 若未设置则回退到 BSC_HTTP_RPC
    @staticmethod
    def _get_http_endpoints():
        trade_rpcs = os.getenv('BSC_TRADE_HTTP_RPC', '')
        if trade_rpcs:
            endpoints = [url.strip() for url in trade_rpcs.split(',') if url.strip()]
            if endpoints:
                return endpoints

        legacy_rpcs = os.getenv('BSC_HTTP_RPC', '')
        if legacy_rpcs:
            endpoints = [url.strip() for url in legacy_rpcs.split(',') if url.strip()]
            if endpoints:
                return endpoints

        # 默认节点
        return [
            'https://bsc-dataseed.binance.org',
            'https://bsc-dataseed1.defibit.io',
            'https://bsc-dataseed1.ninicoin.io',
            'https://rpc.ankr.com/bsc',
        ]

    def __init__(self, w3: AsyncWeb3):
        # 创建独立的 HTTP w3 用于发送交易（不依赖 WSS）
        self._ws_w3 = w3  # 保留引用，仅用于 fallback
        self.HTTP_RPC_ENDPOINTS = self._get_http_endpoints()
        self.w3 = self._create_http_w3()
        logger.info(f"TradeExecutor using independent HTTP RPC: {self.HTTP_RPC_ENDPOINTS[0]}")
        self.contract_address = Config.FOURMEME_CONTRACT
        self.router_address = os.getenv('MEME_ROUTER', '0xc205f591D395d59ad5bcB8bD824d8FA67ab4d15A')

        # 合约实例（使用独立 HTTP w3）
        self.helper = self.w3.eth.contract(
            address=self.w3.to_checksum_address(TOKEN_MANAGER_HELPER),
            abi=TOKEN_MANAGER_HELPER_ABI
        )
        self.router = self.w3.eth.contract(
            address=self.w3.to_checksum_address(self.router_address),
            abi=MEME_ROUTER_ABI
        )
        self.token_manager = self.w3.eth.contract(
            address=self.w3.to_checksum_address(self.contract_address),
            abi=TOKEN_MANAGER_ABI
        )
        # PancakeSwap V2 Router（毕业代币 fallback）
        self.pancake_router = self.w3.eth.contract(
            address=self.w3.to_checksum_address(PANCAKE_ROUTER_V2),
            abi=PANCAKE_ROUTER_ABI
        )

        # 钱包设置
        self.account: Optional[Account] = None
        self.wallet_address: Optional[str] = None

        if TradingConfig.ENABLE_TRADING:
            if not TradingConfig.PRIVATE_KEY:
                raise ValueError("ENABLE_TRADING=true but PRIVATE_KEY not set")
            self.account = Account.from_key(TradingConfig.PRIVATE_KEY)
            self.wallet_address = self.account.address
            logger.info(f"Trading enabled with wallet: {self.wallet_address}")
        else:
            logger.info("Trading disabled (ENABLE_TRADING=false)")

        self.gas_multiplier = TradingConfig.GAS_MULTIPLIER
        self.nonce_lock = asyncio.Lock()
        self.local_nonce = None

        # Gas Price Cache
        self.cached_gas_price = None
        self.last_gas_update = 0
        self._approval_tasks = {}
        self._approved_token_amounts = {}
        self._gas_price_task = asyncio.create_task(self._gas_price_updater())

    def _create_http_w3(self) -> AsyncWeb3:
        """创建独立的 HTTP Web3 实例，用于发送交易"""
        provider = AsyncHTTPProvider(
            self.HTTP_RPC_ENDPOINTS[0],
            request_kwargs=Config.get_http_request_kwargs(),
        )
        return AsyncWeb3(provider)

    async def close(self):
        """Close background tasks and HTTP provider sessions."""
        gas_price_task = getattr(self, '_gas_price_task', None)
        if gas_price_task is not None:
            gas_price_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await gas_price_task

        approval_tasks = list(getattr(self, '_approval_tasks', {}).values())
        for task in approval_tasks:
            if task is not None and not task.done():
                task.cancel()
        if approval_tasks:
            await asyncio.gather(*approval_tasks, return_exceptions=True)
            self._approval_tasks.clear()

        provider = getattr(getattr(self, 'w3', None), 'provider', None)
        disconnect = getattr(provider, 'disconnect', None)
        if disconnect is None:
            return
        result = disconnect()
        if asyncio.iscoroutine(result):
            await result

    async def _gas_price_updater(self):
        """Background task to keep gas price fresh"""
        # BSC固定gas price: 不使用RPC返回值 (常返回过高值)
        BASE_GAS_PRICE_GWEI = TradingConfig.BASE_GAS_PRICE_GWEI
        MAX_GAS_PRICE_GWEI = TradingConfig.MAX_GAS_PRICE_GWEI
        base_gas_wei = self.w3.to_wei(BASE_GAS_PRICE_GWEI, 'gwei')

        while True:
            try:
                # 使用固定gas price,忽略RPC建议
                gas_with_multiplier = int(base_gas_wei * self.gas_multiplier)
                max_gas_wei = self.w3.to_wei(MAX_GAS_PRICE_GWEI, 'gwei')

                # 确保不超过最大值
                self.cached_gas_price = min(gas_with_multiplier, int(max_gas_wei))
                self.last_gas_update = time.time()

                final_gwei = self.w3.from_wei(self.cached_gas_price, 'gwei')
                logger.debug(f"Gas price: {BASE_GAS_PRICE_GWEI} Gwei * {self.gas_multiplier} = {final_gwei:.4f} Gwei (max {MAX_GAS_PRICE_GWEI})")
            except Exception as e:
                logger.debug(f"Gas price update failed: {e}")
            await asyncio.sleep(2) # Update every 2 seconds

    async def prefetch_next_nonce(self):
        """Warm the nonce cache so the buy path does not wait on getTransactionCount."""
        if not self.wallet_address:
            return None
        async with self.nonce_lock:
            if self.local_nonce is None:
                self.local_nonce = await self.w3.eth.get_transaction_count(self.wallet_address)
            return self.local_nonce

    async def _get_next_nonce(self):
        """Thread-safe nonce manager"""
        if not self.wallet_address:
            return 0
        async with self.nonce_lock:
            if self.local_nonce is None:
                self.local_nonce = await self.w3.eth.get_transaction_count(self.wallet_address)
            nonce = self.local_nonce
            self.local_nonce += 1
            return nonce

    async def _wait_for_tx(self, tx_hash: str, timeout: int = 60) -> bool:
        """等待交易确认"""
        try:
            receipt = await self.w3.eth.wait_for_transaction_receipt(
                tx_hash,
                timeout=timeout,
                poll_latency=max(0.1, float(getattr(TradingConfig, "TX_RECEIPT_POLL_LATENCY_SECONDS", 0.1))),
            )
            if receipt['status'] == 1:
                logger.info(f"✅ Transaction confirmed in block {receipt['blockNumber']}")
                return True
            else:
                logger.error(f"❌ Transaction failed (reverted)")
                return False
        except Exception as e:
            logger.error(f"❌ Error waiting for transaction {tx_hash}: {e}")
            return False

    def _get_raw_tx(self, signed_tx):
        """兼容性获取 rawTransaction"""
        for attr in ['rawTransaction', 'raw_transaction']:
            raw = getattr(signed_tx, attr, None)
            if raw is not None:
                return raw
        return signed_tx

    async def _get_token_info_from_helper(self, token_address: str, retries: int = 2) -> Optional[dict]:
        """使用 Helper 获取代币信息，新代币可能需要短暂重试"""
        for attempt in range(retries + 1):
            try:
                data = await self.helper.functions.getTokenInfo(token_address).call()
                return {
                    'version': data[0],
                    'tokenManager': data[1],
                    'quote': data[2],
                    'lastPrice': data[3],
                    'launchTime': data[6],
                    'offers': data[7],
                    'maxOffers': data[8],
                    'funds': data[9],
                    'maxFunds': data[10],
                    'liquidityAdded': data[11]
                }
            except Exception as e:
                err_str = str(e)
                if attempt < retries and (not err_str or 'revert' in err_str.lower()):
                    # 新 token 可能还未注册到 Helper，等一下重试
                    await asyncio.sleep(max(0.1, float(getattr(TradingConfig, "BUY_CONFIRM_POLL_INTERVAL_SECONDS", 0.25))))
                    continue
                if attempt == retries:
                    logger.warning(f"⚠️ Helper query failed for {token_address} (attempt {attempt+1}): type={type(e).__name__}, msg={err_str[:200]}")
                return None

    @staticmethod
    def _quote_asset_to_hex(quote) -> str:
        if quote is None:
            return ""
        if hasattr(quote, "hex"):
            raw = quote.hex()
        else:
            raw = str(quote)
        if raw.startswith("0x"):
            return raw
        if len(raw) == 40:
            return f"0x{raw}"
        return raw

    @classmethod
    def _unsupported_quote_reason(cls, quote) -> Optional[str]:
        quote_hex = cls._quote_asset_to_hex(quote)
        if not quote_hex:
            return "Unknown quote asset"
        normalized = quote_hex.lower()
        if normalized != NATIVE_QUOTE_ADDRESS:
            name = classify_quote(normalized)
            label = quote_hex if name == "unknown" else name
            return f"Unsupported quote asset: {label}"
        return None

    async def check_token_quote_supported(self, token_address: str) -> dict:
        status = {'ready': False, 'quote': None, 'reason': ''}
        try:
            info = await self._get_token_info_from_helper(token_address)
            if not info:
                status['reason'] = 'Helper query failed for quote asset'
                return status

            quote = self._quote_asset_to_hex(info.get('quote'))
            status['quote'] = quote
            reason = self._unsupported_quote_reason(quote)
            if reason:
                status['reason'] = reason
                return status

            status['ready'] = True
            status['reason'] = 'OK'
        except Exception as e:
            status['reason'] = f'Helper query failed for quote asset: {str(e)[:100]}'
        return status

    async def check_token_status(self, token_address: str) -> dict:
        """检查代币状态 (Exists, Ready, Price, LaunchTime, Graduated)"""
        status = {'exists': False, 'ready': False, 'price': 0, 'launch_time': 0, 'reason': ''}

        try:
            info = await self._get_token_info_from_helper(token_address)
            if not info:
                code = await self.w3.eth.get_code(token_address)
                if len(code) <= 2:
                    status['reason'] = 'Token contract not deployed'
                else:
                    status['exists'] = True
                    status['reason'] = 'Helper query failed'
                return status

            status['exists'] = True
            status['quote'] = self._quote_asset_to_hex(info.get('quote'))
            status['price'] = info['lastPrice']
            status['launch_time'] = info['launchTime']

            unsupported_quote_reason = self._unsupported_quote_reason(info.get('quote'))
            if unsupported_quote_reason:
                status['reason'] = unsupported_quote_reason
                return status

            current_time = int(time.time())
            if info['launchTime'] > current_time:
                status['reason'] = f"Not launched yet ({info['launchTime']} > {current_time})"
                return status

            if info['lastPrice'] <= 0:
                status['reason'] = 'Price is 0'
                return status

            if info['liquidityAdded'] or (info['maxFunds'] > 0 and info['funds'] >= info['maxFunds']):
                status['reason'] = 'Graduated/Liquidity Added'
                return status

            status['ready'] = True
            status['reason'] = 'OK'
            return status

        except Exception as e:
            status['reason'] = f'Check failed: {str(e)[:100]}'
            return status

    async def buy_token(self, token_address: str, buy_amount_bnb: float, expected_price: float = 0, skip_estimate: bool = False, wait: bool = True) -> Optional[str]:
        """买入代币"""
        if not TradingConfig.ENABLE_TRADING:
            logger.warning(f"Simulated buy: {token_address} for {buy_amount_bnb} BNB")
            return f"0xmock_buy_{int(time.time())}" if TradingConfig.ENABLE_BACKTEST else None

        try:
            logger.info(f"Buying {token_address} with {buy_amount_bnb} BNB")

            # Get cached gas price (or fetch if not available)
            if self.cached_gas_price and (time.time() - self.last_gas_update) < 10:
                gas_price = self.cached_gas_price
                gas_price_raw = int(gas_price / self.gas_multiplier)
            else:
                # Fallback: use fixed base gas price with max limit
                BASE_GAS_PRICE_GWEI = TradingConfig.BASE_GAS_PRICE_GWEI
                MAX_GAS_PRICE_GWEI = TradingConfig.MAX_GAS_PRICE_GWEI
                base_gas = self.w3.to_wei(BASE_GAS_PRICE_GWEI, 'gwei')
                gas_price_raw = base_gas
                gas_price = min(int(base_gas * self.gas_multiplier), self.w3.to_wei(MAX_GAS_PRICE_GWEI, 'gwei'))

            # 记录gas价格 (转换为Gwei)
            gas_gwei = self.w3.from_wei(gas_price_raw, 'gwei')
            gas_gwei_with_multiplier = self.w3.from_wei(gas_price, 'gwei')
            logger.info(f"⛽ Gas: {gas_gwei:.4f} Gwei -> {gas_gwei_with_multiplier:.4f} Gwei (x{self.gas_multiplier})")

            nonce = await self._get_next_nonce()

            value_wei = self.w3.to_wei(buy_amount_bnb, 'ether')

            min_amount = compute_buy_min_amount(
                buy_amount_bnb,
                expected_price,
                TradingConfig.FOURMEME_TOKEN_DECIMALS,
                TradingConfig.BUY_SLIPPAGE_PERCENT,
            )
            if min_amount is None:
                min_amount = TradingConfig.BUY_MIN_AMOUNT_FLOOR
                logger.warning(
                    "buy minAmount falling back to configured floor=%s because "
                    "expected_price=%s is not a verified positive price",
                    min_amount,
                    expected_price,
                )
            else:
                logger.info(
                    "buy minAmount=%s computed from expected_price=%s slippage=%s%%",
                    min_amount,
                    expected_price,
                    TradingConfig.BUY_SLIPPAGE_PERCENT,
                )

            func = self.router.functions.buyMemeToken(
                self.contract_address, token_address, self.wallet_address, value_wei, min_amount
            )

            if skip_estimate:
                gas_limit = 1000000 # 增加预设 Gas 到 100W 以防止复杂合约 Revert
            else:
                try:
                    gas_limit = int(await func.estimate_gas({'from': self.wallet_address, 'value': value_wei}) * 1.5)
                except Exception as e:
                    error_str = str(e).lower()
                    if 'execution reverted' in error_str:
                        logger.error(f"❌ Buy estimate reverted: {e}")
                        if 'allowance' in error_str:
                            logger.info("Attempting to approve TOKEN_MANAGER...")
                            try:
                                await self._ensure_approve(self.contract_address, 2**256 - 1)
                                gas_limit = int(await func.estimate_gas({'from': self.wallet_address, 'value': value_wei}) * 1.2)
                                logger.info(f"Gas estimation succeeded after approval: {gas_limit}")
                            except Exception as e2:
                                logger.error(f"❌ Still failed after approval: {e2}")
                                return None
                        else:
                            return None
                    else:
                        gas_limit = 500000

            tx = await func.build_transaction({
                'from': self.wallet_address, 'value': value_wei, 'gas': gas_limit,
                'gasPrice': gas_price, 'nonce': nonce, 'chainId': 56
            })

            signed = self.account.sign_transaction(tx)
            tx_hash_bytes = await self.w3.eth.send_raw_transaction(self._get_raw_tx(signed))
            tx_hash = tx_hash_bytes.hex()
            logger.info(f"🚀 Buy sent: {tx_hash}")

            if wait:
                success = await self._wait_for_tx(tx_hash)
                if not success:
                    async with self.nonce_lock: self.local_nonce = None
                    return None
                return tx_hash
            else:
                return tx_hash

        except Exception as e:
            logger.error(f"❌ Buy failed: {e}")
            async with self.nonce_lock: self.local_nonce = None
            return None

    def schedule_sell_approval(self, token_address: str, amount: int):
        """Background approval warmup so later sells can skip the allowance wait."""
        if not TradingConfig.ENABLE_TRADING:
            return None

        key = self.w3.to_checksum_address(token_address).lower()
        existing = self._approval_tasks.get(key)
        if existing is not None and not existing.done():
            return existing

        task = asyncio.create_task(self._ensure_approve(token_address, amount))
        self._approval_tasks[key] = task

        def _cleanup(done_task):
            self._approval_tasks.pop(key, None)
            with contextlib.suppress(asyncio.CancelledError):
                try:
                    done_task.result()
                except Exception as exc:
                    logger.warning(f"⚠️ Background approval failed for {token_address}: {exc}")

        task.add_done_callback(_cleanup)
        return task

    def _approval_cache_key(self, token_address: str) -> str:
        try:
            return self.w3.to_checksum_address(token_address).lower()
        except Exception:
            return str(token_address).lower()

    def _cached_approval_amount(self, token_address: str) -> int:
        cache = getattr(self, "_approved_token_amounts", None)
        if not isinstance(cache, dict):
            self._approved_token_amounts = {}
            cache = self._approved_token_amounts
        return int(cache.get(self._approval_cache_key(token_address), 0) or 0)

    def _remember_approval_amount(self, token_address: str, amount: int) -> None:
        cache = getattr(self, "_approved_token_amounts", None)
        if not isinstance(cache, dict):
            self._approved_token_amounts = {}
            cache = self._approved_token_amounts
        key = self._approval_cache_key(token_address)
        cache[key] = max(int(cache.get(key, 0) or 0), int(amount or 0))

    async def sell_token(self, token_address: str, amount: int) -> Optional[str]:
        """卖出代币（带重试，逐次加gas）"""
        # 对齐到 GWEI 精度 (1e9)，否则合约 revert "Gw"
        amount = (int(amount) // 10**9) * 10**9
        if amount <= 0:
            logger.warning(f"⚠️ Amount too small after GWEI alignment, skip sell")
            return None

        if not TradingConfig.ENABLE_TRADING:
            logger.warning(f"Simulated sell: {amount} of {token_address}")
            return f"0xmock_sell_{int(time.time())}" if TradingConfig.ENABLE_BACKTEST else None

        for attempt in range(3):
            try:
                if attempt == 0:
                    approval_key = self.w3.to_checksum_address(token_address).lower()
                    approval_task = self._approval_tasks.get(approval_key)
                    if approval_task is not None:
                        try:
                            await approval_task
                        except Exception as exc:
                            logger.warning(f"⚠️ Warmed approval failed before sell, retrying inline: {exc}")
                            await self._ensure_approve(token_address, amount)
                    else:
                        await self._ensure_approve(token_address, amount)
                logger.info(f"Selling {amount} of {token_address}" + (f" (retry #{attempt+1})" if attempt > 0 else ""))

                BASE_GAS_PRICE_GWEI = TradingConfig.BASE_GAS_PRICE_GWEI
                MAX_GAS_PRICE_GWEI = TradingConfig.MAX_GAS_PRICE_GWEI
                base_gas = self.w3.to_wei(BASE_GAS_PRICE_GWEI, 'gwei')
                # 每次重试 gas 翻倍: 0.08 -> 0.16 -> 0.32
                retry_multiplier = self.gas_multiplier * (2 ** attempt)
                gas_price = min(int(base_gas * retry_multiplier), self.w3.to_wei(MAX_GAS_PRICE_GWEI * 3, 'gwei'))
                gas_gwei = self.w3.from_wei(gas_price, 'gwei')
                logger.info(f"⛽ Sell gas: {gas_gwei:.4f} Gwei" + (f" (x{2**attempt} boost)" if attempt > 0 else ""))

                nonce = await self._get_next_nonce()

                # 优先尝试 sellToken
                func = self.token_manager.functions.sellToken(token_address, int(amount))
                try:
                    gas_limit = int(await func.estimate_gas({'from': self.wallet_address}) * 1.2)
                except Exception as e:
                    logger.warning(f"⚠️ sellToken estimate failed: {e}")
                    func = self.token_manager.functions.saleToken(token_address, int(amount))
                    try:
                        gas_limit = int(await func.estimate_gas({'from': self.wallet_address}) * 1.2)
                    except Exception as e2:
                        logger.error(f"❌ Both sellToken and saleToken failed: {e2}")
                        # 查链上状态判断是否已毕业
                        info = await self._get_token_info_from_helper(token_address)
                        if info and (info['liquidityAdded'] or (info['maxFunds'] > 0 and info['funds'] >= info['maxFunds'])):
                            logger.info(f"🎓 Token graduated (liquidityAdded={info['liquidityAdded']}), switching to PancakeSwap...")
                            return await self._sell_on_pancakeswap(token_address, amount, gas_price)
                        return None

                tx = await func.build_transaction({
                    'from': self.wallet_address, 'gas': gas_limit,
                    'gasPrice': gas_price, 'nonce': nonce, 'chainId': 56
                })

                signed = self.account.sign_transaction(tx)
                tx_hash = await self.w3.eth.send_raw_transaction(self._get_raw_tx(signed))
                logger.info(f"📉 Sell sent: {tx_hash.hex()}")

                success = await self._wait_for_tx(tx_hash.hex())
                if not success:
                    async with self.nonce_lock: self.local_nonce = None
                    raise Exception("Sell tx failed or timed out")
                return tx_hash.hex()

            except Exception as e:
                logger.error(f"❌ Sell failed (attempt {attempt+1}/3): {e}")
                async with self.nonce_lock: self.local_nonce = None
                if attempt < 2:
                    await asyncio.sleep(1)
                else:
                    return None

    async def _ensure_approve(self, token_address: str, amount: int):
        """确保授权（带重试）"""
        if self._cached_approval_amount(token_address) >= int(amount):
            return

        for attempt in range(3):
            try:
                token = self.w3.eth.contract(address=token_address, abi=[
                    {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
                    {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}
                ])

                allowance = await token.functions.allowance(self.wallet_address, self.contract_address).call()
                if allowance < amount:
                    logger.info(f"Approving {token_address}...")
                    nonce = await self._get_next_nonce()

                    BASE_GAS_PRICE_GWEI = TradingConfig.BASE_GAS_PRICE_GWEI
                    MAX_GAS_PRICE_GWEI = TradingConfig.MAX_GAS_PRICE_GWEI
                    base_gas = self.w3.to_wei(BASE_GAS_PRICE_GWEI, 'gwei')
                    # 每次重试 gas 翻倍: 0.08 -> 0.16 -> 0.32
                    retry_multiplier = self.gas_multiplier * (2 ** attempt)
                    gas_price = min(int(base_gas * retry_multiplier), self.w3.to_wei(MAX_GAS_PRICE_GWEI * 3, 'gwei'))
                    if attempt > 0:
                        logger.info(f"⛽ Approve retry #{attempt+1} gas: {self.w3.from_wei(gas_price, 'gwei'):.4f} Gwei")

                    tx = await token.functions.approve(self.contract_address, 2**256 - 1).build_transaction({
                        'from': self.wallet_address, 'gas': 100000,
                        'gasPrice': gas_price, 'nonce': nonce, 'chainId': 56
                    })
                    tx_hash_bytes = await self.w3.eth.send_raw_transaction(self._get_raw_tx(self.account.sign_transaction(tx)))
                    tx_hash = tx_hash_bytes.hex()
                    logger.info(f"🔓 Approve sent: {tx_hash}")

                    success = await self._wait_for_tx(tx_hash)
                    if not success:
                        async with self.nonce_lock: self.local_nonce = None
                        raise Exception("Approve transaction failed or timed out")
                    self._remember_approval_amount(token_address, 2**256 - 1)
                else:
                    self._remember_approval_amount(token_address, allowance)
                return  # 授权已足够或成功
            except Exception as e:
                logger.error(f"Approve failed (attempt {attempt+1}/3): {e}")
                # 重置 nonce 防止错位
                async with self.nonce_lock: self.local_nonce = None
                if attempt < 2:
                    await asyncio.sleep(1)
                else:
                    raise

    async def _sell_on_pancakeswap(self, token_address: str, amount: int, gas_price: int) -> Optional[str]:
        """通过 PancakeSwap V2 卖出毕业代币"""
        try:
            # 先 approve PancakeSwap Router
            await self._ensure_approve_spender(token_address, PANCAKE_ROUTER_V2, amount)

            nonce = await self._get_next_nonce()
            deadline = int(time.time()) + 300

            path = [
                self.w3.to_checksum_address(token_address),
                self.w3.to_checksum_address(WBNB)
            ]

            amount_out_min = None
            expected_out = None
            try:
                amounts = await self.pancake_router.functions.getAmountsOut(int(amount), path).call()
                if amounts:
                    expected_out = int(amounts[-1])
                    amount_out_min = compute_sell_min_out(
                        expected_out, TradingConfig.SELL_SLIPPAGE_PERCENT
                    )
            except Exception as quote_err:
                logger.warning(f"⚠️ PancakeSwap getAmountsOut failed: {quote_err}")

            if amount_out_min is None:
                amount_out_min = 1
                logger.warning(
                    "PancakeSwap sell has no verified quote; using emergency minOut=1 "
                    "(unquoted_exit=True)"
                )
            else:
                logger.info(
                    "PancakeSwap sell minOut=%s from expected_out=%s slippage=%s%%",
                    amount_out_min,
                    expected_out,
                    TradingConfig.SELL_SLIPPAGE_PERCENT,
                )

            func = self.pancake_router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                int(amount), amount_out_min, path, self.wallet_address, deadline
            )

            try:
                gas_limit = int(await func.estimate_gas({'from': self.wallet_address}) * 1.3)
            except Exception as e:
                logger.warning(f"⚠️ PancakeSwap estimate failed: {e}, using 500000")
                gas_limit = 500000

            tx = await func.build_transaction({
                'from': self.wallet_address, 'gas': gas_limit,
                'gasPrice': gas_price, 'nonce': nonce, 'chainId': 56
            })

            signed = self.account.sign_transaction(tx)
            tx_hash = await self.w3.eth.send_raw_transaction(self._get_raw_tx(signed))
            logger.info(f"🥞 PancakeSwap sell sent: {tx_hash.hex()}")

            success = await self._wait_for_tx(tx_hash.hex())
            if not success:
                async with self.nonce_lock: self.local_nonce = None
                logger.error("❌ PancakeSwap sell tx failed")
                return None
            return tx_hash.hex()

        except Exception as e:
            logger.error(f"❌ PancakeSwap sell failed: {e}")
            async with self.nonce_lock: self.local_nonce = None
            return None

    async def _ensure_approve_spender(self, token_address: str, spender: str, amount: int):
        """确保对指定 spender 的授权"""
        token = self.w3.eth.contract(address=token_address, abi=[
            {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
            {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}
        ])
        spender_addr = self.w3.to_checksum_address(spender)
        if await token.functions.allowance(self.wallet_address, spender_addr).call() < amount:
            logger.info(f"Approving {token_address} for PancakeSwap...")
            nonce = await self._get_next_nonce()
            gas_price = self.w3.to_wei(TradingConfig.BASE_GAS_PRICE_GWEI, 'gwei')
            tx = await token.functions.approve(spender_addr, 2**256 - 1).build_transaction({
                'from': self.wallet_address, 'gas': 100000,
                'gasPrice': gas_price, 'nonce': nonce, 'chainId': 56
            })
            tx_hash = await self.w3.eth.send_raw_transaction(self._get_raw_tx(self.account.sign_transaction(tx)))
            logger.info(f"🔓 PancakeSwap approve sent: {tx_hash.hex()}")
            success = await self._wait_for_tx(tx_hash.hex())
            if not success:
                async with self.nonce_lock: self.local_nonce = None
                raise Exception("PancakeSwap approve failed")

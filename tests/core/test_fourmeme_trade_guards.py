import unittest
from pathlib import Path
from unittest.mock import patch

from config.trading_config import TradingConfig
from src.core.trader import (
    KNOWN_QUOTE_ASSETS,
    NATIVE_QUOTE_ADDRESS,
    TradeExecutor,
    compute_buy_min_amount,
    compute_sell_min_out,
)
from src.data.fourmeme_log_decoder import LIQUIDITY_ADDED_TOPIC
from src.trader import bot as bot_module

WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
USDT = "0x55d398326f99059ff775485246999027b3197955"


class BuyMinAmountTests(unittest.TestCase):
    def test_computes_minimum_from_verified_price_and_slippage(self):
        self.assertEqual(850000000, compute_buy_min_amount(1.0, 0.001, 6, 15))

    def test_returns_none_without_a_verified_price(self):
        self.assertIsNone(compute_buy_min_amount(1.0, 0, 6, 15))
        self.assertIsNone(compute_buy_min_amount(0.0, 0.001, 6, 15))
        self.assertIsNone(compute_buy_min_amount(1.0, -1.0, 6, 15))

    def test_never_returns_zero(self):
        self.assertEqual(1, compute_buy_min_amount(1.0, 1.0, 0, 99))


class SellMinOutTests(unittest.TestCase):
    def test_applies_slippage_to_the_quoted_output(self):
        self.assertEqual(850, compute_sell_min_out(1000, 15))

    def test_returns_none_without_a_quote(self):
        self.assertIsNone(compute_sell_min_out(0, 15))
        self.assertIsNone(compute_sell_min_out(-5, 15))

    def test_never_returns_zero(self):
        self.assertEqual(1, compute_sell_min_out(1, 99))


class QuoteClassificationTests(unittest.TestCase):
    def test_native_bnb_is_supported(self):
        self.assertIsNone(TradeExecutor._unsupported_quote_reason(NATIVE_QUOTE_ADDRESS))
        self.assertIsNone(TradeExecutor._unsupported_quote_reason(bytes(20)))

    def test_non_native_quotes_are_named(self):
        self.assertIn("WBNB", TradeExecutor._unsupported_quote_reason(WBNB))
        self.assertIn("USDT", TradeExecutor._unsupported_quote_reason(USDT))
        self.assertIn("Unsupported quote asset", TradeExecutor._unsupported_quote_reason("0x" + "99" * 20))

    def test_registry_contains_known_quotes(self):
        self.assertEqual("BNB", KNOWN_QUOTE_ASSETS[NATIVE_QUOTE_ADDRESS.lower()])
        self.assertEqual("WBNB", KNOWN_QUOTE_ASSETS[WBNB])
        self.assertEqual("USDT", KNOWN_QUOTE_ASSETS[USDT])


class SaleTopicDerivationTests(unittest.TestCase):
    def test_bot_sale_topics_exclude_liquidity_added(self):
        self.assertNotIn(LIQUIDITY_ADDED_TOPIC, bot_module.FOURMEME_SALE_TOPICS)
        self.assertIn(
            "80d4e495cda89b31af98c8e977ff11f417bafcee26902a17a15be51830c47533",
            bot_module.FOURMEME_SALE_TOPICS,
        )
        self.assertIn(
            "0a5575b3648bae2210cee56bf33254cc1ddfbc7bf637c0af2ac18b14fb1bae19",
            bot_module.FOURMEME_SALE_TOPICS,
        )
        self.assertEqual(2, len(bot_module.FOURMEME_SALE_TOPICS))


class SlippageConfigContractTests(unittest.TestCase):
    def test_env_template_documents_new_trade_guards(self):
        template = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
        for key in (
            "BUY_SLIPPAGE_PERCENT=",
            "SELL_SLIPPAGE_PERCENT=",
            "FOURMEME_TOKEN_DECIMALS=",
            "BUY_MIN_AMOUNT_FLOOR=",
        ):
            self.assertIn(key, template)

    def test_trading_config_exposes_validated_defaults(self):
        self.assertIsInstance(TradingConfig.BUY_SLIPPAGE_PERCENT, int)
        self.assertIsInstance(TradingConfig.SELL_SLIPPAGE_PERCENT, int)
        self.assertIsInstance(TradingConfig.FOURMEME_TOKEN_DECIMALS, int)
        self.assertIsInstance(TradingConfig.BUY_MIN_AMOUNT_FLOOR, int)
        self.assertTrue(0 <= TradingConfig.BUY_SLIPPAGE_PERCENT < 100)
        self.assertTrue(0 <= TradingConfig.SELL_SLIPPAGE_PERCENT < 100)
        self.assertTrue(0 <= TradingConfig.FOURMEME_TOKEN_DECIMALS <= 36)
        self.assertGreaterEqual(TradingConfig.BUY_MIN_AMOUNT_FLOOR, 0)


class QuoteAssetModuleTests(unittest.TestCase):
    def test_normalize_and_classify(self):
        from src.data.fourmeme_quote import (
            classify_quote,
            is_known_non_native,
            is_native_bnb,
            normalize_quote,
        )

        self.assertEqual("0x" + "11" * 20, normalize_quote("0x" + "11" * 20))
        self.assertIsNone(normalize_quote("0x1234"))
        self.assertIsNone(normalize_quote(None))
        self.assertEqual("BNB", classify_quote(bytes(20)))
        self.assertEqual("WBNB", classify_quote(WBNB))
        self.assertEqual("USDT", classify_quote(USDT))
        self.assertEqual("unknown", classify_quote("0x" + "77" * 20))
        self.assertTrue(is_native_bnb(bytes(20)))
        self.assertFalse(is_native_bnb(USDT))
        self.assertTrue(is_known_non_native(USDT))
        self.assertFalse(is_known_non_native("0x" + "77" * 20))


if __name__ == "__main__":
    unittest.main()


class SellAmountAlignmentTests(unittest.TestCase):
    """The Four.meme manager only accepts 1e9-wei multiples, which must not zero a position."""

    def test_rounds_down_to_the_contract_step(self):
        from src.core.trader import align_sell_amount

        self.assertEqual(1_000_000_000, align_sell_amount(1_999_999_999))
        self.assertEqual(2_000_000_000, align_sell_amount(2_000_000_000))

    def test_returns_zero_when_the_balance_is_below_one_step(self):
        from src.core.trader import align_sell_amount

        # 500 tokens with 6 decimals is a real position, but it is smaller than one step.
        self.assertEqual(0, align_sell_amount(500 * 10 ** 6))
        self.assertEqual(0, align_sell_amount(0))
        self.assertEqual(0, align_sell_amount(-5))
        self.assertEqual(0, align_sell_amount("not-a-number"))

    def test_eighteen_decimal_balance_only_loses_sub_wei_dust(self):
        from src.core.trader import align_sell_amount

        raw = 1_234_567_890_123_456_789
        aligned = align_sell_amount(raw)
        self.assertLessEqual(raw - aligned, 10 ** 9 - 1)


class ChainIdConfigTests(unittest.TestCase):
    def test_chain_id_defaults_to_bsc(self):
        self.assertEqual(56, TradingConfig.CHAIN_ID)

    def test_chain_id_must_be_positive(self):
        # Patch the class attribute instead of reloading config.trading_config: a reload
        # would rebind the module to a new class object while modules that already imported
        # TradingConfig keep the stale one, which silently breaks unrelated tests.
        with patch.object(TradingConfig, "CHAIN_ID", 0):
            with self.assertRaisesRegex(ValueError, "MEME_CHAIN_ID"):
                TradingConfig.validate()

    def test_executor_does_not_hardcode_chain_id(self):
        source = (Path(__file__).resolve().parents[2] / "src" / "core" / "trader.py").read_text()
        self.assertNotIn("'chainId': 56", source)
        self.assertIn("'chainId': TradingConfig.CHAIN_ID", source)

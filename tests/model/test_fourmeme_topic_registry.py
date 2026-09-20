import unittest

from src.data.fourmeme_log_decoder import (
    EVENT_NAME_BY_TOPIC,
    LIQUIDITY_ADDED_TOPIC,
    TRADE_TOPICS,
    canonical_trade_name,
    is_known_topic,
)


class FourMemeTopicRegistryTests(unittest.TestCase):
    def test_every_registered_topic_is_a_32_byte_hash(self):
        self.assertTrue(EVENT_NAME_BY_TOPIC)
        for topic in EVENT_NAME_BY_TOPIC:
            self.assertEqual(64, len(topic))
            int(topic, 16)

    def test_liquidity_added_topic_is_not_a_trade_topic(self):
        self.assertEqual("LiquidityAdded", EVENT_NAME_BY_TOPIC[LIQUIDITY_ADDED_TOPIC])
        self.assertNotIn(LIQUIDITY_ADDED_TOPIC, TRADE_TOPICS)

    def test_only_real_trade_abis_are_classified_as_trades(self):
        names = {EVENT_NAME_BY_TOPIC[topic] for topic in TRADE_TOPICS}
        self.assertEqual({"TokenPurchase", "TokenSale"}, names)

    def test_auxiliary_origin_events_are_not_canonical_trades(self):
        self.assertEqual("TokenPurchase", canonical_trade_name("TokenPurchase"))
        self.assertEqual("TokenSale", canonical_trade_name("TokenSale"))
        self.assertIsNone(canonical_trade_name("TokenPurchase2"))
        self.assertIsNone(canonical_trade_name("TokenSale2"))
        self.assertIsNone(canonical_trade_name("LiquidityAdded"))

    def test_malformed_legacy_topic_is_rejected(self):
        # 65 hex chars: the old hand-written listener table never matched this.
        self.assertFalse(
            is_known_topic("a78d55aeb92a87db782edde05df51f62cd9c43f9c4ee844147e54d963cd30d37a")
        )
        self.assertFalse(is_known_topic("no-topic"))
        self.assertFalse(is_known_topic(b"\x00" * 31))


if __name__ == "__main__":
    unittest.main()

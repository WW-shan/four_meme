import unittest

from src.radar.adapters.evm_pair import PAIR_CREATED_TOPIC, EvmPairAdapter

WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
TOKEN = "0x" + "11" * 20
POOL = "0x" + "22" * 20
FACTORY = "0x" + "33" * 20


class EvmPairAdapterTests(unittest.TestCase):
    def adapter(self):
        return EvmPairAdapter("bsc", "pancake_v2", FACTORY, (WBNB,))

    def log(self, token0, token1):
        return {
            "topics": ["0x" + PAIR_CREATED_TOPIC, "0x" + "00" * 12 + token0[2:], "0x" + "00" * 12 + token1[2:]],
            "data": "0x" + "00" * 12 + POOL[2:] + "00" * 32,
        }

    def test_selects_non_quote_token(self):
        event = self.adapter().normalize_pair_created(self.log(WBNB, TOKEN), received_at=1.0)
        self.assertEqual(TOKEN, event.token)
        self.assertEqual(WBNB, event.quote_asset)
        self.assertEqual("pancake_v2", event.platform)

    def test_rejects_pair_without_exactly_one_quote(self):
        self.assertIsNone(self.adapter().normalize_pair_created(self.log(TOKEN, "0x" + "44" * 20)))

    def test_rejects_unverified_factory(self):
        with self.assertRaises(ValueError):
            EvmPairAdapter("bsc", "flap", "0x1234", (WBNB,))

    def test_rejects_unknown_topic(self):
        log = self.log(WBNB, TOKEN)
        log["topics"][0] = "0x" + "ab" * 32
        self.assertIsNone(self.adapter().normalize_pair_created(log))


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path
import unittest

from eth_abi import encode
from hexbytes import HexBytes
from web3 import Web3

from src.data.fourmeme_log_decoder import (
    EVENT_ABIS_BY_TOPIC,
    TRADE_TOPICS,
    decode_fourmeme_log,
)


ROOT = Path(__file__).resolve().parents[2]
TOKEN = "0x1b85a7b2ce69aea67f64049dbed6ab6c3e0ad53d"
ACCOUNT = "0x6ff848ddc24e38f96f20c4f8a3cd4fc15a27e5fa"
QUOTE = "0x55d398326f99059ff775485246999027b3197955"
ZERO_ADDRESS = "0x" + "00" * 20


class FourMemeLogDecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.abis = {
            version: json.loads((ROOT / "config" / filename).read_text())
            for version, filename in (
                ("modern", "TokenManager2.lite.abi"),
                ("legacy", "TokenManager.lite.abi"),
            )
        }

    def event_log(self, name, values, *, version="modern"):
        event = next(
            item for item in self.abis[version]
            if item.get("type") == "event" and item["name"] == name
        )
        types = [item["type"] for item in event["inputs"]]
        signature = name + "(" + ",".join(types) + ")"
        return {
            "topics": ["0x" + Web3.keccak(text=signature).hex().removeprefix("0x")],
            "data": "0x" + encode(types, values).hex(),
        }

    def test_registry_matches_both_local_abis_and_real_event_topics(self):
        expected_topics = {}
        for abi in self.abis.values():
            for event in abi:
                if event.get("type") != "event":
                    continue
                self.assertFalse(event.get("anonymous", False))
                self.assertTrue(all(not item["indexed"] for item in event["inputs"]))
                signature = event["name"] + "(" + ",".join(
                    item["type"] for item in event["inputs"]
                ) + ")"
                topic = Web3.keccak(text=signature).hex().removeprefix("0x")
                expected_topics[topic] = event["name"]

        self.assertEqual(set(EVENT_ABIS_BY_TOPIC), set(expected_topics))
        self.assertEqual(len(expected_topics), 9)
        self.assertEqual(
            expected_topics["c18aa71171b358b706fe3dd345299685ba21a5316c66ffa9e319268b033c44b0"],
            "LiquidityAdded",
        )
        self.assertEqual(
            set(TRADE_TOPICS),
            {
                "7db52723a3b2cdd6164364b3b766e65e540d7be48ffa89582956d8eaebe62942",
                "0a5575b3648bae2210cee56bf33254cc1ddfbc7bf637c0af2ac18b14fb1bae19",
                "00fe0e12b43090c1fc19a34aefa5cc138a4eeafc60ab800f855c730b3fb9480e",
                "80d4e495cda89b31af98c8e977ff11f417bafcee26902a17a15be51830c47533",
            },
        )

    def test_modern_trades_preserve_raw_price_fee_and_state_integers(self):
        raw = {
            "price": 10**9 + 13,
            "amount": 10**25 + 17,
            "cost": 3 * 10**18 + 19,
            "fee": 5 * 10**16 + 23,
            "offers": 2 * 10**26 + 29,
            "funds": 57 * 10**18 + 31,
        }
        for name in ("TokenPurchase", "TokenSale"):
            with self.subTest(name=name):
                decoded = decode_fourmeme_log(self.event_log(name, [TOKEN, ACCOUNT, *raw.values()]))
                self.assertIsNotNone(decoded)
                self.assertEqual(decoded[0], name)
                self.assertEqual(decoded[1]["token"], TOKEN)
                self.assertEqual(decoded[1]["account"], Web3.to_checksum_address(ACCOUNT))
                for field, expected in raw.items():
                    self.assertEqual(decoded[1][field], expected)
                    self.assertIsInstance(decoded[1][field], int)
                self.assertNotIn("quote", decoded[1])

    def test_legacy_amount_aliases_do_not_confuse_nonzero_fee_with_proceeds(self):
        amount, proceeds = 100_000 * 10**18, 5 * 10**17
        for name in ("TokenPurchase", "TokenSale"):
            for fee in (0, 5 * 10**15):
                with self.subTest(name=name, fee=fee):
                    decoded = decode_fourmeme_log(
                        self.event_log(name, [TOKEN, ACCOUNT, amount, proceeds, fee], version="legacy")
                    )
                    self.assertIsNotNone(decoded)
                    self.assertEqual(decoded[0], name)
                    args = decoded[1]
                    self.assertEqual(args["tokenAmount"], amount)
                    self.assertEqual(args["etherAmount"], proceeds)
                    self.assertEqual(args["amount"], amount)
                    self.assertEqual(args["cost"], proceeds)
                    self.assertEqual(args["fee"], fee)
                    self.assertEqual(args["cost"] / args["amount"], 0.000005)
                    self.assertNotIn("price", args)

    def test_liquidity_preserves_native_or_erc20_quote_and_never_invents_a_seller(self):
        offers, funds = 200_000_000 * 10**18, 1234 * 10**18
        for quote in (ZERO_ADDRESS, QUOTE):
            with self.subTest(quote=quote):
                decoded = decode_fourmeme_log(
                    self.event_log("LiquidityAdded", [TOKEN, offers, quote, funds])
                )
                self.assertEqual(
                    decoded,
                    ("LiquidityAdded", {"base": TOKEN, "offers": offers, "quote": quote, "funds": funds}),
                )
                self.assertNotIn("account", decoded[1])
                self.assertNotIn("amount", decoded[1])

    def test_auxiliary_origin_events_are_not_trade_events(self):
        for name in ("TokenPurchase2", "TokenSale2"):
            with self.subTest(name=name):
                self.assertEqual(
                    decode_fourmeme_log(self.event_log(name, [987654321])),
                    (name, {"origin": 987654321}),
                )

    def test_create_and_stop_keep_lowercase_token_keys(self):
        create = decode_fourmeme_log(self.event_log(
            "TokenCreate", [ACCOUNT, TOKEN, 7, "测试代币", "SYM", 10**27, 123, 5 * 10**16]
        ))
        self.assertIsNotNone(create)
        self.assertEqual(create[0], "TokenCreate")
        self.assertEqual(create[1]["token"], TOKEN)
        self.assertEqual(create[1]["creator"], Web3.to_checksum_address(ACCOUNT))
        self.assertEqual(create[1]["name"], "测试代币")
        self.assertEqual(create[1]["totalSupply"], 10**27)
        self.assertEqual(
            decode_fourmeme_log(self.event_log("TradeStop", [TOKEN])),
            ("TradeStop", {"token": TOKEN}),
        )

    def test_hex_and_web3_byte_payloads_decode_identically(self):
        log = self.event_log("TokenSale", [TOKEN, ACCOUNT, 1, 2, 3, 4, 5, 6])
        expected = decode_fourmeme_log(log)
        for convert in (HexBytes, bytes.fromhex, bytearray.fromhex):
            with self.subTest(convert=convert):
                self.assertEqual(
                    decode_fourmeme_log({
                        "topics": [convert(log["topics"][0][2:])],
                        "data": convert(log["data"][2:]),
                    }),
                    expected,
                )

    def test_exact_static_payload_lengths_are_required(self):
        cases = (
            ("TokenSale", [TOKEN, ACCOUNT, 1, 2, 3, 4, 5, 6], "modern", (128, 160, 224, 288)),
            ("TokenSale", [TOKEN, ACCOUNT, 2, 3, 4], "legacy", (128, 192)),
            ("LiquidityAdded", [TOKEN, 2, QUOTE, 3], "modern", (96, 160)),
            ("TokenPurchase2", [7], "modern", (0, 64)),
            ("TradeStop", [TOKEN], "modern", (0, 64)),
        )
        for name, values, version, lengths in cases:
            log = self.event_log(name, values, version=version)
            data = bytes.fromhex(log["data"][2:])
            for length in lengths:
                with self.subTest(name=name, version=version, length=length):
                    malformed = dict(log, data=data[:length].ljust(length, b"\x00"))
                    self.assertIsNone(decode_fourmeme_log(malformed))

    def test_indexed_layout_and_topic_width_cannot_be_guessed(self):
        log = self.event_log("TokenSale", [TOKEN, ACCOUNT, 1, 2, 3, 4, 5, 6])
        topic = log["topics"][0]
        for topics in ([], [topic, "0x" + "00" * 32], [topic] * 3, [topic[:-2]], [topic + "00"], topic):
            with self.subTest(topics=topics):
                self.assertIsNone(decode_fourmeme_log(dict(log, topics=topics)))

    def test_invalid_hex_and_address_padding_are_rejected(self):
        log = self.event_log("TokenSale", [TOKEN, ACCOUNT, 1, 2, 3, 4, 5, 6])
        for data in ("0xzz", "0x0", None, 256):
            with self.subTest(data=data):
                self.assertIsNone(decode_fourmeme_log(dict(log, data=data)))
        payload = bytearray.fromhex(log["data"][2:])
        payload[0] = 1
        self.assertIsNone(decode_fourmeme_log(dict(log, data=payload)))

    def test_create_rejects_truncated_and_trailing_data(self):
        log = self.event_log("TokenCreate", [ACCOUNT, TOKEN, 1, "Name", "SYM", 10**27, 0, 0])
        data = bytes.fromhex(log["data"][2:])
        for malformed in (data[:-32], data + b"\x00" * 32):
            with self.subTest(length=len(malformed)):
                self.assertIsNone(decode_fourmeme_log(dict(log, data=malformed)))

    def test_unknown_topic_is_not_inferred_from_a_trade_shaped_payload(self):
        log = self.event_log("TokenSale", [TOKEN, ACCOUNT, 1, 2, 3, 4, 5, 6])
        for topic in (
            "0x" + "00" * 32,
            "0xa78d55aeb92a87db782edde05df51f62cd9c43f9c4ee844147e54d963cd30d37a",
        ):
            with self.subTest(topic=topic):
                self.assertIsNone(decode_fourmeme_log(dict(log, topics=[topic])))

    def test_legitimate_tiny_price_is_not_filtered(self):
        decoded = decode_fourmeme_log(
            self.event_log("TokenSale", [TOKEN, ACCOUNT, 1, 10**27, 1, 0, 17, 19])
        )
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded[0], "TokenSale")
        self.assertEqual(decoded[1]["amount"], 10**27)
        self.assertEqual(decoded[1]["cost"], 1)
        self.assertEqual(decoded[1]["price"], 1)


if __name__ == "__main__":
    unittest.main()

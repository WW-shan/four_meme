import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eth_abi import encode
from web3 import Web3

from scripts import backfill_fourmeme_month_raw as backfill
from scripts.backfill_fourmeme_month_raw import TOPIC_CREATE, _decode_log, _decode_trade


class BackfillFourMemeRawTests(unittest.TestCase):
    def test_trade_token_key_matches_lowercase_create_key(self):
        token = "0x1b85a7b2ce69aea67f64049dbed6ab6c3e0ad53d"
        creator = "0x23c98294f6cb63a0a1d1b10096bfa7823ddae31b"
        account = "0x6ff848ddc24e38f96f20c4f8a3cd4fc15a27e5fa"
        create_data = encode(
            ["address", "address", "uint256", "string", "string", "uint256", "uint256", "uint256"],
            [creator, token, 1, "name", "SYM", 10**27, 0, 0],
        )
        create = _decode_log({"topics": ["0x" + TOPIC_CREATE], "data": "0x" + create_data.hex()})
        self.assertIsNotNone(create)
        self.assertEqual(create[1]["token"], token)

        trade_data = encode(
            ["address", "address", "uint256", "uint256", "uint256", "uint256", "uint256", "uint256"],
            [token, account, 1, 123, 456, 0, 0, 0],
        )
        trade = _decode_log(
            {
                "topics": ["0x7db52723a3b2cdd6164364b3b766e65e540d7be48ffa89582956d8eaebe62942"],
                "data": "0x" + trade_data.hex(),
            }
        )
        self.assertIsNotNone(trade)
        self.assertEqual(trade[1]["token"], token)
        self.assertEqual(trade[1]["amount"], 123)
        self.assertEqual(trade[1]["cost"], 456)

    def test_only_actual_trade_events_are_exposed_by_decode_trade(self):
        token = "0x" + "11" * 20
        for quote in ("0x" + "00" * 20, "0x55d398326f99059ff775485246999027b3197955"):
            with self.subTest(quote=quote):
                log = {
                    "topics": ["0xc18aa71171b358b706fe3dd345299685ba21a5316c66ffa9e319268b033c44b0"],
                    "data": "0x" + encode(
                        ["address", "uint256", "address", "uint256"],
                        [token, 200_000_000 * 10**18, quote, 1234 * 10**18],
                    ).hex(),
                }
                self.assertEqual(_decode_log(log)[0], "LiquidityAdded")
                self.assertIsNone(_decode_trade(log))
        for signature in ("TokenPurchase2(uint256)", "TokenSale2(uint256)"):
            log = {
                "topics": [Web3.keccak(text=signature)],
                "data": encode(["uint256"], [7]),
            }
            self.assertIsNone(_decode_trade(log))

    def test_backfill_counts_legacy_sale_but_not_graduation_liquidity(self):
        token, account = "0x" + "11" * 20, "0x" + "22" * 20
        events = (
            (
                "TokenCreate",
                ["address", "address", "uint256", "string", "string", "uint256", "uint256", "uint256"],
                [account, token, 1, "Name", "SYM", 10**27, 0, 0],
            ),
            (
                "TokenPurchase",
                ["address", "address", "uint256", "uint256", "uint256", "uint256", "uint256", "uint256"],
                [token, account, 99, 100_000 * 10**18, 10**18, 10**16, 1, 2],
            ),
            (
                "TokenSale",
                ["address", "address", "uint256", "uint256", "uint256"],
                [token, account, 100_000 * 10**18, 5 * 10**17, 5 * 10**15],
            ),
            ("TokenSale2", ["uint256"], [7]),
            ("TradeStop", ["address"], [token]),
            (
                "LiquidityAdded",
                ["address", "uint256", "address", "uint256"],
                [token, 200_000_000 * 10**18, "0x55d398326f99059ff775485246999027b3197955", 1234 * 10**18],
            ),
        )
        logs = []
        for index, (name, types, values) in enumerate(events):
            signature = name + "(" + ",".join(types) + ")"
            logs.append({
                "topics": ["0x" + Web3.keccak(text=signature).hex().removeprefix("0x")],
                "data": "0x" + encode(types, values).hex(),
                "blockNumber": "0x64",
                "blockTimestamp": "0x3e8",
                "logIndex": hex(index),
                "transactionHash": "0x" + "ab" * 32,
            })

        with tempfile.TemporaryDirectory() as directory:
            args = backfill.parse_args([
                "--output-dir", directory, "--from-block", "100", "--to-block", "100",
                "--fetch-workers", "1", "--max-workers", "1",
            ])
            with (
                patch.object(backfill, "load_dotenv"),
                patch.object(backfill.Config, "validate_rpc_config"),
                patch.object(backfill.Config, "get_local_proxy_url", return_value=None),
                patch.object(backfill, "_block_number", return_value=100),
                patch.object(backfill, "_block_timestamp", return_value=1000),
                patch.object(backfill, "_fetch_logs_range", return_value=logs),
            ):
                self.assertEqual(backfill.run(args), 0)
            lifecycle_file = next(Path(directory).glob("lifecycle_incremental_*.jsonl"))
            lifecycle = json.loads(lifecycle_file.read_text())

        self.assertEqual(lifecycle["total_buy_count"], 1)
        self.assertEqual(lifecycle["total_sell_count"], 1)
        self.assertEqual(lifecycle["sells"][0]["account"].lower(), account)
        self.assertAlmostEqual(lifecycle["sells"][0]["token_amount"], 100_000)
        self.assertEqual(lifecycle["sells"][0]["bnb_amount"], 0.5)
        self.assertAlmostEqual(lifecycle["price_min"], 0.000005, places=18)
        self.assertEqual(len(lifecycle["price_history"]), 2)
        self.assertTrue(lifecycle["graduated"])


if __name__ == "__main__":
    unittest.main()

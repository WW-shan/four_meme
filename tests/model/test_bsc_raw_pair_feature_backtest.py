import unittest

from scripts import run_bsc_raw_pair_feature_backtest as raw


def _word(value):
    return f"{int(value):064x}"


def _swap(timestamp, block, log_index, *, buy=True):
    if buy:
        amounts = (0, 10**18, 100 * 10**18, 0)
    else:
        amounts = (100 * 10**18, 0, 0, 10**18)
    return {
        "address": "0xpair",
        "topics": [
            raw.SWAP_TOPIC,
            "0x" + "11" * 32,
            "0x" + ("22" if buy else "33") * 32,
        ],
        "data": "0x" + "".join(_word(value) for value in amounts),
        "blockNumber": hex(block),
        "logIndex": hex(log_index),
        "blockTimestamp": hex(timestamp),
        "transactionHash": "0x" + f"{block:064x}",
    }


def _sync(timestamp, block, log_index, reserve0, reserve1):
    return {
        "address": "0xpair",
        "topics": [raw.SYNC_TOPIC],
        "data": "0x" + _word(reserve0) + _word(reserve1),
        "blockNumber": hex(block),
        "logIndex": hex(log_index),
        "blockTimestamp": hex(timestamp),
        "transactionHash": "0x" + f"{block:064x}",
    }


class BscRawPairFeatureBacktestTests(unittest.TestCase):
    def test_pair_bars_decode_swap_direction_and_liquidity(self):
        token = "0xtoken"
        token_meta = {
            "base_token": token,
            "token0": token,
            "token1": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
            "pair_address": "0xpair",
        }
        events = [
            _sync(3600, 1, 0, 1000 * 10**18, 10 * 10**18),
            _swap(3610, 1, 1, buy=True),
            _sync(7200, 2, 0, 1100 * 10**18, 9 * 10**18),
            _swap(7210, 2, 1, buy=False),
            _sync(10800, 3, 0, 1000 * 10**18, 10 * 10**18),
            _swap(10810, 3, 1, buy=True),
        ]
        bars, rich = raw._pair_bars(events, pair_meta={}, token_meta=token_meta)
        self.assertEqual(len(bars), 3)
        self.assertEqual(rich["quote_symbol"], "WBNB")
        first_bucket = rich["buckets"][3600]
        self.assertGreater(first_bucket["buy_volume"], 0.0)
        self.assertEqual(first_bucket["sell_volume"], 0.0)
        self.assertAlmostEqual(first_bucket["liquidity_quote"], 20.0)
        second_bucket = rich["buckets"][7200]
        self.assertGreater(second_bucket["sell_volume"], 0.0)
        self.assertEqual(second_bucket["new_traders"], 1)

    def test_build_raw_episodes_respects_graduation_and_raw_window(self):
        token = "0xtoken"
        pair_meta = {
            token: {
                "base_token": token,
                "token0": token,
                "token1": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
                "pair_address": "0xpair",
            }
        }
        lifecycle = {
            token: {
                "token_address": token,
                "graduated": True,
                "graduate_time": 3600,
                "symbol": "T",
            }
        }
        events = []
        for index, timestamp in enumerate((3600, 7200, 10800, 14400), start=1):
            events.extend([
                _sync(timestamp, index, 0, 1000 * 10**18, 10 * 10**18),
                _swap(timestamp + 10, index, 1, buy=True),
            ])
        episodes, coverage = raw.build_raw_episodes(
            lifecycle,
            pair_meta,
            {
                "events": events,
                "payload": {"pairs": {"0xpair": {"pair_address": "0xpair"}}},
            },
        )
        self.assertEqual(len(episodes), 1)
        self.assertEqual(coverage["episode_count"], 1)
        self.assertEqual(episodes[0]["quote_symbol"], "WBNB")
        self.assertEqual(episodes[0]["entry_index"], 1)


if __name__ == "__main__":
    unittest.main()

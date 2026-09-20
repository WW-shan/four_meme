import json
import tempfile
import unittest
from pathlib import Path

from scripts import run_bsc_dex_feature_horizon_backtest as backtest


class BscDexFeatureHorizonBacktestTests(unittest.TestCase):
    def test_simulation_uses_next_bar_for_stop_and_applies_costs(self):
        episode = {
            "entry_index": 1,
            "bars": [
                (0, 1.0, 1.0, 1.0, 1.0, 10.0),
                (3600, 1.0, 1.1, 0.6, 0.65, 10.0),
                (7200, 0.8, 0.9, 0.7, 0.8, 10.0),
                (10800, 0.9, 1.0, 0.9, 1.0, 10.0),
            ],
        }
        result = backtest.simulate_episode(episode, horizon_seconds=7200, exit_mode="hard_stop_30")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["exit_reason"], "hard_stop")
        self.assertEqual(result["exit_time"], 7200.0)
        self.assertLess(result["net_return_pct"], result["gross_return_pct"])

    def test_walk_forward_feature_thresholds_are_fit_on_train_slice(self):
        def episode(token, graduation, momentum, volume):
            return {
                "token": token,
                "graduation_time": graduation,
                "entry_index": 1,
                "momentum_1h": momentum,
                "range_1h": 0.1,
                "volume_1h": volume,
                "bars": [
                    (0, 1.0, 1.0, 1.0, 1.0, 1.0),
                    (3600, 1.0, 1.1, 0.9, 1.0, 1.0),
                    (7200, 2.0, 2.0, 2.0, 2.0, 1.0),
                ],
            }

        episodes = [
            episode("a", 1, 0.1, 10),
            episode("b", 2, -0.1, 1),
            episode("c", 3, 0.1, 20),
            episode("d", 4, -0.1, 1),
        ]
        report = backtest.run_grid(episodes, horizons_hours=[1], train_fraction=0.5, min_train_samples=1)
        selected = report["horizons"]["1"]["fixed"]["selected_by_train"]
        self.assertEqual(report["split"]["train_count"], 2)
        self.assertEqual(report["split"]["test_count"], 2)
        self.assertEqual(selected["thresholds"]["volume_median"], 1.0)
        self.assertIn(selected["feature_mode"], backtest.FEATURE_MODES)

    def test_cli_build_report_loads_lifecycle_and_ohlcv_inputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lifecycle_dir = root / "lifecycle"
            lifecycle_dir.mkdir()
            (lifecycle_dir / "lifecycle_0001.jsonl").write_text(
                json.dumps({
                    "token_address": "0xtoken",
                    "symbol": "T",
                    "graduated": True,
                    "graduate_time": 1000,
                    "price_history": [],
                }) + "\n",
                encoding="utf-8",
            )
            ohlcv = root / "ohlcv.json"
            ohlcv.write_text(json.dumps({
                "rows": [{
                    "token": "0xtoken",
                    "symbol": "T",
                    "ohlcv": [
                        [10800, 2, 2, 2, 2, 10],
                        [7200, 1, 1, 1, 1, 10],
                        [3600, 1, 1, 1, 1, 10],
                    ],
                }],
            }), encoding="utf-8")
            args = backtest.parse_args([
                "--lifecycle-dir", str(lifecycle_dir),
                "--ohlcv", str(ohlcv),
                "--horizons-hours", "1",
            ])
            report = backtest.build_report(args)

        self.assertEqual(report["inputs"]["episode_count"], 1)
        self.assertEqual(report["grid"]["split"]["train_count"], 1)
        self.assertEqual(report["decision"], "research_only_do_not_promote_without_raw_chain_and_future_validation")


if __name__ == "__main__":
    unittest.main()

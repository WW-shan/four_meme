import json
import tempfile
import unittest
from pathlib import Path

from scripts import run_post_graduation_retention_replay as cli


class PostGraduationRetentionReplayCliTests(unittest.TestCase):
    def test_parse_args_defaults_to_research_profile(self):
        args = cli.parse_args([])
        self.assertEqual(args.lifecycle_dir, cli.DEFAULT_LIFECYCLE_DIR)
        self.assertEqual(args.output, cli.DEFAULT_OUTPUT)
        self.assertEqual(args.max_data_age_seconds, 120.0)

    def test_replay_reports_missing_feed_and_healthy_snapshot_separately(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "lifecycle_incremental_0001.jsonl"
            rows = [
                {
                    "token_address": "0xhealthy",
                    "symbol": "GOOD",
                    "create_timestamp": 900,
                    "graduated": True,
                    "price_max": 2.0,
                    "price_history": [{"timestamp": 900, "price": 1.0}],
                    "post_graduation_snapshots": [{
                        "observed_at": 1000,
                        "current_price": 1.9,
                        "dex_liquidity_usd": 20_000,
                        "volume_liquidity_ratio_5m": 0.4,
                        "new_traders_1h": 3,
                        "sell_pressure_5m": 0.2,
                    }],
                },
                {
                    "token_address": "0xhealthy",
                    "symbol": "GOOD",
                    "create_timestamp": 900,
                    "graduated": True,
                    "price_max": 2.0,
                    "price_history": [
                        {"timestamp": 900, "price": 1.0},
                        {"timestamp": 950, "price": 2.0},
                    ],
                },
                {
                    "token_address": "0xmissing",
                    "symbol": "MISSING",
                    "create_timestamp": 900,
                    "graduate_time": 1000,
                    "graduated": True,
                    "price_max": 2.0,
                },
                {
                    "token_address": "0xcurve",
                    "symbol": "CURVE",
                    "create_timestamp": 900,
                    "graduated": False,
                },
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            output = root / "report.json"
            args = cli.parse_args([
                "--lifecycle-file", str(path),
                "--output", str(output),
                "--max-examples", "10",
            ])
            report = cli.build_report(args)

        self.assertEqual(report["summary"]["token_count"], 3)
        self.assertEqual(report["summary"]["graduated_count"], 2)
        self.assertEqual(report["summary"]["post_graduation_snapshot_token_count"], 1)
        self.assertEqual(report["summary"]["graduated_without_dex_snapshot_count"], 1)
        self.assertEqual(report["summary"]["decision_counts"], {"close": 1, "hold": 1})
        self.assertEqual(report["summary"]["reason_counts"]["retention_missing_health_data"], 1)
        self.assertEqual(report["decision"], "research_only_missing_post_graduation_dex_coverage")


if __name__ == "__main__":
    unittest.main()

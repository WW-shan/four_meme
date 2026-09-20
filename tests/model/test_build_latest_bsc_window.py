import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_latest_bsc_window import build_latest_window


class TestBuildLatestBscWindow(unittest.TestCase):
    def test_filters_by_creation_time_and_keeps_most_active_duplicate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "training"
            output = Path(tmpdir) / "latest"
            root.mkdir()
            rows = [
                {
                    "token_address": "0xA",
                    "create_timestamp": 99950,
                    "buys": [{"timestamp": 99951}],
                    "sells": [],
                    "last_update": 99951,
                },
                {
                    "token_address": "0xA",
                    "create_timestamp": 99950,
                    "buys": [{"timestamp": 99951}, {"timestamp": 99952}],
                    "sells": [],
                    "last_update": 99952,
                },
                {
                    "token_address": "0xOLD",
                    "create_timestamp": 100,
                    "buys": [{"timestamp": 101}],
                    "sells": [],
                },
                {"token_address": "0xMISSING", "buys": []},
            ]
            lifecycle_path = root / "lifecycle_incremental_20260910_000001.jsonl"
            lifecycle_path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\nnot-json\n",
                encoding="utf-8",
            )

            summary = build_latest_window(
                root,
                output,
                days=1,
                as_of=100000,
            )

            self.assertEqual(summary["output_token_count"], 1)
            self.assertEqual(summary["duplicate_token_count"], 1)
            self.assertEqual(summary["malformed_row_count"], 1)
            self.assertTrue((output / "token_metadata.json").exists())
            lifecycle_files = list(output.glob("lifecycle_*.jsonl"))
            self.assertEqual(len(lifecycle_files), 1)
            saved = [json.loads(line) for line in lifecycle_files[0].read_text().splitlines()]
            self.assertEqual(saved[0]["token_address"], "0xa")
            self.assertEqual(len(saved[0]["buys"]), 2)

    def test_refuses_to_replace_existing_output_without_force(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "training"
            output = Path(tmpdir) / "latest"
            root.mkdir()
            output.mkdir()
            (root / "lifecycle_20260910_000001.jsonl").write_text("", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                build_latest_window(root, output, as_of=1000)

    def test_can_write_disjoint_chronological_files_for_three_way_training(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "training"
            output = Path(tmpdir) / "latest"
            root.mkdir()
            rows = []
            for index in range(6):
                rows.append(
                    {
                        "token_address": f"0x{index}",
                        "create_timestamp": 100000 + index,
                        "buys": [{"timestamp": 100001 + index}],
                        "sells": [],
                    }
                )
            (root / "lifecycle_20260910_000001.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows),
                encoding="utf-8",
            )

            summary = build_latest_window(root, output, days=1, as_of=100000 + 6, split_files=3)

            self.assertEqual(summary["split_files"], 3)
            files = sorted(output.glob("lifecycle_*.jsonl"))
            self.assertEqual(len(files), 3)
            tokens = []
            for path in files:
                tokens.extend(json.loads(line)["token_address"] for line in path.read_text().splitlines())
            self.assertEqual(len(tokens), len(set(tokens)))
            self.assertEqual(len(tokens), 6)

    def test_normalizes_event_order_and_stale_last_update(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "training"
            output = Path(tmpdir) / "latest"
            root.mkdir()
            row = {
                "token_address": "0xORDER",
                "create_timestamp": 100,
                "buys": [{"timestamp": 102}, {"timestamp": 101}],
                "sells": [{"timestamp": 104}, {"timestamp": 103}],
                "price_history": [
                    {"timestamp": 104, "price": 4},
                    {"timestamp": 101, "price": 1},
                ],
                "last_update": 102,
            }
            (root / "lifecycle_20260910_000001.jsonl").write_text(
                json.dumps(row) + "\n", encoding="utf-8"
            )

            build_latest_window(root, output, days=1, as_of=200)
            saved = json.loads(next(output.glob("lifecycle_*.jsonl")).read_text().strip())
            self.assertEqual([event["timestamp"] for event in saved["buys"]], [101, 102])
            self.assertEqual([event["timestamp"] for event in saved["sells"]], [103, 104])
            self.assertEqual([event["timestamp"] for event in saved["price_history"]], [101, 104])
            self.assertEqual(saved["last_update"], 104)

    def test_merges_reactivation_fragments_instead_of_picking_one_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "training"
            output = Path(tmpdir) / "latest"
            root.mkdir()
            rows = [
                {
                    "token_address": "0xFRAGMENT",
                    "create_timestamp": 100,
                    "buys": [{"timestamp": 101, "account": "0xone", "bnb_amount": 1.0}],
                    "sells": [],
                    "price_history": [{"timestamp": 101, "price": 1.0, "type": "buy"}],
                    "last_update": 101,
                },
                {
                    "token_address": "0xFRAGMENT",
                    "create_timestamp": 100,
                    "buys": [{"timestamp": 201, "account": "0xtwo", "bnb_amount": 2.0}],
                    "sells": [],
                    "price_history": [{"timestamp": 201, "price": 2.0, "type": "buy"}],
                    "last_update": 201,
                },
            ]
            (root / "lifecycle_incremental_20260910_000001.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )

            summary = build_latest_window(root, output, days=1, as_of=300)
            saved = json.loads(next(output.glob("lifecycle_*.jsonl")).read_text().strip())

            self.assertEqual(summary["fragment_token_count"], 1)
            self.assertEqual(summary["merged_event_count"], 1)
            self.assertEqual([event["timestamp"] for event in saved["buys"]], [101, 201])
            self.assertEqual(saved["total_buy_count"], 2)
            self.assertEqual(saved["total_buy_volume_bnb"], 3.0)
            self.assertEqual(saved["last_update"], 201)


if __name__ == "__main__":
    unittest.main()

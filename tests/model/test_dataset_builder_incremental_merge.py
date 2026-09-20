import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import importlib.util


def _load_worktree_dataset_builder():
    builder_path = Path(__file__).resolve().parents[2] / "src" / "data" / "dataset_builder.py"
    spec = importlib.util.spec_from_file_location("worktree_dataset_builder", builder_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.DatasetBuilder


DatasetBuilder = _load_worktree_dataset_builder()


def _write_new_format_lifecycle(path: Path, token_address: str, purchases_count: int = 0):
    purchases = []
    for i in range(purchases_count):
        purchases.append(
            {
                "timestamp": 100 + i,
                "account": f"0x{i:040x}",
                "token_amount": 100 + i,
                "ether_amount": 1 + i,
            }
        )

    lifecycle = {
        "token_address": token_address,
        "name": token_address,
        "symbol": token_address,
        "created_at": 1,
        "purchases": purchases,
        "sales": [],
        "total_supply": 1_000_000_000,
        "launch_fee": 0.01,
    }

    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(lifecycle, ensure_ascii=False) + "\n")


class TestDatasetBuilderIncrementalMerge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lifecycle_dir = Path(self.tmp.name)
        self.builder = DatasetBuilder(lifecycle_dir=str(self.lifecycle_dir))

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_load_uses_incremental_files_only_when_present(self):
        _write_new_format_lifecycle(
            self.lifecycle_dir / "lifecycle_incremental_20260215_100000.jsonl",
            token_address="INC",
            purchases_count=1,
        )
        _write_new_format_lifecycle(
            self.lifecycle_dir / "lifecycle_20260215_120000.jsonl",
            token_address="SNAP",
            purchases_count=1,
        )

        seen_tokens = []

        def _capture_and_skip(lifecycle):
            seen_tokens.append(lifecycle["token_address"])
            return []

        with patch.object(self.builder, "_generate_samples_from_lifecycle", side_effect=_capture_and_skip):
            loaded = self.builder.load_lifecycle_files()

        self.assertEqual(loaded, 1)
        self.assertEqual(set(seen_tokens), {"inc"})

    def test_default_load_ignores_snapshot_when_incremental_for_same_token_exists(self):
        _write_new_format_lifecycle(
            self.lifecycle_dir / "lifecycle_incremental_20260215_100000.jsonl",
            token_address="SAME",
            purchases_count=1,
        )
        _write_new_format_lifecycle(
            self.lifecycle_dir / "lifecycle_20260215_120000.jsonl",
            token_address="SAME",
            purchases_count=3,
        )

        captured_buys_lengths = []

        def _capture_and_skip(lifecycle):
            captured_buys_lengths.append(len(lifecycle.get("buys", [])))
            return []

        with patch.object(self.builder, "_generate_samples_from_lifecycle", side_effect=_capture_and_skip):
            loaded = self.builder.load_lifecycle_files()

        self.assertEqual(loaded, 1)
        self.assertEqual(captured_buys_lengths, [1])
    def test_default_load_uses_timestamp_style_snapshot_when_incrementals_absent(self):
        older = self.lifecycle_dir / "lifecycle_20260215_100000.jsonl"
        newer = self.lifecycle_dir / "lifecycle_20260215_120000.jsonl"
        _write_new_format_lifecycle(older, token_address="OLD", purchases_count=1)
        _write_new_format_lifecycle(newer, token_address="NEW", purchases_count=1)

        seen_tokens = []

        def _capture_and_skip(lifecycle):
            seen_tokens.append(lifecycle["token_address"])
            return []

        with patch.object(self.builder, "_generate_samples_from_lifecycle", side_effect=_capture_and_skip):
            loaded = self.builder.load_lifecycle_files()

        self.assertEqual(loaded, 1)
        self.assertEqual(seen_tokens, ["new"])
    def test_default_load_ignores_non_snapshot_lifecycle_glob_matches(self):
        valid_snapshot = self.lifecycle_dir / "lifecycle_20260215_120000.jsonl"
        invalid_snapshot = self.lifecycle_dir / "lifecycle_backup.jsonl"
        _write_new_format_lifecycle(valid_snapshot, token_address="VALID", purchases_count=1)
        _write_new_format_lifecycle(invalid_snapshot, token_address="BACKUP", purchases_count=1)

        seen_tokens = []

        def _capture_and_skip(lifecycle):
            seen_tokens.append(lifecycle["token_address"])
            return []

        with patch.object(self.builder, "_generate_samples_from_lifecycle", side_effect=_capture_and_skip):
            loaded = self.builder.load_lifecycle_files()

        self.assertEqual(loaded, 1)
        self.assertEqual(seen_tokens, ["valid"])

    def test_incremental_fragments_are_merged_before_sample_generation(self):
        rows = [
            {
                "token_address": "FRAGMENT",
                "create_timestamp": 1,
                "buys": [{"timestamp": 10, "account": "0x1", "bnb_amount": 0.1, "price": 1.0}],
                "sells": [],
                "price_history": [{"timestamp": 10, "price": 1.0, "type": "buy"}],
                "last_update": 10,
            },
            {
                "token_address": "FRAGMENT",
                "create_timestamp": 1,
                "buys": [{"timestamp": 20, "account": "0x2", "bnb_amount": 0.2, "price": 2.0}],
                "sells": [],
                "price_history": [{"timestamp": 20, "price": 2.0, "type": "buy"}],
                "last_update": 20,
            },
        ]
        for index, row in enumerate(rows, start=1):
            (self.lifecycle_dir / f"lifecycle_incremental_20260215_10{index:04d}.jsonl").write_text(
                json.dumps(row) + "\n", encoding="utf-8"
            )

        captured = []

        def _capture_and_skip(lifecycle):
            captured.append(lifecycle)
            return []

        with patch.object(self.builder, "_generate_samples_from_lifecycle", side_effect=_capture_and_skip):
            loaded = self.builder.load_lifecycle_files()

        self.assertEqual(loaded, 1)
        self.assertEqual(len(captured), 1)
        self.assertEqual([row["timestamp"] for row in captured[0]["buys"]], [10, 20])
        self.assertEqual(captured[0]["total_buy_count"], 2)
        self.assertEqual(captured[0]["last_update"], 20)


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_rolling_bsc_window import build_rolling_window


class RollingBscWindowTests(unittest.TestCase):
    def test_latest_overlap_is_not_written_twice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            historical = root / "historical"
            latest = root / "latest"
            output = root / "rolling"
            historical.mkdir()
            latest.mkdir()

            historical_rows = [
                {"token_address": "0xold", "create_timestamp": 100, "buys": [{"timestamp": 101}], "sells": []},
                {"token_address": "0xshared", "create_timestamp": 110, "buys": [{"timestamp": 111}], "sells": []},
            ]
            latest_rows = historical_rows + [
                {"token_address": "0xnew1", "create_timestamp": 120, "buys": [{"timestamp": 121}], "sells": []},
                {"token_address": "0xnew2", "create_timestamp": 130, "buys": [{"timestamp": 131}], "sells": []},
            ]
            (historical / "lifecycle_incremental_00000001.jsonl").write_text(
                "\n".join(json.dumps(row) for row in historical_rows) + "\n", encoding="utf-8"
            )
            (latest / "lifecycle_incremental_00000001.jsonl").write_text(
                "\n".join(json.dumps(row) for row in latest_rows) + "\n", encoding="utf-8"
            )

            summary = build_rolling_window(historical, latest, output, latest_split_files=2)
            rows = []
            for path in sorted(output.glob("lifecycle_*.jsonl")):
                rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)

        self.assertEqual(summary["overlap_token_count"], 2)
        self.assertEqual(summary["latest_only_token_count"], 2)
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({row["token_address"] for row in rows}), 4)
        self.assertEqual([row["token_address"] for row in rows], ["0xold", "0xshared", "0xnew1", "0xnew2"])


if __name__ == "__main__":
    unittest.main()


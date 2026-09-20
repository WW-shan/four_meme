import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_heat_event_replay import main
from src.pipeline.heat_event_schema import HeatEvent, HeatEventWriter, lifecycle_to_heat_events


class HeatEventSchemaTests(unittest.TestCase):
    def test_live_event_derives_latency_and_normalizes_identity(self):
        event = HeatEvent(
            source="x",
            event_type="post_create",
            token="0xABC",
            wallet="0xDEF",
            source_event_time="1970-01-01T00:01:40Z",
            observed_time=106.5,
            payload={"post_id": "p1"},
        )
        self.assertEqual(event.token, "0xabc")
        self.assertEqual(event.wallet, "0xdef")
        self.assertEqual(event.latency_seconds, 6.5)
        row = event.to_dict()
        self.assertEqual(row["schema_version"], 1)
        self.assertEqual(len(row["event_id"]), 64)
        self.assertEqual(len(row["dedupe_key"]), 64)

    def test_reconstructed_lifecycle_events_never_fabricate_observation_time(self):
        lifecycle = {
            "token_address": "0xTOKEN",
            "create_timestamp": 100,
            "create_block": 10,
            "buys": [
                {
                    "timestamp": 105,
                    "account": "0xBUYER",
                    "bnb_amount": 0.1,
                    "price": 1.0,
                    "block_number": 11,
                    "log_index": 2,
                }
            ],
            "sells": [],
            "graduated": True,
            "graduate_time": 200,
        }
        events = lifecycle_to_heat_events(lifecycle)
        self.assertEqual([event.event_type for event in events], ["token_create", "token_buy", "trade_stop"])
        self.assertTrue(all(event.reconstructed for event in events))
        self.assertTrue(all(event.observed_time is None for event in events))
        self.assertEqual(events[1].wallet, "0xbuyer")
        self.assertEqual(events[1].block_number, 11)
        self.assertEqual(events[0].block_number, 10)
        self.assertEqual(events[0].payload["creator"], None)

    def test_writer_deduplicates_same_source_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "heat.jsonl"
            event = HeatEvent(source="x", event_type="post_create", observed_time=100, payload={"id": "x"})
            writer = HeatEventWriter(path)
            self.assertTrue(writer.append(event))
            self.assertFalse(writer.append(event))
            self.assertFalse(HeatEventWriter(path).append(event))
            rows = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0])["source"], "x")

    def test_time_order_and_latency_validation_fail_closed(self):
        with self.assertRaises(ValueError):
            HeatEvent(source="x", event_type="post_create", source_event_time=101, observed_time=100)
        with self.assertRaises(ValueError):
            HeatEvent(
                source="x",
                event_type="post_create",
                source_event_time=100,
                observed_time=106,
                latency_seconds=1,
            )

    def test_cli_exports_jsonl_and_explicitly_marks_offline_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lifecycle = root / "lifecycle_1.jsonl"
            lifecycle.write_text(
                json.dumps(
                    {
                        "token_address": "0xTOKEN",
                        "create_timestamp": 100,
                        "buys": [{"timestamp": 101, "account": "0xBUYER", "price": 1.0}],
                        "sells": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output = root / "heat.jsonl"
            summary = root / "summary.json"
            exit_code = main(
                [
                    "--lifecycle-file",
                    str(lifecycle),
                    "--output",
                    str(output),
                    "--summary-output",
                    str(summary),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), 2)
            self.assertFalse(json.loads(summary.read_text(encoding="utf-8"))["safe_for_live_switch"])


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
import importlib.util
import sys
import types
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch


def _load_collect_continuous_module():
    module_path = Path(__file__).resolve().parents[2] / "tools" / "collect_continuous.py"
    spec = importlib.util.spec_from_file_location("worktree_collect_continuous", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader

    created_stubs = []

    # Provide lightweight stubs for optional runtime deps
    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")
        dotenv_stub.load_dotenv = lambda *args, **kwargs: None
        sys.modules["dotenv"] = dotenv_stub
        created_stubs.append("dotenv")

    # Stub heavy runtime imports not needed for cleanup test
    if "config.config" not in sys.modules:
        config_pkg = types.ModuleType("config")
        config_module = types.ModuleType("config.config")

        class _Config:
            FAST_RPC_ENDPOINTS = []

            @staticmethod
            def validate_rpc_config():
                return None

            @staticmethod
            def get_listener_mode():
                return "hybrid"

            @staticmethod
            def get_listener_ws_url():
                return "wss://example"

            @staticmethod
            def get_contract_config():
                return {
                    "contract_address": "0x1",
                    "contract_abi": [],
                    "log_provider_cooldown_seconds": 45.0,
                }

            @staticmethod
            def get_log_http_pool():
                return []

            @staticmethod
            def get_http_request_kwargs():
                return {}

        config_module.Config = _Config
        sys.modules["config"] = config_pkg
        sys.modules["config.config"] = config_module
        created_stubs.extend(["config", "config.config"])

    if "src.core.ws_manager" not in sys.modules:
        ws_module = types.ModuleType("src.core.ws_manager")

        class _WSConnectionManager:
            def __init__(self, *_args, **_kwargs):
                pass

        ws_module.WSConnectionManager = _WSConnectionManager
        sys.modules["src.core.ws_manager"] = ws_module
        created_stubs.append("src.core.ws_manager")

    if "src.core.listener" not in sys.modules:
        listener_module = types.ModuleType("src.core.listener")

        class _FourMemeListener:
            def __init__(self, *_args, **_kwargs):
                pass

        listener_module.FourMemeListener = _FourMemeListener
        sys.modules["src.core.listener"] = listener_module
        created_stubs.append("src.core.listener")

    if "src.data" not in sys.modules:
        data_module = types.ModuleType("src.data")

        class _DataCollector:
            def __init__(self, *_args, **_kwargs):
                self.output_dir = Path.cwd() / "data" / "training"

        data_module.DataCollector = _DataCollector
        sys.modules["src.data"] = data_module
        created_stubs.append("src.data")

    try:
        spec.loader.exec_module(module)
        return module
    finally:
        for name in reversed(created_stubs):
            sys.modules.pop(name, None)


collect_continuous_module = _load_collect_continuous_module()
ContinuousCollector = collect_continuous_module.ContinuousCollector


class TestCollectContinuousCleanup(unittest.TestCase):
    def test_cleanup_old_files_keeps_incremental_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            training_dir = Path(tmpdir) / "data" / "training"
            training_dir.mkdir(parents=True, exist_ok=True)

            snapshot_files = [
                training_dir / "lifecycle_20260227_010000.jsonl",
                training_dir / "lifecycle_20260227_020000.jsonl",
                training_dir / "lifecycle_20260227_030000.jsonl",
            ]
            incremental_files = [
                training_dir / "lifecycle_incremental_20260227_010000.jsonl",
                training_dir / "lifecycle_incremental_20260227_020000.jsonl",
                training_dir / "lifecycle_incremental_20260227_030000.jsonl",
            ]

            for index, fp in enumerate(snapshot_files + incremental_files, start=1):
                fp.write_text(f"{index}\n", encoding="utf-8")

            original_project_root = collect_continuous_module.project_root
            try:
                collect_continuous_module.project_root = Path(tmpdir)
                collector = ContinuousCollector()
                collector._cleanup_old_files(keep_count=2)
            finally:
                collect_continuous_module.project_root = original_project_root

            remaining_snapshots = sorted(training_dir.glob("lifecycle_[0-9]*.jsonl"))
            remaining_incrementals = sorted(training_dir.glob("lifecycle_incremental_*.jsonl"))

            self.assertEqual(len(remaining_snapshots), 2)
            self.assertEqual(len(remaining_incrementals), 3)


class TestCollectContinuousDrain(unittest.IsolatedAsyncioTestCase):
    async def test_collector_worker_drains_queued_events_after_running_false(self):
        collector = ContinuousCollector()
        collector.running = False
        collector._event_queue = asyncio.Queue()
        await collector._event_queue.put(
            (
                "TokenPurchase",
                {
                    "args": {
                        "token": "0x1",
                        "account": "0x2",
                        "amount": 1,
                        "cost": 1,
                    },
                    "timestamp": 123,
                    "blockNumber": 1,
                },
            )
        )

        calls = []

        class _FakeCollector:
            def on_token_purchase(self, event_data):
                calls.append(event_data["args"]["token"])

        collector.collector = _FakeCollector()

        await collector._collector_worker()

        self.assertEqual(calls, ["0x1"])
        self.assertTrue(collector._event_queue.empty())

    async def test_skip_flush_when_listener_is_still_catching_up(self):
        collector = ContinuousCollector()
        collector.flush_max_listener_lag_blocks = 16
        collector.listener = types.SimpleNamespace(
            get_stats=lambda: {"current_block_lag": 128}
        )

        self.assertTrue(collector._should_skip_flush_while_catching_up())


class TestCollectContinuousMetadataRestore(unittest.TestCase):
    def test_persist_runtime_state_saves_metadata_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = ContinuousCollector()
            collector.state_file = Path(tmpdir) / "collector_runtime_state.json"

            saved = []

            class _FakeCollector:
                def get_applied_cursor(self):
                    return None

                def save_token_metadata_index(self):
                    path = Path(tmpdir) / "token_metadata.json"
                    path.write_text("{}", encoding="utf-8")
                    saved.append(path)
                    return path

                def save_runtime_state(self, state_file, applied_cursor=None, last_processed_block=None):
                    state_file.write_text(str(last_processed_block), encoding="utf-8")
                    return state_file

            collector.collector = _FakeCollector()
            collector.listener = types.SimpleNamespace(get_stats=lambda: {"last_block_processed": 321})
            collector._persist_runtime_state()

            self.assertEqual(len(saved), 1)
            self.assertTrue(saved[0].exists())
            self.assertEqual("321", collector.state_file.read_text(encoding="utf-8"))



class TestCollectContinuousBoundedResume(unittest.TestCase):
    def test_bound_resume_cursor_clamps_old_checkpoint_to_recent_window(self):
        collector = ContinuousCollector()
        collector.resume_max_catchup_blocks = 256

        bounded = collector._bound_resume_cursor(
            resume_cursor={
                "block_number": 1000,
                "log_index": 7,
                "tx_hash": "aa" * 32,
            },
            current_block=1600,
        )

        self.assertEqual(
            {
                "block_number": 1344,
                "log_index": -1,
                "tx_hash": "",
            },
            bounded,
        )

    def test_bound_resume_cursor_keeps_recent_checkpoint_unchanged(self):
        collector = ContinuousCollector()
        collector.resume_max_catchup_blocks = 256

        bounded = collector._bound_resume_cursor(
            resume_cursor={
                "block_number": 1500,
                "log_index": 7,
                "tx_hash": "aa" * 32,
            },
            current_block=1600,
        )

        self.assertEqual(
            {
                "block_number": 1500,
                "log_index": 7,
                "tx_hash": "aa" * 32,
            },
            bounded,
        )


class TestCollectContinuousListenerMode(unittest.TestCase):
    def test_get_collector_listener_mode_defaults_to_http_only(self):
        collector = ContinuousCollector()

        self.assertEqual("http_only", collector._get_collector_listener_mode())

    def test_get_collector_listener_mode_allows_explicit_override(self):
        collector = ContinuousCollector()
        original_getenv = collect_continuous_module.os.getenv
        try:
            collect_continuous_module.os.getenv = lambda key, default=None: "hybrid" if key == "COLLECTOR_LISTENER_MODE" else original_getenv(key, default)
            self.assertEqual("hybrid", collector._get_collector_listener_mode())
        finally:
            collect_continuous_module.os.getenv = original_getenv

    def test_http_provider_receives_configured_proxy(self):
        captured = {}

        class _Provider:
            def __init__(self, endpoint, request_kwargs=None):
                captured["endpoint"] = endpoint
                captured["request_kwargs"] = request_kwargs

        collector = ContinuousCollector()
        with patch.object(
            collect_continuous_module.Config,
            "get_http_request_kwargs",
            return_value={"proxy": "http://127.0.0.1:10808"},
        ):
            collector._build_http_provider("https://rpc.example", _Provider)

        self.assertEqual(captured["endpoint"], "https://rpc.example")
        self.assertEqual(captured["request_kwargs"], {"proxy": "http://127.0.0.1:10808"})


class TestCollectContinuousCheckpointAge(unittest.TestCase):
    def test_should_skip_resume_when_checkpoint_is_older_than_threshold(self):
        collector = ContinuousCollector()
        collector.resume_max_age_seconds = 21600

        old_saved_at_dt = datetime(2026, 3, 24, tzinfo=timezone.utc)
        old_saved_at = old_saved_at_dt.isoformat()
        now_ts = int(old_saved_at_dt.timestamp()) + 21601

        should_skip = collector._should_skip_resume_due_to_checkpoint_age(
            {"saved_at": old_saved_at},
            now_ts=now_ts,
        )

        self.assertTrue(should_skip)

    def test_should_resume_when_checkpoint_is_within_threshold(self):
        collector = ContinuousCollector()
        collector.resume_max_age_seconds = 21600

        fresh_saved_at_dt = datetime(2026, 3, 25, 10, 30, tzinfo=timezone.utc)
        fresh_saved_at = fresh_saved_at_dt.isoformat()
        now_ts = int(fresh_saved_at_dt.timestamp()) + 3600

        should_skip = collector._should_skip_resume_due_to_checkpoint_age(
            {"saved_at": fresh_saved_at},
            now_ts=now_ts,
        )

        self.assertFalse(should_skip)

    def test_should_skip_invalid_saved_at(self):
        collector = ContinuousCollector()
        collector.resume_max_age_seconds = 21600

        should_skip = collector._should_skip_resume_due_to_checkpoint_age(
            {"saved_at": "not-a-timestamp"},
            now_ts=1742943600,
        )

        self.assertTrue(should_skip)


if __name__ == "__main__":
    unittest.main()

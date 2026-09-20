import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from src.pipeline import action_policy_replay_gate as gate


def _load_cli():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_action_policy_low_volume_replay.py"
    spec = importlib.util.spec_from_file_location("run_action_policy_low_volume_replay", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _robust_evaluation(*, net_profit_bnb, total_trades=10, entry_count=1):
    return {
        "net_profit_bnb": net_profit_bnb,
        "total_trades": total_trades,
        "max_drawdown_pct": -8.0,
        "win_rate": 0.7,
        "walk_forward_worst_net_return_pct": 4.0,
        "walk_forward_worst_max_drawdown_pct": -10.0,
        "stress_replay": [{
            "name": "harsh_friction",
            "net_return_pct": 3.0,
            "net_profit_bnb": 0.0005,
            "max_drawdown_pct": -11.0,
        }],
        "low_volume_rescue_entry_count": entry_count,
    }


class _FakeBuyModel:
    def predict_proba(self, rows):
        return [[0.01, 0.99] for _row in rows]


class _FakeEntryModel:
    def predict(self, rows):
        return [40.0 for _row in rows]


class TestActionPolicyReplayGate(unittest.TestCase):
    def test_missing_historical_report_is_an_empty_fail_closed_input(self):
        cli = _load_cli()

        report = cli._load_json(Path("/tmp/missing-latest-only-report.json"))

        self.assertTrue(report["_missing_report"])
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["evaluation"], {})

    def test_scores_only_low_volume_candidates_with_decision_time_features(self):
        train_rejected = {
            "candidate_sample": [
                {
                    "symbol": "REJ_WIN",
                    "recommended_policy": "quick_take_profit",
                    "prob": 0.99,
                    "pred_return": 40.0,
                    "volume_30s": 1.2,
                    "price_volatility": 0.16,
                    "flow_buy_sell_overlap_ratio_60s": 0.1,
                    "mfe_pct": 120.0,
                    "time_to_plus_25_seconds": 5.0,
                },
                {
                    "symbol": "REJ_SKIP",
                    "recommended_policy": "skip",
                    "prob": 0.99,
                    "pred_return": 40.0,
                    "volume_30s": 1.2,
                    "price_volatility": 0.16,
                    "flow_buy_sell_overlap_ratio_60s": 0.9,
                    "time_to_minus_18_seconds": 5.0,
                },
            ]
        }
        train_accepted = {
            "candidate_sample": [
                {
                    "symbol": "ACC_WIN",
                    "classification": "post_target_continuation",
                    "recommended_policy": "continue_hold",
                    "prob": 0.99,
                    "pred_return": 40.0,
                    "volume_30s": 2.0,
                    "price_volatility": 0.2,
                    "flow_buy_sell_overlap_ratio_60s": 0.1,
                    "post_target_window_returns_pct": {"60": 50.0},
                },
                {
                    "symbol": "ACC_SKIP",
                    "classification": "target_not_hit",
                    "recommended_policy": "no_action",
                    "prob": 0.99,
                    "pred_return": 40.0,
                    "volume_30s": 2.0,
                    "price_volatility": 0.2,
                    "flow_buy_sell_overlap_ratio_60s": 0.9,
                },
            ]
        }
        episode = [
            {
                "features": {
                    "current_price": 1.0,
                    "volume_30s": 1.2,
                    "price_volatility": 0.16,
                    "flow_buy_sell_overlap_ratio_60s": 0.1,
                },
                "meta": {"token_address": "0xwin", "sample_time": 100, "create_timestamp": 70},
            },
            {
                "features": {
                    "current_price": 1.1,
                    "volume_30s": 2.0,
                    "price_volatility": 0.16,
                    "flow_buy_sell_overlap_ratio_60s": 0.1,
                },
                "meta": {"token_address": "0xwin", "sample_time": 105, "create_timestamp": 70},
            },
        ]
        buy_artifact = {
            "model": _FakeBuyModel(),
            "entry_value_model": {"model": _FakeEntryModel()},
            "feature_names": ["current_price", "volume_30s", "price_volatility", "flow_buy_sell_overlap_ratio_60s"],
            "dropped_features": [],
        }
        runtime_params = {
            "buy_threshold": 0.98,
            "min_entry_score": 35.0,
            "min_entry_volume_30s": 1.5,
            "min_entry_price_volatility": 0.10,
            "buy_low_volume_rescue_min_prob": 0.982,
            "buy_low_volume_rescue_min_entry_volume_30s": 0.95,
            "buy_low_volume_rescue_max_entry_volume_30s": 1.5,
            "buy_low_volume_rescue_min_entry_price_volatility": 0.10,
            "buy_low_volume_rescue_max_age_seconds": 60.0,
        }

        score_maps, metadata = gate.fit_action_policy_model_and_score_episodes(
            train_rejected_reports=[train_rejected],
            train_accepted_reports=[train_accepted],
            eval_episodes=[episode],
            buy_artifact=buy_artifact,
            runtime_params=runtime_params,
            max_depth=1,
            min_samples_leaf=1,
            min_common_features=1,
        )

        self.assertTrue(metadata["trained"])
        self.assertIn("flow_buy_sell_overlap_ratio_60s", metadata["feature_names"])
        self.assertNotIn("mfe_pct", metadata["feature_names"])
        self.assertEqual(sorted(key for key in score_maps[0] if isinstance(key, int)), [0])
        self.assertGreaterEqual(score_maps[0][0], 0.5)


class TestActionPolicyLowVolumeReplayCli(unittest.TestCase):
    def test_score_universe_runtime_params_cover_candidate_grid_bounds(self):
        cli = _load_cli()
        cli.candidate_grid = lambda: iter([
            {
                "buy_low_volume_rescue_min_prob": 0.988,
                "buy_low_volume_rescue_min_entry_volume_30s": 1.15,
                "buy_low_volume_rescue_max_entry_volume_30s": 1.5,
                "buy_low_volume_rescue_min_entry_price_volatility": 0.10,
                "buy_low_volume_rescue_max_age_seconds": 60.0,
                "buy_low_volume_rescue_take_profit_pct": 0.25,
                "buy_low_volume_rescue_min_action_score": 0.65,
            },
            {
                "buy_low_volume_rescue_min_prob": 0.982,
                "buy_low_volume_rescue_min_entry_volume_30s": 0.95,
                "buy_low_volume_rescue_max_entry_volume_30s": 1.5,
                "buy_low_volume_rescue_min_entry_price_volatility": 0.08,
                "buy_low_volume_rescue_max_age_seconds": 120.0,
                "buy_low_volume_rescue_take_profit_pct": 0.35,
                "buy_low_volume_rescue_min_action_score": 0.35,
            },
        ])

        params = cli._score_universe_runtime_params({"buy_threshold": 0.98, "min_entry_volume_30s": 1.5})

        self.assertEqual(params["buy_low_volume_rescue_min_prob"], 0.982)
        self.assertEqual(params["buy_low_volume_rescue_min_entry_volume_30s"], 0.95)
        self.assertEqual(params["buy_low_volume_rescue_max_entry_volume_30s"], 1.5)
        self.assertEqual(params["buy_low_volume_rescue_min_entry_price_volatility"], 0.08)
        self.assertEqual(params["buy_low_volume_rescue_max_age_seconds"], 120.0)
        self.assertNotIn("buy_low_volume_rescue_min_action_score", params)

    def test_main_passes_action_policy_scores_into_low_volume_replay(self):
        cli = _load_cli()
        cli.candidate_grid = lambda: iter([{
            "buy_low_volume_rescue_min_prob": 0.982,
            "buy_low_volume_rescue_min_entry_volume_30s": 0.95,
            "buy_low_volume_rescue_max_entry_volume_30s": 1.5,
            "buy_low_volume_rescue_min_entry_price_volatility": 0.10,
            "buy_low_volume_rescue_max_age_seconds": 60,
            "buy_low_volume_rescue_take_profit_pct": 0.25,
            "buy_low_volume_rescue_min_action_score": 0.5,
        }])
        calls = []
        validation_samples = [{"meta": {"token_address": "0xvalidation", "sample_time": 1}}]
        final_samples = [{"meta": {"token_address": "0xfinal", "sample_time": 1}}]

        def fake_run_model_replay(**kwargs):
            calls.append(kwargs)
            overrides = dict(kwargs.get("overrides") or {})
            is_candidate = "buy_low_volume_rescue_min_prob" in overrides
            return {
                "generated_at": "2026-05-25T00:00:00+00:00",
                "split": kwargs["split"],
                "selection_role": "report_only",
                "git": {"commit": "abc123"},
                "model_checksums": {"buy_model.cbm": "sha256"},
                "replay_config": dict(overrides),
                "sample_count": 2,
                "lifecycle_paths": ["data/training/a.json"],
                "evaluation": _robust_evaluation(
                    net_profit_bnb=0.002 if is_candidate else 0.001,
                    entry_count=int(is_candidate),
                ),
            }

        fake_module = types.ModuleType("src.pipeline.model_replay")
        fake_module.run_model_replay = fake_run_model_replay

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "action_policy_low_volume_report.json"
            with patch.dict(sys.modules, {"src.pipeline.model_replay": fake_module}), patch.object(
                cli, "_load_common_context", return_value={"stub": True}
            ), patch.object(
                cli, "_low_volume_action_policy_score_maps_for_split", return_value=([{"0": 0.75}], {"trained": True})
            ), patch.object(
                cli,
                "_eval_samples_for_split",
                side_effect=lambda _args, *, split, **_kwargs: {
                    "samples": validation_samples if split == "validation" else final_samples,
                    "episodes": [[{"meta": {"token_address": split, "sample_time": 1}}]],
                },
            ):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    report = cli.main(["--output", str(output_path)])
            saved = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["decision"], "accept")
        self.assertFalse(saved["live_switch_evidence"])
        self.assertEqual([call["split"] for call in calls], ["validation", "validation", "final", "final"])
        self.assertNotIn("buy_low_volume_rescue_min_prob", calls[0]["overrides"])
        self.assertIn("low_volume_rescue_scores_by_episode", calls[1]["overrides"])
        self.assertEqual(calls[1]["overrides"]["buy_low_volume_rescue_min_action_score"], 0.5)
        self.assertIs(calls[1]["overrides"]["eval_samples"], validation_samples)
        self.assertIs(calls[3]["overrides"]["eval_samples"], final_samples)
        self.assertIn("action_policy_model", report)
        self.assertNotIn(
            "low_volume_rescue_scores_by_episode",
            saved["candidates"][0]["replay_metadata"]["replay_config"],
        )
        self.assertEqual(
            saved["candidates"][0]["replay_metadata"]["replay_config"]["low_volume_rescue_scores_by_episode_summary"],
            {
                "episode_count": 1,
                "non_empty_episode_count": 1,
                "scored_sample_count": 1,
                "max_episode_score_count": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()

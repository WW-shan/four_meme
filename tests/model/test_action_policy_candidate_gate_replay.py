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
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_action_policy_candidate_gate_replay.py"
    spec = importlib.util.spec_from_file_location("run_action_policy_candidate_gate_replay", path)
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
        "path_state_meta_gate_entry_count": entry_count,
        "path_state_meta_gate_signal_count": entry_count,
    }


def _passing_lcb_report():
    return {
        "decision": "shadow_reward_positive_lcb_replay_required",
        "support_gate": {"passes": True, "reasons": []},
        "stability_gate": {"passes": True, "reasons": []},
        "validation": {"reward_lcb_pct": 10.0, "reward_lcb_average_reward_pct": 10.0},
        "final": {"reward_lcb_pct": 5.0, "reward_lcb_average_reward_pct": 5.0},
    }


class _FakeBuyModel:
    def predict_proba(self, rows):
        return [[0.01, 0.99] for _row in rows]


class _FakeEntryModel:
    def predict(self, rows):
        return [40.0 for _row in rows]


class TestActionPolicyCandidateGateReplay(unittest.TestCase):
    def test_missing_historical_report_is_an_empty_fail_closed_input(self):
        cli = _load_cli()

        report = cli._load_json(Path("/tmp/missing-latest-only-report.json"))

        self.assertTrue(report["_missing_report"])
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["evaluation"], {})

    def test_scores_baseline_candidate_rows_with_support_complete_features(self):
        train_rejected = {
            "candidate_sample": [
                {
                    "symbol": "REJ_WIN",
                    "recommended_policy": "quick_take_profit",
                    "prob": 0.99,
                    "pred_return": 40.0,
                    "volume_30s": 1.8,
                    "price_volatility": 0.16,
                    "flow_sell_pressure_30s": 0.1,
                    "mfe_pct": 120.0,
                    "time_to_plus_25_seconds": 5.0,
                },
                {
                    "symbol": "REJ_SKIP",
                    "recommended_policy": "skip",
                    "prob": 0.99,
                    "pred_return": 40.0,
                    "volume_30s": 1.8,
                    "price_volatility": 0.16,
                    "flow_sell_pressure_30s": 0.9,
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
                    "flow_sell_pressure_30s": 0.1,
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
                    "flow_sell_pressure_30s": 0.9,
                },
            ]
        }
        episode = [
            {
                "features": {
                    "current_price": 1.0,
                    "volume_30s": 1.8,
                    "price_volatility": 0.16,
                    "flow_sell_pressure_30s": 0.1,
                },
                "meta": {"token_address": "0xwin", "sample_time": 100, "create_timestamp": 70},
            },
            {
                "features": {
                    "current_price": 1.1,
                    "volume_30s": 1.8,
                    "price_volatility": 0.16,
                    "flow_sell_pressure_30s": 0.9,
                },
                "meta": {"token_address": "0xlast", "sample_time": 105, "create_timestamp": 70},
            },
        ]
        buy_artifact = {
            "model": _FakeBuyModel(),
            "entry_value_model": {"model": _FakeEntryModel()},
            "feature_names": ["current_price", "volume_30s", "price_volatility", "flow_sell_pressure_30s"],
            "dropped_features": [],
        }
        runtime_params = {
            "buy_threshold": 0.98,
            "min_entry_score": 35.0,
            "min_entry_volume_30s": 1.5,
            "min_entry_price_volatility": 0.10,
            "buy_near_threshold_min_prob": 0.94,
            "buy_near_min_pred_return": 32.0,
            "buy_near_min_entry_volume_30s": 1.25,
            "buy_near_min_entry_price_volatility": 0.08,
        }

        score_maps, metadata = gate.fit_action_policy_candidate_gate_and_score_episodes(
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
        self.assertEqual(metadata["intended_use"], "path_state_candidate_gate_score_map_for_replay_only")
        self.assertIn("flow_sell_pressure_30s", metadata["feature_names"])
        self.assertNotIn("mfe_pct", metadata["feature_names"])
        self.assertEqual(
            score_maps[0]["__episode_meta__"],
            {
                "token": "0xwin",
                "sample_count": 2,
                "start_time": 100,
                "end_time": 105,
            },
        )
        self.assertEqual(sorted(key for key in score_maps[0] if isinstance(key, int)), [0])
        self.assertGreaterEqual(score_maps[0][0], 0.5)

    def test_scores_candidate_gate_eval_samples_in_one_batch(self):
        train_rejected = {
            "candidate_sample": [{
                "symbol": "REJ_SKIP",
                "recommended_policy": "skip",
                "prob": 0.99,
                "pred_return": 40.0,
                "volume_30s": 1.8,
                "price_volatility": 0.16,
                "flow_sell_pressure_30s": 0.9,
                "time_to_minus_18_seconds": 5.0,
            }]
        }
        train_accepted = {
            "candidate_sample": [{
                "symbol": "ACC_WIN",
                "classification": "post_target_continuation",
                "recommended_policy": "continue_hold",
                "prob": 0.99,
                "pred_return": 40.0,
                "volume_30s": 2.0,
                "price_volatility": 0.2,
                "flow_sell_pressure_30s": 0.1,
                "post_target_window_returns_pct": {"60": 50.0},
            }]
        }
        eval_episodes = []
        for index in range(3):
            eval_episodes.append([
                {
                    "features": {
                        "current_price": 1.0 + index,
                        "volume_30s": 1.8,
                        "price_volatility": 0.16,
                        "flow_sell_pressure_30s": 0.1,
                    },
                    "meta": {
                        "token_address": f"0x{index}",
                        "sample_time": 100 + index,
                        "create_timestamp": 70,
                    },
                },
                {
                    "features": {
                        "current_price": 1.0 + index,
                        "volume_30s": 1.8,
                        "price_volatility": 0.16,
                        "flow_sell_pressure_30s": 0.9,
                    },
                    "meta": {
                        "token_address": f"0x{index}",
                        "sample_time": 105 + index,
                        "create_timestamp": 70,
                    },
                },
            ])
        runtime_params = {
            "buy_threshold": 0.98,
            "buy_near_threshold_min_prob": 0.94,
            "min_entry_score": 35.0,
        }
        score_calls = []

        def fake_score_samples(samples, buy_artifact):
            score_calls.append(len(samples))
            return [0.99 for _sample in samples], [40.0 for _sample in samples]

        with patch.object(gate.ranker_probe, "_score_samples", side_effect=fake_score_samples):
            score_maps, metadata = gate.fit_action_policy_candidate_gate_and_score_episodes(
                train_rejected_reports=[train_rejected],
                train_accepted_reports=[train_accepted],
                eval_episodes=eval_episodes,
                buy_artifact={},
                runtime_params=runtime_params,
                max_depth=1,
                min_samples_leaf=1,
                min_common_features=1,
            )

        self.assertTrue(metadata["trained"])
        self.assertEqual(score_calls, [3])
        self.assertEqual(metadata["scored_candidate_count"], 3)
        self.assertEqual([sorted(key for key in row if isinstance(key, int)) for row in score_maps], [[0], [0], [0]])

    def test_support_failure_score_maps_keep_path_state_metadata(self):
        train_rejected = {
            "candidate_sample": [{
                "symbol": "REJ_SKIP",
                "recommended_policy": "skip",
                "prob": 0.99,
                "pred_return": 40.0,
                "volume_30s": 1.8,
                "price_volatility": 0.16,
                "time_to_minus_18_seconds": 5.0,
            }]
        }
        episode = [
            {
                "features": {"current_price": 1.0, "volume_30s": 1.8},
                "meta": {"token_address": "0xsupport", "sample_time": 100},
            },
            {
                "features": {"current_price": 1.1, "volume_30s": 1.9},
                "meta": {"token_address": "0xsupport", "sample_time": 105},
            },
        ]

        score_maps, metadata = gate.fit_action_policy_candidate_gate_and_score_episodes(
            train_rejected_reports=[train_rejected],
            train_accepted_reports=[],
            eval_episodes=[episode],
            buy_artifact={},
            runtime_params={"buy_threshold": 0.98},
            max_depth=1,
            min_samples_leaf=1,
            min_common_features=1,
        )

        self.assertFalse(metadata["trained"])
        self.assertIn("train_labels_missing_positive_or_negative", metadata["support_reasons"])
        self.assertEqual(
            score_maps,
            [{
                "__episode_meta__": {
                    "token": "0xsupport",
                    "sample_count": 2,
                    "start_time": 100,
                    "end_time": 105,
                },
            }],
        )

    def test_main_writes_strict_path_state_candidate_gate_report_after_lcb_gate(self):
        cli = _load_cli()
        cli.candidate_grid = lambda: iter([{"buy_path_state_meta_gate_min_score": 0.4}])
        calls = []

        def fake_run_model_replay(**kwargs):
            calls.append(kwargs)
            overrides = dict(kwargs.get("overrides") or {})
            is_candidate = "buy_path_state_meta_gate_min_score" in overrides
            return {
                "generated_at": "2026-05-26T00:00:00+00:00",
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

        def frozen_samples(_args, split, _base_overrides, _context):
            return [{
                "features": {},
                "meta": {"token_address": f"0x{split}", "sample_time": 1},
            }]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "candidate_gate_report.json"
            lcb_path = Path(tmpdir) / "lcb.json"
            lcb_path.write_text(json.dumps(_passing_lcb_report()), encoding="utf-8")
            with patch.dict(sys.modules, {"src.pipeline.model_replay": fake_module}), patch.object(
                cli,
                "_candidate_gate_score_maps_for_split",
                return_value=([{"0": 0.75}], {"trained": True}),
            ), patch.object(cli, "_split_samples_for_replay", side_effect=frozen_samples):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    report = cli.main([
                        "--output",
                        str(output_path),
                        "--source-lcb-report",
                        str(lcb_path),
                    ])
            saved = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["decision"], "accept")
        self.assertTrue(saved["source_lcb_gate"]["passes"])
        self.assertFalse(saved["live_switch_evidence"])
        self.assertEqual([call["split"] for call in calls], ["validation", "validation", "final", "final"])
        self.assertNotIn("buy_path_state_meta_gate_min_score", calls[0]["overrides"])
        self.assertIn("path_state_scores_by_episode", calls[1]["overrides"])
        self.assertEqual(calls[1]["overrides"]["buy_path_state_meta_gate_min_score"], 0.4)
        self.assertNotIn(
            "path_state_scores_by_episode",
            saved["candidates"][0]["replay_metadata"]["replay_config"],
        )
        self.assertEqual(
            saved["candidates"][0]["replay_metadata"]["replay_config"]["path_state_scores_by_episode_summary"],
            {
                "episode_count": 1,
                "non_empty_episode_count": 1,
                "scored_sample_count": 1,
                "max_episode_score_count": 1,
            },
        )

    def test_main_reuses_frozen_split_samples_for_all_replay_calls(self):
        cli = _load_cli()
        cli.candidate_grid = lambda: iter([{"buy_path_state_meta_gate_min_score": 0.4}])
        calls = []

        def fake_run_model_replay(**kwargs):
            calls.append(kwargs)
            overrides = dict(kwargs.get("overrides") or {})
            is_candidate = "buy_path_state_meta_gate_min_score" in overrides
            return {
                "generated_at": "2026-05-26T00:00:00+00:00",
                "split": kwargs["split"],
                "selection_role": "report_only",
                "git": {"commit": "abc123"},
                "model_checksums": {"buy_model.cbm": "sha256"},
                "replay_config": dict(overrides),
                "sample_count": len(overrides.get("eval_samples") or []),
                "lifecycle_paths": ["data/training/a.json"],
                "evaluation": _robust_evaluation(
                    net_profit_bnb=0.002 if is_candidate else 0.001,
                    entry_count=int(is_candidate),
                ),
            }

        fake_module = types.ModuleType("src.pipeline.model_replay")
        fake_module.run_model_replay = fake_run_model_replay

        def frozen_samples(_args, split, _base_overrides, _context):
            return [{
                "features": {},
                "meta": {"token_address": f"0x{split}", "sample_time": 1},
            }]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "candidate_gate_report.json"
            lcb_path = Path(tmpdir) / "lcb.json"
            lcb_path.write_text(json.dumps(_passing_lcb_report()), encoding="utf-8")
            with patch.dict(sys.modules, {"src.pipeline.model_replay": fake_module}), \
                 patch.object(cli, "_candidate_gate_score_maps_for_split", return_value=([{"0": 0.75}], {"trained": True})), \
                 patch.object(cli, "_split_samples_for_replay", side_effect=frozen_samples):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    cli.main([
                        "--output",
                        str(output_path),
                        "--source-lcb-report",
                        str(lcb_path),
                    ])

        self.assertEqual([call["split"] for call in calls], ["validation", "validation", "final", "final"])
        self.assertEqual(
            [call["overrides"]["eval_samples"][0]["meta"]["token_address"] for call in calls],
            ["0xvalidation", "0xvalidation", "0xfinal", "0xfinal"],
        )
        self.assertTrue(all(call["overrides"]["eval_samples_already_split_filtered"] for call in calls))
        self.assertIn("path_state_scores_by_episode", calls[1]["overrides"])
        self.assertIn("path_state_scores_by_episode", calls[3]["overrides"])


if __name__ == "__main__":
    unittest.main()

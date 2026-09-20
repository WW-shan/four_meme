import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import importlib.util
import sys


def _load_module():
    path = Path(__file__).resolve().parents[2] / "src" / "pipeline" / "train_hybrid.py"
    spec = importlib.util.spec_from_file_location("train_hybrid", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class TestTrainHybridPipeline(unittest.TestCase):
    def test_run_hybrid_training_returns_artifact_manifest(self):
        import tempfile

        m = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_files = [Path(tmpdir) / "lifecycle_incremental_001.jsonl", Path(tmpdir) / "lifecycle_incremental_002.jsonl"]
            for path in fake_files:
                path.write_text("{}\n", encoding="utf-8")
            with patch.object(m, "_discover_lifecycle_files", return_value=fake_files), \
                 patch.object(m, "_split_lifecycle_files", return_value=(fake_files[:1], fake_files[1:], 0)), \
                 patch.object(m, "_load_samples", return_value=[]), \
                 patch.object(m, "train_buy_model", return_value={"model_path": "buy_model.cbm", "threshold": 0.42, "threshold_path": "buy_threshold.json", "feature_schema_path": "feature_schema.json", "feature_names": ["current_price"]}), \
                 patch.object(m, "build_sell_env", return_value=MagicMock()), \
                 patch.object(m, "run_bc_warmstart", return_value={"weights": "bc.pt"}), \
                 patch.object(m, "run_ppo_finetune", return_value={"policy_path": "sell_policy.zip"}), \
                 patch.object(m, "run_ab_evaluation", return_value={"maxdd_delta": -0.25, "sortino_delta": 0.2}):
                result = m.run_hybrid_training({"output_dir": tmpdir})

        self.assertIn("buy_model", result["artifacts"])
        self.assertIn("sell_policy", result["artifacts"])
        self.assertIn("evaluation", result)

    def test_run_hybrid_training_writes_reproducibility_metadata(self):
        import json
        import tempfile

        m = _load_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_files = [Path(tmpdir) / "lifecycle_incremental_001.jsonl", Path(tmpdir) / "lifecycle_incremental_002.jsonl"]
            for path in fake_files:
                path.write_text("{}\n", encoding="utf-8")
            with patch.object(m, "_discover_lifecycle_files", return_value=fake_files), \
                 patch.object(m, "_split_lifecycle_files", return_value=(fake_files[:1], fake_files[1:], 0)), \
                 patch.object(m, "_load_samples", return_value=[]), \
                 patch.object(m, "train_buy_model", return_value={
                     "model_path": "buy_model.cbm",
                     "threshold": 0.42,
                     "threshold_path": "buy_threshold.json",
                     "feature_schema_path": "feature_schema.json",
                     "feature_names": ["current_price"],
                     "samples": [{"features": {"current_price": 1.0}, "meta": {"token_address": "0xtrain"}}],
                 }), \
                 patch.object(m, "build_sell_env", return_value=MagicMock()), \
                 patch.object(m, "run_bc_warmstart", return_value={"weights": "bc.pt"}), \
                 patch.object(m, "run_ppo_finetune", return_value={"policy_path": "sell_policy.zip"}), \
                 patch.object(m, "run_ab_evaluation", return_value={"maxdd_delta": -0.25, "sortino_delta": 0.2}), \
                 patch.object(m, "git_metadata", return_value={"commit": "abc123", "branch": "main", "dirty": False}), \
                 patch.object(m, "_artifact_checksums", return_value={"buy_model.cbm": "sha256-buy"}):
                result = m.run_hybrid_training({
                    "output_dir": tmpdir,
                    "command_args": ["--output-dir", tmpdir, "--total-timesteps", "32"],
                    "include_flow_features": True,
                    "sample_cache_dir": str(Path(tmpdir) / "cache"),
                })

            manifest = json.loads((Path(tmpdir) / "hybrid_manifest.json").read_text(encoding="utf-8"))

        self.assertIn("generated_at", result)
        self.assertEqual(result["command_args"], ["--output-dir", tmpdir, "--total-timesteps", "32"])
        self.assertEqual(result["git"], {"commit": "abc123", "branch": "main", "dirty": False})
        self.assertTrue(result["include_flow_features"])
        self.assertEqual(result["lifecycle_paths"], [str(path) for path in fake_files])
        self.assertEqual(result["sample_count"], 1)
        self.assertTrue(result["sample_cache_path"].startswith(str(Path(tmpdir) / "cache")))
        self.assertTrue(result["sample_cache_path"].endswith(".pkl"))
        self.assertEqual(result["model_checksums"], {"buy_model.cbm": "sha256-buy"})
        self.assertEqual(manifest["command_args"], result["command_args"])
        self.assertEqual(manifest["git"], result["git"])
        self.assertTrue(manifest["include_flow_features"])
        self.assertEqual(manifest["model_checksums"], result["model_checksums"])

    def test_run_hybrid_training_writes_selected_runtime_params_for_live_alignment(self):
        import json
        import tempfile

        m = _load_module()
        evaluation = {
            "buy_threshold": 0.98,
            "stop_loss": -0.18,
            "max_hold_seconds": 560,
            "min_policy_hold_seconds": 45,
            "position_fraction": 0.1,
            "max_position_fraction": 0.1,
            "fixed_stake_bnb": None,
            "max_open_positions": 8,
            "max_entry_age_seconds": 300,
            "min_entry_unique_buyers": 3,
            "min_entry_buy_count": 5,
            "min_entry_volume_30s": 1.5,
            "min_entry_price_volatility": 0.1,
            "entry_ranking_mode": "entry_value",
            "min_entry_score": 35.0,
            "buy_primary_score_rescue_min_prob": 0.985,
            "buy_primary_score_rescue_min_pred_return": 25.0,
            "buy_primary_score_rescue_min_entry_volume_30s": 3.0,
            "buy_primary_score_rescue_min_entry_price_volatility": 0.30,
            "buy_primary_score_rescue_min_age_seconds": 0.0,
            "use_pred_return_filter": True,
            "runtime_replay": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_files = [Path(tmpdir) / "lifecycle_incremental_001.jsonl", Path(tmpdir) / "lifecycle_incremental_002.jsonl"]
            with patch.object(m, "_discover_lifecycle_files", return_value=fake_files), \
                 patch.object(m, "_split_lifecycle_files", return_value=(fake_files[:1], fake_files[1:], 0)), \
                 patch.object(m, "_load_samples", return_value=[]), \
                 patch.object(m, "train_buy_model", return_value={"model_path": "buy_model.cbm", "threshold": 0.98, "threshold_path": "buy_threshold.json", "feature_schema_path": "feature_schema.json", "feature_names": ["current_price"]}), \
                 patch.object(m, "build_sell_env", return_value=MagicMock()), \
                 patch.object(m, "run_bc_warmstart", return_value={"weights": "bc.pt"}), \
                 patch.object(m, "run_ppo_finetune", return_value={"policy_path": "sell_policy.zip"}), \
                 patch.object(m, "run_ab_evaluation", return_value=evaluation):
                result = m.run_hybrid_training({"output_dir": tmpdir})
            manifest = json.loads((Path(tmpdir) / "hybrid_manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(result["selected_runtime_params"]["buy_threshold"], 0.98)
        self.assertEqual(result["selected_runtime_params"]["buy_primary_score_rescue_min_prob"], 0.985)
        self.assertEqual(result["selected_runtime_params"]["buy_primary_score_rescue_min_pred_return"], 25.0)
        self.assertEqual(manifest["selected_runtime_params"], result["selected_runtime_params"])
        self.assertNotIn("one_entry_per_token", result["selected_runtime_params"])
        self.assertNotIn("max_trades_per_token", result["selected_runtime_params"])

    def test_selected_runtime_params_exclude_replay_only_token_reentry_controls(self):
        m = _load_module()

        selected = m._selected_runtime_params_from_evaluation({
            "buy_threshold": 0.98,
            "one_entry_per_token": False,
            "max_trades_per_token": 3,
        })

        self.assertEqual(selected["buy_threshold"], 0.98)
        self.assertNotIn("one_entry_per_token", selected)
        self.assertNotIn("max_trades_per_token", selected)

    def test_run_hybrid_training_trains_and_publishes_entry_value_model_when_enabled(self):
        import tempfile

        m = _load_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_files = [Path(tmpdir) / "lifecycle_incremental_001.jsonl", Path(tmpdir) / "lifecycle_incremental_002.jsonl"]
            with patch.object(m, "_discover_lifecycle_files", return_value=fake_files), \
                 patch.object(m, "_split_lifecycle_files", return_value=(fake_files[:1], fake_files[1:], 0)), \
                 patch.object(m, "_load_samples", return_value=[]), \
                 patch.object(m, "train_buy_model", return_value={"model_path": "buy_model.cbm", "threshold": 0.42, "threshold_path": "buy_threshold.json", "feature_schema_path": "feature_schema.json", "feature_names": ["current_price"], "samples": [{"features": {"current_price": 1.0}, "meta": {"token_address": "0xtrain"}}]}), \
                 patch.object(m, "train_entry_value_model", return_value={"model_path": "entry_value_model.cbm", "target_label_column": "live_risk_adjusted_return_pct", "sample_count": 1, "model": MagicMock()}) as mock_entry, \
                 patch.object(m, "build_sell_env", return_value=MagicMock()), \
                 patch.object(m, "run_bc_warmstart", return_value={"weights": "bc.pt"}), \
                 patch.object(m, "run_ppo_finetune", return_value={"policy_path": "sell_policy.zip"}), \
                 patch.object(m, "run_ab_evaluation", return_value={"maxdd_delta": -0.25, "sortino_delta": 0.2}) as mock_eval:
                result = m.run_hybrid_training({"output_dir": tmpdir, "train_entry_value_model": True, "entry_ranking_mode": "entry_value"})

        self.assertTrue(mock_entry.called)
        self.assertEqual(mock_eval.call_args.args[0]["entry_ranking_mode"], "entry_value")
        self.assertIn("entry_value_model", result["artifacts"])

    def test_run_hybrid_training_can_refit_artifacts_on_all_lifecycle_files_after_eval(self):
        import json
        import tempfile

        m = _load_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_files = [
                Path(tmpdir) / f"lifecycle_incremental_{index:03d}.jsonl"
                for index in range(1, 5)
            ]
            for index, path in enumerate(fake_files, start=1):
                path.write_text(json.dumps({"token_address": f"0x{index}"}) + "\n", encoding="utf-8")
            train_files = fake_files[:2]
            eval_files = fake_files[2:]
            eval_samples = [
                {"features": {"current_price": 1.0}, "meta": {"token_address": "0xeval", "sample_time": 100}},
                {"features": {"current_price": 1.1}, "meta": {"token_address": "0xeval", "sample_time": 110}},
            ]
            observed = {"train_paths": []}

            def _fake_train_buy_model(cfg):
                call_index = len(observed["train_paths"])
                observed["train_paths"].append(list(cfg.get("lifecycle_paths") or []))
                return {
                    "model_path": f"buy_model_{call_index}.cbm",
                    "threshold": 0.5 + call_index / 10,
                    "threshold_path": str(Path(tmpdir) / f"buy_threshold_{call_index}.json"),
                    "feature_schema_path": f"feature_schema_{call_index}.json",
                    "feature_names": ["current_price"],
                    "sample_weighting": {
                        "mode": "token_balanced",
                        "sample_count": 10 + call_index,
                        "token_count": 2 + call_index,
                    },
                    "model": MagicMock(),
                    "samples": [
                        {
                            "features": {"current_price": 1.0},
                            "meta": {"token_address": f"0xtrain{call_index}", "sample_time": 1},
                        }
                    ],
                    "calibration_samples": [],
                }

            def _fake_eval(eval_config, buy_artifact, ppo_artifact, bc_artifact):
                return {
                    "total_trades": 1,
                    "win_rate": 1.0,
                    "net_return_pct": 10.0,
                    "max_drawdown_pct": -1.0,
                    "sortino_ratio": 0.5,
                    "buy_threshold": buy_artifact["threshold"],
                    "sell_episode_count": len(eval_config.get("eval_samples", [])),
                    "bc_samples": bc_artifact["bc_samples"],
                    "ppo_total_timesteps": ppo_artifact["total_timesteps"],
                    "train_file_count": eval_config["train_file_count"],
                    "eval_file_count": eval_config["eval_file_count"],
                    "overlap_token_count": eval_config["overlap_token_count"],
                    "pipeline_status": "ok",
                }

            def _fake_tune(_tune_config, _buy_artifact, _ppo_artifact):
                return {
                    "status": "selected",
                    "threshold": 0.88,
                    "previous_threshold": _buy_artifact["threshold"],
                    "replay": {"total_trades": 1},
                }

            def _fake_bc(_cfg, _env_bundle):
                call_index = len(observed.get("bc_calls", []))
                observed.setdefault("bc_calls", []).append(call_index)
                return {"weights": f"bc_{call_index}.pt", "bc_samples": 10 + call_index}

            def _fake_ppo(_cfg, _env_bundle, _bc_artifact):
                call_index = len(observed.get("ppo_calls", []))
                observed.setdefault("ppo_calls", []).append(call_index)
                return {"policy_path": f"sell_policy_{call_index}.zip", "total_timesteps": 128}

            with patch.object(m, "_discover_lifecycle_files", return_value=fake_files), \
                 patch.object(m, "_split_lifecycle_files", return_value=(train_files, eval_files, 0)), \
                 patch.object(m, "_load_samples", return_value=eval_samples), \
                 patch.object(m, "train_buy_model", side_effect=_fake_train_buy_model), \
                 patch.object(m, "build_sell_env", return_value={"env": object(), "episodes": [[{}]], "episode_count": 1}), \
                 patch.object(m, "run_bc_warmstart", side_effect=_fake_bc), \
                 patch.object(m, "run_ppo_finetune", side_effect=_fake_ppo), \
                 patch.object(m, "_tune_buy_threshold_by_replay", side_effect=_fake_tune), \
                 patch.object(m, "run_ab_evaluation", side_effect=_fake_eval):
                result = m.run_hybrid_training({
                    "output_dir": tmpdir,
                    "fit_artifacts_on_all_data": True,
                    "risk_tune_buy_threshold": True,
                })

            manifest = json.loads((Path(tmpdir) / "hybrid_manifest.json").read_text(encoding="utf-8"))
            production_threshold_file = Path(result["artifacts"]["buy_model"]["threshold_path"])
            production_threshold = json.loads(production_threshold_file.read_text(encoding="utf-8"))["threshold"]

        self.assertEqual(observed["train_paths"], [train_files, fake_files])
        self.assertEqual(result["artifacts"]["buy_model"]["model_path"], "buy_model_1.cbm")
        self.assertEqual(result["artifacts"]["buy_model"]["threshold"], 0.88)
        self.assertEqual(production_threshold, 0.88)
        self.assertEqual(result["artifacts"]["sell_policy"]["policy_path"], "sell_policy_1.zip")
        self.assertEqual(result["artifacts"]["buy_model"]["sample_weighting"]["mode"], "token_balanced")
        self.assertEqual(manifest["artifacts"]["buy_model"]["sample_weighting"]["sample_count"], 11)
        self.assertEqual(result["production_fit"]["artifact_scope"], "all_lifecycle_files")
        self.assertEqual(result["production_fit"]["lifecycle_file_count"], 4)
        self.assertEqual(result["production_fit"]["selection_evaluation_scope"], "holdout_split")
        self.assertEqual(manifest["production_fit"], result["production_fit"])
        self.assertEqual(manifest["artifacts"]["buy_model"]["threshold"], 0.88)

    def test_split_lifecycle_files_three_way_reserves_chronological_validation_and_final_test(self):
        import json
        import tempfile

        m = _load_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            files = []
            for index in range(5):
                path = Path(tmpdir) / f"lifecycle_incremental_{index + 1:03d}.jsonl"
                path.write_text(json.dumps({"token_address": f"0x{index}"}) + "\n", encoding="utf-8")
                files.append(path)

            split = m._split_lifecycle_files_three_way(
                files,
                train_split_ratio=0.6,
                validation_split_ratio=0.2,
                min_validation_files=1,
                min_eval_files=1,
            )

        self.assertEqual(split["train_files"], files[:3])
        self.assertEqual(split["validation_files"], files[3:4])
        self.assertEqual(split["eval_files"], files[4:])
        self.assertEqual(split["raw_train_validation_overlap_count"], 0)
        self.assertEqual(split["raw_final_overlap_token_count"], 0)

    def test_run_hybrid_training_three_way_uses_validation_for_risk_tuning_and_final_eval(self):
        import json
        import tempfile

        m = _load_module()

        validation_samples = [
            {"features": {"current_price": 1.0}, "meta": {"token_address": "0xval", "sample_time": 100}},
            {"features": {"current_price": 1.1}, "meta": {"token_address": "0xval", "sample_time": 110}},
        ]
        final_samples = [
            {"features": {"current_price": 2.0}, "meta": {"token_address": "0xfinal", "sample_time": 200}},
            {"features": {"current_price": 2.2}, "meta": {"token_address": "0xfinal", "sample_time": 210}},
        ]
        observed = {"load_configs": [], "eval_configs": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_files = []
            for index, token in enumerate(["0xtrain1", "0xtrain2", "0xtrain3", "0xval", "0xfinal"], start=1):
                path = Path(tmpdir) / f"lifecycle_incremental_{index:03d}.jsonl"
                path.write_text(json.dumps({"token_address": token}) + "\n", encoding="utf-8")
                fake_files.append(path)

            def _fake_load_samples(load_config):
                observed["load_configs"].append(dict(load_config))
                if load_config.get("evaluation_split") == "validation":
                    return validation_samples
                if load_config.get("evaluation_split") == "final_test":
                    return final_samples
                self.fail(f"unexpected load split: {load_config.get('evaluation_split')}")

            def _fake_tune(tune_config, buy_artifact, ppo_artifact):
                observed["tune_config"] = dict(tune_config)
                observed["tune_calibration_samples"] = list(buy_artifact.get("calibration_samples") or [])
                return {"status": "selected", "threshold": 0.8, "previous_threshold": 0.5, "replay": {"total_trades": 1}}

            def _fake_eval(eval_config, buy_artifact, ppo_artifact, bc_artifact):
                observed["eval_configs"].append(dict(eval_config))
                return {
                    "split": eval_config.get("evaluation_split"),
                    "total_trades": 1,
                    "win_rate": 1.0,
                    "net_return_pct": 10.0,
                    "max_drawdown_pct": -1.0,
                    "sortino_ratio": 0.5,
                    "buy_threshold": buy_artifact["threshold"],
                    "sell_episode_count": len(eval_config.get("eval_samples", [])),
                    "bc_samples": bc_artifact["bc_samples"],
                    "ppo_total_timesteps": ppo_artifact["total_timesteps"],
                    "train_file_count": eval_config["train_file_count"],
                    "validation_file_count": eval_config.get("validation_file_count", 0),
                    "eval_file_count": eval_config["eval_file_count"],
                    "overlap_token_count": eval_config["overlap_token_count"],
                    "pipeline_status": "ok",
                }

            with patch.object(m, "_discover_lifecycle_files", return_value=fake_files), \
                 patch.object(m, "_load_samples", side_effect=_fake_load_samples), \
                 patch.object(
                     m,
                     "train_buy_model",
                     return_value={
                         "model_path": "buy_model.cbm",
                         "threshold": 0.5,
                         "feature_schema_path": "feature_schema.json",
                         "feature_names": ["current_price"],
                         "model": MagicMock(),
                         "samples": [{"features": {"current_price": 1.0}, "meta": {"token_address": "0xtrain1"}}],
                         "calibration_samples": [{"features": {"current_price": 0.5}, "meta": {"token_address": "0xtraincal"}}],
                     },
                 ), \
                 patch.object(m, "build_sell_env", return_value={"env": object(), "episodes": [[{}]], "episode_count": 1}), \
                 patch.object(m, "run_bc_warmstart", return_value={"weights": "bc.pt", "bc_samples": 10}), \
                 patch.object(m, "run_ppo_finetune", return_value={"policy_path": "sell_policy.zip", "total_timesteps": 128, "model": object()}), \
                 patch.object(m, "_tune_buy_threshold_by_replay", side_effect=_fake_tune), \
                 patch.object(m, "run_ab_evaluation", side_effect=_fake_eval):
                result = m.run_hybrid_training(
                    {
                        "output_dir": tmpdir,
                        "train_split_ratio": 0.6,
                        "validation_split_ratio": 0.2,
                        "min_validation_files": 1,
                        "min_eval_files": 1,
                        "risk_tune_buy_threshold": True,
                    }
                )

        self.assertTrue(result["three_way_split"]["enabled"])
        self.assertEqual(result["three_way_split"]["train_file_count"], 3)
        self.assertEqual(result["three_way_split"]["validation_file_count"], 1)
        self.assertEqual(result["three_way_split"]["eval_file_count"], 1)
        self.assertEqual(observed["tune_calibration_samples"], validation_samples)
        self.assertEqual(observed["tune_config"]["evaluation_split"], "validation")
        self.assertEqual([config["evaluation_split"] for config in observed["load_configs"]], ["validation", "final_test"])
        self.assertEqual([config["evaluation_split"] for config in observed["eval_configs"]], ["validation", "final_test"])
        self.assertEqual(observed["eval_configs"][0]["eval_samples"], validation_samples)
        self.assertEqual(observed["eval_configs"][1]["eval_samples"], final_samples)
        self.assertEqual(result["validation_evaluation"]["split"], "validation")
        self.assertEqual(result["evaluation"]["split"], "final_test")

    def test_prepare_training_rows_rejects_empty_samples(self):
        m = _load_module()
        with self.assertRaises(ValueError):
            m._prepare_training_rows([], "max_return_pct", 80.0)

    def test_prepare_training_rows_rejects_missing_target_label(self):
        m = _load_module()
        samples = [
            {
                "features": {"current_price": 1.0},
                "label": {"executable_return_pct": 25.0},
                "meta": {"token_address": "A", "sample_time": 100},
            }
        ]

        with self.assertRaisesRegex(ValueError, "missing target label column: live_delay_robust_return_pct"):
            m._prepare_training_rows(samples, "live_delay_robust_return_pct", 20.0)

    def test_limit_samples_per_token_keeps_even_time_coverage(self):
        m = _load_module()
        samples = [
            {
                "features": {"current_price": float(index)},
                "label": {"max_return_pct": float(index)},
                "meta": {"token_address": "A", "sample_time": 100 + index, "sample_interval": index},
            }
            for index in range(10)
        ]

        limited = m._limit_samples_per_token(samples, 4)

        self.assertEqual([sample["meta"]["sample_interval"] for sample in limited], [0, 3, 6, 9])

    def test_split_samples_for_calibration_scales_with_tokens_not_candidate_indices(self):
        m = _load_module()
        samples = []
        labels = []
        for token_index in range(200):
            for sample_index in range(3):
                samples.append({
                    "features": {"current_price": float(sample_index)},
                    "meta": {
                        "token_address": f"0x{token_index:04x}",
                        "sample_time": token_index * 10 + sample_index,
                    },
                })
                labels.append((token_index + sample_index) % 2)

        with patch.object(m, "_indices_have_two_classes", wraps=m._indices_have_two_classes) as mock_classes:
            fit_indices, calibration_indices = m._split_samples_for_calibration(
                samples,
                labels,
                ratio=0.2,
                min_samples=20,
                random_state=7,
            )

        self.assertTrue(fit_indices)
        self.assertTrue(calibration_indices)
        self.assertLessEqual(mock_classes.call_count, 4)
        self.assertLess(abs(len(calibration_indices) - 120), 3)
        self.assertFalse(set(fit_indices).intersection(calibration_indices))

    def test_prepare_training_rows_rejects_single_class_target(self):
        m = _load_module()
        samples = [
            {
                "features": {"current_price": 1.0, "buy_pressure": 0.6},
                "label": {"max_return_pct": 10.0},
                "meta": {"token_address": "A", "sample_time": 100},
            },
            {
                "features": {"current_price": 1.1, "buy_pressure": 0.7},
                "label": {"max_return_pct": 12.0},
                "meta": {"token_address": "B", "sample_time": 110},
            },
        ]
        with self.assertRaises(ValueError):
            m._prepare_training_rows(samples, "max_return_pct", 80.0)

    def test_train_buy_model_saves_model_and_threshold(self):
        import tempfile
        m = _load_module()
        samples = [
            {"features": {"current_price": 1.0, "buy_pressure": 0.4}, "label": {"max_return_pct": 20.0}, "meta": {"token_address": "A", "sample_time": 100}},
            {"features": {"current_price": 1.1, "buy_pressure": 0.8}, "label": {"max_return_pct": 120.0}, "meta": {"token_address": "B", "sample_time": 110}},
            {"features": {"current_price": 1.2, "buy_pressure": 0.3}, "label": {"max_return_pct": 10.0}, "meta": {"token_address": "C", "sample_time": 120}},
            {"features": {"current_price": 1.3, "buy_pressure": 0.9}, "label": {"max_return_pct": 200.0}, "meta": {"token_address": "D", "sample_time": 130}},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(m, "_load_samples", return_value=samples), \
                 patch.object(m, "BuyCatBoostModel") as MockModel:
                fake = MagicMock()
                fake.predict_proba.return_value = [[0.3, 0.7]] * len(samples)
                fake.select_threshold.return_value = 0.42
                fake.model = MagicMock()

                def _save_model(path):
                    Path(path).write_text("cbm", encoding="utf-8")

                fake.model.save_model.side_effect = _save_model
                MockModel.return_value = fake

                out = m.train_buy_model({"output_dir": tmpdir, "target_label_column": "max_return_pct", "target_threshold_value": 80.0})

            self.assertTrue(Path(out["model_path"]).exists())
            self.assertTrue(Path(out["threshold_path"]).exists())
            self.assertIn("labels", out)

    def test_train_buy_model_can_cap_buy_samples_without_shortening_sell_episodes(self):
        import tempfile

        m = _load_module()
        samples = []
        for token, base_price, base_label in (("A", 1.0, 10.0), ("B", 2.0, 120.0)):
            for index in range(3):
                samples.append(
                    {
                        "features": {"current_price": base_price + index},
                        "label": {"max_return_pct": base_label + index},
                        "meta": {"token_address": token, "sample_time": 100 + index},
                    }
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(m, "_load_samples", return_value=samples), \
                 patch.object(m, "_split_samples_for_calibration", return_value=([0, 1], [])), \
                 patch.object(m, "BuyCatBoostModel") as MockModel:
                fake = MagicMock()
                fake.predict_proba.return_value = [[0.3, 0.7], [0.3, 0.7]]
                fake.select_threshold.return_value = 0.42
                fake.model = MagicMock()
                fake.model.save_model.side_effect = lambda path: Path(path).write_text("cbm", encoding="utf-8")
                MockModel.return_value = fake

                out = m.train_buy_model(
                    {
                        "output_dir": tmpdir,
                        "target_label_column": "max_return_pct",
                        "target_threshold_value": 80.0,
                        "buy_max_samples_per_token": 1,
                    }
                )

        self.assertEqual(len(out["samples"]), 2)
        self.assertEqual(len(out["sell_training_samples"]), 6)

    def test_train_buy_model_can_balance_fit_weights_by_token(self):
        import tempfile
        m = _load_module()
        samples = [
            {"features": {"current_price": 1.0}, "label": {"max_return_pct": 10.0}, "meta": {"token_address": "A", "sample_time": 100}},
            {"features": {"current_price": 1.1}, "label": {"max_return_pct": 120.0}, "meta": {"token_address": "A", "sample_time": 110}},
            {"features": {"current_price": 1.2}, "label": {"max_return_pct": 130.0}, "meta": {"token_address": "A", "sample_time": 120}},
            {"features": {"current_price": 2.0}, "label": {"max_return_pct": 140.0}, "meta": {"token_address": "B", "sample_time": 200}},
        ]
        observed = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(m, "_load_samples", return_value=samples), \
                 patch.object(m, "_split_samples_for_calibration", return_value=([0, 1, 2, 3], [])), \
                 patch.object(m, "BuyCatBoostModel") as MockModel:
                fake = MagicMock()

                def _fit(_X, _y, eval_set=None, sample_weight=None):
                    observed["sample_weight"] = list(sample_weight)
                    return fake

                def _save_model(path):
                    Path(path).write_text("cbm", encoding="utf-8")

                fake.fit.side_effect = _fit
                fake.predict_proba.return_value = [[0.4, 0.6]] * len(samples)
                fake.select_threshold.return_value = 0.42
                fake.model = MagicMock()
                fake.model.save_model.side_effect = _save_model
                MockModel.return_value = fake

                out = m.train_buy_model({
                    "output_dir": tmpdir,
                    "target_label_column": "max_return_pct",
                    "target_threshold_value": 80.0,
                    "buy_sample_weighting": "token_balanced",
                })

        self.assertAlmostEqual(sum(observed["sample_weight"][:3]), observed["sample_weight"][3])
        self.assertEqual(out["sample_weighting"]["mode"], "token_balanced")
        self.assertEqual(out["sample_weighting"]["token_count"], 2)

    def test_train_buy_model_can_weight_recent_samples_more_heavily(self):
        import tempfile
        m = _load_module()
        samples = [
            {"features": {"current_price": 1.0}, "label": {"max_return_pct": 10.0}, "meta": {"token_address": "A", "sample_time": 0}},
            {"features": {"current_price": 1.1}, "label": {"max_return_pct": 120.0}, "meta": {"token_address": "B", "sample_time": 3600}},
            {"features": {"current_price": 1.2}, "label": {"max_return_pct": 130.0}, "meta": {"token_address": "C", "sample_time": 7200}},
        ]
        observed = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(m, "_load_samples", return_value=samples), \
                 patch.object(m, "_split_samples_for_calibration", return_value=([0, 1, 2], [])), \
                 patch.object(m, "BuyCatBoostModel") as MockModel:
                fake = MagicMock()

                def _fit(_X, _y, eval_set=None, sample_weight=None):
                    observed["sample_weight"] = list(sample_weight)
                    return fake

                def _save_model(path):
                    Path(path).write_text("cbm", encoding="utf-8")

                fake.fit.side_effect = _fit
                fake.predict_proba.return_value = [[0.4, 0.6]] * len(samples)
                fake.select_threshold.return_value = 0.42
                fake.model = MagicMock()
                fake.model.save_model.side_effect = _save_model
                MockModel.return_value = fake

                out = m.train_buy_model({
                    "output_dir": tmpdir,
                    "target_label_column": "max_return_pct",
                    "target_threshold_value": 80.0,
                    "buy_sample_weighting": "recency_decay",
                    "buy_recency_half_life_hours": 1.0,
                })

        self.assertLess(observed["sample_weight"][0], observed["sample_weight"][1])
        self.assertLess(observed["sample_weight"][1], observed["sample_weight"][2])
        self.assertAlmostEqual(sum(observed["sample_weight"]) / len(observed["sample_weight"]), 1.0)
        self.assertEqual(out["sample_weighting"]["mode"], "recency_decay")
        self.assertEqual(out["sample_weighting"]["half_life_hours"], 1.0)

    def test_train_buy_model_defaults_to_executable_return_target(self):
        import tempfile
        m = _load_module()
        samples = [
            {"features": {"current_price": 1.0, "signal": 0.1}, "label": {"max_return_pct": 200.0, "executable_return_pct": 10.0}, "meta": {"token_address": "A", "sample_time": 100}},
            {"features": {"current_price": 1.1, "signal": 0.2}, "label": {"max_return_pct": 10.0, "executable_return_pct": 120.0}, "meta": {"token_address": "B", "sample_time": 110}},
            {"features": {"current_price": 1.2, "signal": 0.3}, "label": {"max_return_pct": 160.0, "executable_return_pct": 20.0}, "meta": {"token_address": "C", "sample_time": 120}},
            {"features": {"current_price": 1.3, "signal": 0.4}, "label": {"max_return_pct": 5.0, "executable_return_pct": 140.0}, "meta": {"token_address": "D", "sample_time": 130}},
        ]
        observed = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(m, "_load_samples", return_value=samples), \
                 patch.object(m, "BuyCatBoostModel") as MockModel:
                fake = MagicMock()

                def _fit(X, y, eval_set=None):
                    observed["labels"] = list(y)
                    return fake

                def _predict_proba(X):
                    return [[0.4, 0.6] for _ in range(len(X))]

                def _save_model(path):
                    Path(path).write_text("cbm", encoding="utf-8")

                fake.fit.side_effect = _fit
                fake.predict_proba.side_effect = _predict_proba
                fake.select_threshold.return_value = 0.42
                fake.model = MagicMock()
                fake.model.save_model.side_effect = _save_model
                MockModel.return_value = fake

                out = m.train_buy_model(
                    {
                        "output_dir": tmpdir,
                        "target_threshold_value": 80.0,
                        "buy_calibration_ratio": 0.0,
                    }
                )

        self.assertEqual(observed["labels"], [0, 1, 0, 1])
        self.assertEqual(out["target_label_column"], "executable_return_pct")

    def test_load_samples_passes_execution_label_controls_to_builder(self):
        m = _load_module()
        fake_builder = MagicMock()
        fake_builder.samples = []

        with patch.object(m, "DatasetBuilder", return_value=fake_builder) as MockBuilder:
            samples = m._load_samples(
                {
                    "lifecycle_dir": "data/training",
                    "fee_bps": 80.0,
                    "slippage_bps": 150.0,
                    "stop_loss": -0.4,
                    "target_threshold_value": 60.0,
                    "max_samples_per_token": 10,
                    "min_entry_unique_buyers": 2,
                    "min_entry_buy_count": 4,
                    "entry_delay_seconds": 3,
                    "exit_delay_seconds": 4,
                    "label_live_downside_penalty_weight": 0.75,
                    "include_flow_features": True,
                }
            )

        self.assertEqual(samples, [])
        kwargs = MockBuilder.call_args.kwargs
        self.assertEqual(kwargs["label_fee_bps"], 80.0)
        self.assertEqual(kwargs["label_slippage_bps"], 150.0)
        self.assertEqual(kwargs["label_stop_loss_pct"], -40.0)
        self.assertEqual(kwargs["label_target_return_pct"], 60.0)
        self.assertEqual(kwargs["label_entry_delay_seconds"], 3)
        self.assertEqual(kwargs["label_exit_delay_seconds"], 4)
        self.assertEqual(kwargs["label_live_downside_penalty_weight"], 0.75)
        self.assertEqual(kwargs["min_entry_unique_buyers"], 2)
        self.assertEqual(kwargs["min_entry_buy_count"], 4)
        self.assertTrue(kwargs["include_flow_features"])
        fake_builder.load_lifecycle_files.assert_called_once()

    def test_load_samples_reuses_sample_cache_for_unchanged_lifecycle_files(self):
        import tempfile

        m = _load_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            lifecycle_path = Path(tmpdir) / "lifecycle_incremental_001.jsonl"
            lifecycle_path.write_text('{"token_address":"0x1"}\n', encoding="utf-8")
            cache_dir = Path(tmpdir) / "sample-cache"
            samples = [
                {
                    "features": {"current_price": 1.0},
                    "label": {"executable_return_pct": 12.5},
                    "meta": {"token_address": "0x1", "sample_time": 100},
                }
            ]

            fake_builder = MagicMock()
            fake_builder.samples = list(samples)
            with patch.object(m, "DatasetBuilder", return_value=fake_builder):
                first = m._load_samples(
                    {
                        "lifecycle_paths": [lifecycle_path],
                        "sample_cache_dir": cache_dir,
                        "max_samples_per_token": 10,
                    }
                )

            self.assertEqual(first, samples)
            self.assertTrue(list(cache_dir.glob("*.pkl")))

            with patch.object(m, "DatasetBuilder", side_effect=AssertionError("cache miss")):
                second = m._load_samples(
                    {
                        "lifecycle_paths": [lifecycle_path],
                        "sample_cache_dir": cache_dir,
                        "max_samples_per_token": 10,
                    }
                )

        self.assertEqual(second, samples)

    def test_load_samples_invalidates_cache_when_lifecycle_file_changes(self):
        import tempfile

        m = _load_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            lifecycle_path = Path(tmpdir) / "lifecycle_incremental_001.jsonl"
            lifecycle_path.write_text('{"token_address":"0x1"}\n', encoding="utf-8")
            cache_dir = Path(tmpdir) / "sample-cache"

            first_samples = [
                {
                    "features": {"current_price": 1.0},
                    "label": {"executable_return_pct": 12.5},
                    "meta": {"token_address": "0x1", "sample_time": 100},
                }
            ]
            second_samples = [
                {
                    "features": {"current_price": 2.0},
                    "label": {"executable_return_pct": 25.0},
                    "meta": {"token_address": "0x1", "sample_time": 100},
                }
            ]

            first_builder = MagicMock()
            first_builder.samples = list(first_samples)
            with patch.object(m, "DatasetBuilder", return_value=first_builder):
                first = m._load_samples(
                    {
                        "lifecycle_paths": [lifecycle_path],
                        "sample_cache_dir": cache_dir,
                    }
                )
            self.assertEqual(first, first_samples)

            lifecycle_path.write_text('{"token_address":"0x1"}\n{"token_address":"0x2"}\n', encoding="utf-8")
            second_builder = MagicMock()
            second_builder.samples = list(second_samples)
            with patch.object(m, "DatasetBuilder", return_value=second_builder):
                second = m._load_samples(
                    {
                        "lifecycle_paths": [lifecycle_path],
                        "sample_cache_dir": cache_dir,
                    }
                )

        self.assertEqual(second, second_samples)

    def test_train_buy_model_writes_feature_schema(self):
        import json
        import tempfile

        m = _load_module()
        samples = [
            {"features": {"current_price": 1.0, "buy_pressure": 0.4}, "label": {"max_return_pct": 20.0}, "meta": {"token_address": "A", "sample_time": 100}},
            {"features": {"current_price": 1.1, "buy_pressure": 0.8}, "label": {"max_return_pct": 120.0}, "meta": {"token_address": "B", "sample_time": 110}},
            {"features": {"current_price": 1.2, "buy_pressure": 0.3}, "label": {"max_return_pct": 10.0}, "meta": {"token_address": "C", "sample_time": 120}},
            {"features": {"current_price": 1.3, "buy_pressure": 0.9}, "label": {"max_return_pct": 200.0}, "meta": {"token_address": "D", "sample_time": 130}},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(m, "_load_samples", return_value=samples), \
                 patch.object(m, "BuyCatBoostModel") as MockModel:
                fake = MagicMock()
                fake.predict_proba.return_value = [[0.3, 0.7]] * len(samples)
                fake.select_threshold.return_value = 0.42
                fake.model = MagicMock()

                def _save_model(path):
                    Path(path).write_text("cbm", encoding="utf-8")

                fake.model.save_model.side_effect = _save_model
                MockModel.return_value = fake

                out = m.train_buy_model({"output_dir": tmpdir, "target_label_column": "max_return_pct", "target_threshold_value": 80.0})

            schema_path = Path(out["feature_schema_path"])
            self.assertTrue(schema_path.exists())
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            self.assertEqual(schema["feature_names"], ["buy_pressure", "current_price"])
            self.assertEqual(out["feature_names"], ["buy_pressure", "current_price"])

    def test_train_buy_model_writes_stable_feature_schema_order(self):
        import json
        import tempfile

        m = _load_module()
        samples = [
            {"features": {"buy_pressure": 0.4, "current_price": 1.0}, "label": {"max_return_pct": 20.0}, "meta": {"token_address": "A", "sample_time": 100}},
            {"features": {"current_price": 1.1, "buy_pressure": 0.8}, "label": {"max_return_pct": 120.0}, "meta": {"token_address": "B", "sample_time": 110}},
            {"features": {"buy_pressure": 0.3, "current_price": 1.2}, "label": {"max_return_pct": 10.0}, "meta": {"token_address": "C", "sample_time": 120}},
            {"features": {"current_price": 1.3, "buy_pressure": 0.9}, "label": {"max_return_pct": 200.0}, "meta": {"token_address": "D", "sample_time": 130}},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(m, "_load_samples", return_value=samples), \
                 patch.object(m, "BuyCatBoostModel") as MockModel:
                fake = MagicMock()
                fake.predict_proba.return_value = [[0.3, 0.7]] * len(samples)
                fake.select_threshold.return_value = 0.42
                fake.model = MagicMock()

                def _save_model(path):
                    Path(path).write_text("cbm", encoding="utf-8")

                fake.model.save_model.side_effect = _save_model
                MockModel.return_value = fake

                out = m.train_buy_model({"output_dir": tmpdir, "target_label_column": "max_return_pct", "target_threshold_value": 80.0})

            schema_path = Path(out["feature_schema_path"])
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            self.assertEqual(schema["feature_names"], ["buy_pressure", "current_price"])

    def test_train_entry_value_model_saves_model_with_live_risk_target(self):
        import tempfile

        m = _load_module()
        samples = [
            {
                "features": {"current_price": 1.0, "signal": 0.1},
                "label": {"live_risk_adjusted_return_pct": -5.0},
                "meta": {"token_address": "A", "sample_time": 100},
            },
            {
                "features": {"current_price": 1.1, "signal": 0.9},
                "label": {"live_risk_adjusted_return_pct": 35.0},
                "meta": {"token_address": "B", "sample_time": 110},
            },
        ]
        observed = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(m, "EntryValueCatBoostModel") as MockModel:
                fake = MagicMock()

                def _fit(X, y, eval_set=None):
                    observed["columns"] = list(X.columns)
                    observed["targets"] = list(y)
                    return fake

                def _save_model(path):
                    Path(path).write_text("entry-value", encoding="utf-8")

                fake.fit.side_effect = _fit
                fake.model = MagicMock()
                fake.model.save_model.side_effect = _save_model
                MockModel.return_value = fake

                out = m.train_entry_value_model(
                    {"output_dir": tmpdir},
                    {
                        "samples": samples,
                        "feature_names": ["signal", "current_price"],
                        "dropped_features": {},
                    },
                )
                model_path_exists = Path(out["model_path"]).exists()

        self.assertEqual(observed["columns"], ["signal", "current_price"])
        self.assertEqual(observed["targets"], [-5.0, 35.0])
        self.assertTrue(model_path_exists)
        self.assertEqual(out["target_label_column"], "live_risk_adjusted_return_pct")
        self.assertEqual(out["sample_count"], 2)

    def test_prepare_regression_rows_rejects_missing_target_label(self):
        m = _load_module()
        samples = [
            {
                "features": {"current_price": 1.0, "signal": 0.1},
                "label": {"live_risk_adjusted_return_pct": -5.0},
                "meta": {"token_address": "A", "sample_time": 100},
            },
            {
                "features": {"current_price": 1.1, "signal": 0.9},
                "label": {"live_risk_adjusted_return_pct": 35.0},
                "meta": {"token_address": "B", "sample_time": 110},
            },
        ]

        with self.assertRaisesRegex(
            ValueError,
            "missing regression target label column: live_delay_robust_return_pct",
        ):
            m._prepare_regression_rows(samples, "live_delay_robust_return_pct")

    def test_train_buy_model_uses_calibration_samples_for_threshold_selection(self):
        import tempfile

        m = _load_module()
        samples = [
            {"features": {"signal": 0.10, "current_price": 1.0}, "label": {"max_return_pct": 10.0}, "meta": {"token_address": "A", "sample_time": 100}},
            {"features": {"signal": 0.20, "current_price": 1.1}, "label": {"max_return_pct": 120.0}, "meta": {"token_address": "B", "sample_time": 110}},
            {"features": {"signal": 0.30, "current_price": 1.2}, "label": {"max_return_pct": 20.0}, "meta": {"token_address": "C", "sample_time": 120}},
            {"features": {"signal": 0.40, "current_price": 1.3}, "label": {"max_return_pct": 160.0}, "meta": {"token_address": "D", "sample_time": 130}},
            {"features": {"signal": 0.50, "current_price": 1.4}, "label": {"max_return_pct": 15.0}, "meta": {"token_address": "E", "sample_time": 140}},
            {"features": {"signal": 0.60, "current_price": 1.5}, "label": {"max_return_pct": 180.0}, "meta": {"token_address": "F", "sample_time": 150}},
            {"features": {"signal": 0.70, "current_price": 1.6}, "label": {"max_return_pct": 25.0}, "meta": {"token_address": "G", "sample_time": 160}},
            {"features": {"signal": 0.80, "current_price": 1.7}, "label": {"max_return_pct": 220.0}, "meta": {"token_address": "H", "sample_time": 170}},
        ]

        observed = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(m, "_load_samples", return_value=samples), \
                 patch.object(m, "BuyCatBoostModel") as MockModel:
                fake = MagicMock()

                def _fit(X, y, eval_set=None):
                    observed["fit_len"] = len(X)
                    observed["eval_len"] = len(eval_set[0])
                    observed["eval_labels"] = list(eval_set[1])
                    return fake

                def _predict_proba(X):
                    observed["predict_len"] = len(X)
                    return [[0.4, 0.6] for _ in range(len(X))]

                def _save_model(path):
                    Path(path).write_text("cbm", encoding="utf-8")

                fake.fit.side_effect = _fit
                fake.predict_proba.side_effect = _predict_proba
                fake.select_threshold.return_value = 0.64
                fake.model = MagicMock()
                fake.model.save_model.side_effect = _save_model
                MockModel.return_value = fake

                out = m.train_buy_model(
                    {
                        "output_dir": tmpdir,
                        "target_label_column": "max_return_pct",
                        "target_threshold_value": 80.0,
                        "buy_calibration_ratio": 0.5,
                        "min_calibration_samples": 2,
                        "buy_min_calibration_predictions": 1,
                    }
                )

        self.assertLess(observed["fit_len"], len(samples))
        self.assertGreater(observed["eval_len"], 0)
        self.assertEqual(observed["predict_len"], observed["eval_len"])
        self.assertEqual(len(fake.select_threshold.call_args.args[0]), observed["eval_len"])
        self.assertEqual(out["threshold_source"], "calibration")
        self.assertEqual(out["calibration"]["sample_count"], observed["eval_len"])

    def test_train_buy_model_prunes_invalid_and_constant_features_from_schema(self):
        import json
        import tempfile

        m = _load_module()
        samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "future_window": 240,
                    "target_hint": 0.1,
                    "label_debug": 1,
                    "constant_feature": 7.0,
                },
                "label": {"max_return_pct": 20.0},
                "meta": {"token_address": "A", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "future_window": 240,
                    "target_hint": 0.2,
                    "label_debug": 0,
                    "constant_feature": 7.0,
                },
                "label": {"max_return_pct": 120.0},
                "meta": {"token_address": "B", "sample_time": 110},
            },
            {
                "features": {
                    "current_price": 1.4,
                    "future_window": 240,
                    "target_hint": 0.3,
                    "label_debug": 1,
                    "constant_feature": 7.0,
                },
                "label": {"max_return_pct": 10.0},
                "meta": {"token_address": "C", "sample_time": 120},
            },
            {
                "features": {
                    "current_price": 1.6,
                    "future_window": 240,
                    "target_hint": 0.4,
                    "label_debug": 0,
                    "constant_feature": 7.0,
                },
                "label": {"max_return_pct": 200.0},
                "meta": {"token_address": "D", "sample_time": 130},
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(m, "_load_samples", return_value=samples), \
                 patch.object(m, "BuyCatBoostModel") as MockModel:
                fake = MagicMock()
                fake.predict_proba.return_value = [[0.3, 0.7]] * len(samples)
                fake.select_threshold.return_value = 0.42
                fake.model = MagicMock()

                def _save_model(path):
                    Path(path).write_text("cbm", encoding="utf-8")

                fake.model.save_model.side_effect = _save_model
                MockModel.return_value = fake

                out = m.train_buy_model(
                    {
                        "output_dir": tmpdir,
                        "target_label_column": "max_return_pct",
                        "target_threshold_value": 80.0,
                        "buy_calibration_ratio": 0.0,
                    }
                )

            schema_path = Path(out["feature_schema_path"])
            schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertEqual(schema["feature_names"], ["current_price"])
        self.assertEqual(out["feature_names"], ["current_price"])
        self.assertIn("future_window", out["dropped_features"]["invalid"])
        self.assertIn("target_hint", out["dropped_features"]["invalid"])
        self.assertIn("label_debug", out["dropped_features"]["invalid"])
        self.assertIn("constant_feature", out["dropped_features"]["constant"])

    def test_build_sell_env_creates_trading_env_bundle(self):
        m = _load_module()
        buy_artifact = {
            "samples": [
                {
                    "features": {
                        "current_price": 1.0,
                        "launch_fee": 0.5,
                        "buy_pressure": 0.7,
                        "holder_count": 40,
                        "total_buy_volume": 3.0,
                        "total_sell_volume": 1.0,
                    },
                    "meta": {"token_address": "A", "sample_time": 100},
                },
                {
                    "features": {
                        "current_price": 1.1,
                        "launch_fee": 0.5,
                        "buy_pressure": 0.6,
                        "holder_count": 42,
                        "total_buy_volume": 4.0,
                        "total_sell_volume": 2.0,
                    },
                    "meta": {"token_address": "A", "sample_time": 110},
                },
            ]
        }

        bundle = m.build_sell_env({"liquidity_floor": 0.05, "stall_steps": 2}, buy_artifact)

        self.assertIn("env", bundle)
        self.assertGreater(bundle["episode_count"], 0)

    def test_build_sell_env_uses_multi_episode_env_for_ppo(self):
        m = _load_module()
        buy_artifact = {
            "samples": [
                {
                    "features": {
                        "current_price": 1.0,
                        "launch_fee": 0.5,
                        "holder_count": 40,
                        "total_buy_volume": 3.0,
                        "total_sell_volume": 1.0,
                    },
                    "meta": {"token_address": "A", "sample_time": 100},
                },
                {
                    "features": {
                        "current_price": 1.1,
                        "launch_fee": 0.5,
                        "holder_count": 42,
                        "total_buy_volume": 4.0,
                        "total_sell_volume": 2.0,
                    },
                    "meta": {"token_address": "A", "sample_time": 110},
                },
                {
                    "features": {
                        "current_price": 2.0,
                        "launch_fee": 0.6,
                        "holder_count": 45,
                        "total_buy_volume": 5.0,
                        "total_sell_volume": 1.0,
                    },
                    "meta": {"token_address": "B", "sample_time": 120},
                },
                {
                    "features": {
                        "current_price": 2.2,
                        "launch_fee": 0.6,
                        "holder_count": 47,
                        "total_buy_volume": 6.0,
                        "total_sell_volume": 2.0,
                    },
                    "meta": {"token_address": "B", "sample_time": 130},
                },
            ]
        }

        bundle = m.build_sell_env({"liquidity_floor": 0.05, "stall_steps": 2}, buy_artifact)

        self.assertEqual(bundle["episode_count"], 2)
        self.assertEqual(type(bundle["env"]).__name__, "MultiEpisodeTradingEnv")

    def test_build_sell_env_prefers_fit_samples_over_calibration_samples(self):
        m = _load_module()
        buy_artifact = {
            "samples": [
                {
                    "features": {
                        "current_price": 9.0,
                        "launch_fee": 0.5,
                        "holder_count": 10,
                        "total_buy_volume": 1.0,
                        "total_sell_volume": 1.0,
                    },
                    "meta": {"token_address": "CAL", "sample_time": 100},
                },
                {
                    "features": {
                        "current_price": 9.1,
                        "launch_fee": 0.5,
                        "holder_count": 11,
                        "total_buy_volume": 1.0,
                        "total_sell_volume": 1.0,
                    },
                    "meta": {"token_address": "CAL", "sample_time": 110},
                },
            ],
            "sell_training_samples": [
                {
                    "features": {
                        "current_price": 1.0,
                        "launch_fee": 0.5,
                        "holder_count": 20,
                        "total_buy_volume": 2.0,
                        "total_sell_volume": 1.0,
                    },
                    "meta": {"token_address": "FIT", "sample_time": 100},
                },
                {
                    "features": {
                        "current_price": 1.1,
                        "launch_fee": 0.5,
                        "holder_count": 21,
                        "total_buy_volume": 2.0,
                        "total_sell_volume": 1.0,
                    },
                    "meta": {"token_address": "FIT", "sample_time": 110},
                },
            ],
        }

        bundle = m.build_sell_env({"liquidity_floor": 0.05, "stall_steps": 2}, buy_artifact)

        self.assertEqual(bundle["episode_count"], 1)
        self.assertEqual(bundle["episodes"][0][0]["mid_price"], 1.0)

    def test_build_sell_env_passes_execution_and_reward_controls(self):
        m = _load_module()
        buy_artifact = {
            "sell_training_samples": [
                {
                    "features": {
                        "current_price": 1.0,
                        "launch_fee": 0.5,
                        "holder_count": 10,
                        "total_buy_volume": 10.0,
                        "total_sell_volume": 1.0,
                    },
                    "meta": {"token_address": "FIT", "sample_time": 100},
                },
                {
                    "features": {
                        "current_price": 1.1,
                        "launch_fee": 0.5,
                        "holder_count": 11,
                        "total_buy_volume": 10.0,
                        "total_sell_volume": 1.0,
                    },
                    "meta": {"token_address": "FIT", "sample_time": 110},
                },
            ],
        }

        bundle = m.build_sell_env(
            {
                "fee_bps": 100.0,
                "slippage_bps": 200.0,
                "sell_drawdown_penalty_weight": 0.5,
                "sell_hold_penalty_per_step": 0.01,
                "sell_turnover_penalty": 0.02,
                "allow_partial_exits": True,
            },
            buy_artifact,
        )

        env = bundle["env"]
        self.assertAlmostEqual(env.fee_bps, 100.0)
        self.assertAlmostEqual(env.slippage_bps, 200.0)
        self.assertAlmostEqual(env.drawdown_penalty_weight, 0.5)
        self.assertAlmostEqual(env.hold_penalty_per_step, 0.01)
        self.assertAlmostEqual(env.turnover_penalty, 0.02)
        self.assertTrue(env.allow_partial_exits)

    def test_tune_buy_threshold_by_replay_prefers_feasible_drawdown(self):
        m = _load_module()

        class _ScoreBuyModel:
            def predict_proba(self, X):
                return [[1.0 - float(row["score"]), float(row["score"])] for _, row in X.iterrows()]

        samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "score": 0.6,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "LOSE", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 0.4,
                    "score": 0.6,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "LOSE", "sample_time": 110},
            },
            {
                "features": {
                    "current_price": 1.0,
                    "score": 0.9,
                    "launch_fee": 0.5,
                    "holder_count": 20,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "WIN", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.5,
                    "score": 0.9,
                    "launch_fee": 0.5,
                    "holder_count": 21,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "WIN", "sample_time": 110},
            },
        ]

        tuned = m._tune_buy_threshold_by_replay(
            {
                "risk_tune_buy_threshold": True,
                "risk_tune_thresholds": [0.5, 0.8],
                "risk_tune_min_trades": 1,
                "risk_tune_max_drawdown_pct": -30.0,
                "position_fraction": 1.0,
            },
            {
                "model": _ScoreBuyModel(),
                "threshold": 0.5,
                "calibration_samples": samples,
            },
            {"model": None},
        )

        self.assertGreaterEqual(tuned["threshold"], 0.8)
        self.assertEqual(tuned["replay"]["total_trades"], 1)
        self.assertGreaterEqual(tuned["replay"]["max_drawdown_pct"], -30.0)

    def test_tune_buy_threshold_by_replay_rejects_excessive_turnover(self):
        m = _load_module()

        class _ScoreBuyModel:
            def predict_proba(self, X):
                return [[1.0 - float(row["score"]), float(row["score"])] for _, row in X.iterrows()]

        samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "score": 0.6,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "LOW", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "score": 0.6,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "LOW", "sample_time": 110},
            },
            {
                "features": {
                    "current_price": 1.0,
                    "score": 0.9,
                    "launch_fee": 0.5,
                    "holder_count": 20,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "HIGH", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.1,
                    "score": 0.9,
                    "launch_fee": 0.5,
                    "holder_count": 21,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "HIGH", "sample_time": 110},
            },
        ]

        tuned = m._tune_buy_threshold_by_replay(
            {
                "risk_tune_buy_threshold": True,
                "risk_tune_thresholds": [0.5, 0.8],
                "risk_tune_min_trades": 1,
                "risk_tune_max_trades": 1,
                "risk_tune_max_drawdown_pct": -30.0,
                "risk_tune_min_win_rate": 0.0,
                "position_fraction": 1.0,
                "one_entry_per_token": False,
            },
            {
                "model": _ScoreBuyModel(),
                "threshold": 0.5,
                "calibration_samples": samples,
            },
            {"model": None},
        )

        self.assertGreaterEqual(tuned["threshold"], 0.8)
        self.assertEqual(tuned["replay"]["total_trades"], 1)
        low_candidate = next(candidate for candidate in tuned["candidates"] if candidate["threshold"] == 0.5)
        self.assertFalse(low_candidate["feasible"])
        self.assertEqual(tuned["constraints"]["max_trades"], 1)

    def test_tune_buy_threshold_by_replay_falls_back_to_best_trading_candidate_when_constraints_are_infeasible(self):
        m = _load_module()

        class _ScoreBuyModel:
            def predict_proba(self, X):
                return [[1.0 - float(row["score"]), float(row["score"])] for _, row in X.iterrows()]

        samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "score": 0.9,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "LOSE", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 0.4,
                    "score": 0.9,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "LOSE", "sample_time": 110},
            },
        ]

        tuned = m._tune_buy_threshold_by_replay(
            {
                "risk_tune_buy_threshold": True,
                "risk_tune_thresholds": [0.5, 0.8],
                "risk_tune_min_trades": 1,
                "risk_tune_max_drawdown_pct": -100.0,
                "risk_tune_min_win_rate": 0.5,
                "position_fraction": 1.0,
            },
            {
                "model": _ScoreBuyModel(),
                "threshold": 1.0,
                "calibration_samples": samples,
            },
            {"model": None},
        )

        self.assertEqual(tuned["status"], "fallback_selected")
        self.assertLess(tuned["threshold"], 1.0)
        self.assertEqual(tuned["replay"]["total_trades"], 1)
        self.assertFalse(tuned["feasible"])

    def test_tune_buy_threshold_by_replay_uses_probability_coverage_candidates(self):
        m = _load_module()

        class _ScoreBuyModel:
            def predict_proba(self, X):
                return [[1.0 - float(row["score"]), float(row["score"])] for _, row in X.iterrows()]

        samples = []
        for token, score in [
            ("T1", 0.93),
            ("T2", 0.91),
            ("T3", 0.89),
            ("T4", 0.87),
        ]:
            samples.extend(
                [
                    {
                        "features": {
                            "current_price": 1.0,
                            "score": score,
                            "launch_fee": 0.5,
                            "holder_count": 20,
                            "total_buy_volume": 10.0,
                            "total_sell_volume": 1.0,
                        },
                        "meta": {"token_address": token, "sample_time": 100},
                    },
                    {
                        "features": {
                            "current_price": 1.2,
                            "score": score,
                            "launch_fee": 0.5,
                            "holder_count": 21,
                            "total_buy_volume": 1.0,
                            "total_sell_volume": 9.0,
                        },
                        "meta": {"token_address": token, "sample_time": 110},
                    },
                ]
            )

        tuned = m._tune_buy_threshold_by_replay(
            {
                "risk_tune_buy_threshold": True,
                "risk_tune_min_threshold": 0.0,
                "risk_tune_candidate_entry_rates": [0.75],
                "risk_tune_min_trades": 3,
                "risk_tune_max_trades": 3,
                "risk_tune_max_drawdown_pct": -50.0,
                "risk_tune_min_win_rate": 0.0,
                "position_fraction": 1.0,
            },
            {
                "model": _ScoreBuyModel(),
                "threshold": 1.0,
                "calibration_samples": samples,
            },
            {"model": None},
        )

        self.assertEqual(tuned["status"], "selected")
        self.assertEqual(tuned["replay"]["entry_count"], 3)
        self.assertEqual(tuned["replay"]["total_trades"], 3)
        self.assertGreater(tuned["threshold"], 0.87)
        self.assertLessEqual(tuned["threshold"], 0.89)

    def test_tune_buy_threshold_by_replay_uses_live_replay_controls(self):
        m = _load_module()

        class _ScoreBuyModel:
            def predict_proba(self, X):
                return [[1.0 - float(row["score"]), float(row["score"])] for _, row in X.iterrows()]

        samples = []
        for token in ("A", "B"):
            samples.extend(
                [
                    {
                        "features": {
                            "current_price": 1.0,
                            "score": 0.9,
                            "launch_fee": 0.5,
                            "holder_count": 20,
                            "total_buy_volume": 10.0,
                            "total_sell_volume": 1.0,
                        },
                        "meta": {"token_address": token, "sample_time": 100},
                    },
                    {
                        "features": {
                            "current_price": 1.2,
                            "score": 0.9,
                            "launch_fee": 0.5,
                            "holder_count": 21,
                            "total_buy_volume": 1.0,
                            "total_sell_volume": 9.0,
                        },
                        "meta": {"token_address": token, "sample_time": 110},
                    },
                ]
            )

        tuned = m._tune_buy_threshold_by_replay(
            {
                "risk_tune_buy_threshold": True,
                "risk_tune_thresholds": [0.5],
                "risk_tune_min_trades": 1,
                "risk_tune_max_trades": 1,
                "risk_tune_max_drawdown_pct": -100.0,
                "risk_tune_min_win_rate": 0.0,
                "position_fraction": 1.0,
                "entry_delay_seconds": 3,
                "exit_delay_seconds": 3,
                "max_open_positions": 1,
                "buy_primary_score_rescue_min_prob": 0.985,
                "buy_primary_score_rescue_min_pred_return": 25.0,
                "buy_primary_score_rescue_min_entry_volume_30s": 3.0,
                "buy_primary_score_rescue_min_entry_price_volatility": 0.30,
                "buy_primary_score_rescue_min_age_seconds": 0.0,
            },
            {
                "model": _ScoreBuyModel(),
                "threshold": 0.5,
                "calibration_samples": samples,
            },
            {"model": None},
        )

        self.assertEqual(tuned["status"], "selected")
        self.assertEqual(tuned["replay"]["total_trades"], 1)
        self.assertEqual(tuned["replay"]["entry_delay_seconds"], 3)
        self.assertEqual(tuned["replay"]["exit_delay_seconds"], 3)
        self.assertEqual(tuned["replay"]["max_open_positions"], 1)
        self.assertEqual(tuned["constraints"]["entry_delay_seconds"], 3)
        self.assertEqual(tuned["constraints"]["exit_delay_seconds"], 3)
        self.assertEqual(tuned["constraints"]["max_open_positions"], 1)
        self.assertEqual(tuned["constraints"]["buy_primary_score_rescue_min_prob"], 0.985)
        self.assertEqual(tuned["constraints"]["buy_primary_score_rescue_min_pred_return"], 25.0)
        self.assertEqual(tuned["constraints"]["buy_primary_score_rescue_min_entry_volume_30s"], 3.0)
        self.assertEqual(tuned["constraints"]["buy_primary_score_rescue_min_entry_price_volatility"], 0.30)
        self.assertEqual(tuned["constraints"]["buy_primary_score_rescue_min_age_seconds"], 0.0)

    def test_risk_tune_replay_score_prefers_target_entry_rate(self):
        m = _load_module()
        config = {
            "risk_tune_turnover_penalty": 0.0,
            "risk_tune_target_entry_rate": 0.5,
            "risk_tune_entry_rate_penalty": 1.0,
        }
        base = {
            "total_trades": 5,
            "net_return_pct": 10.0,
            "max_drawdown_pct": -5.0,
            "episode_count": 10,
        }

        on_target = dict(base, entry_count=5, entry_rate=0.5)
        under_target = dict(base, entry_count=1, entry_rate=0.1)

        self.assertGreater(
            m._risk_tune_replay_score(config, on_target),
            m._risk_tune_replay_score(config, under_target),
        )

    def test_risk_tune_score_prefers_higher_bnb_profit_with_acceptable_drawdown(self):
        m = _load_module()
        high_return = {
            "net_return_pct": 0.0,
            "net_profit_bnb": 1.0,
            "account_multiple": 1.0,
            "max_drawdown_pct": -25.0,
            "entry_rate": 0.2,
        }
        low_return = {
            "net_return_pct": 1000.0,
            "net_profit_bnb": 0.2,
            "account_multiple": 11.0,
            "max_drawdown_pct": -5.0,
            "entry_rate": 0.2,
        }

        high_score = m._risk_tune_replay_score(
            {
                "risk_tune_preferred_max_drawdown_pct": -30.0,
                "risk_tune_excess_drawdown_penalty": 4.0,
                "risk_tune_drawdown_penalty": 0.0,
            },
            high_return,
        )
        low_score = m._risk_tune_replay_score(
            {
                "risk_tune_preferred_max_drawdown_pct": -30.0,
                "risk_tune_excess_drawdown_penalty": 4.0,
                "risk_tune_drawdown_penalty": 0.0,
            },
            low_return,
        )

        self.assertGreater(high_score, low_score)

    def test_risk_tune_score_penalizes_drawdown_beyond_preferred_band(self):
        m = _load_module()
        acceptable = {
            "net_profit_bnb": 1.0,
            "account_multiple": 2.0,
            "max_drawdown_pct": -30.0,
            "entry_rate": 0.2,
        }
        excessive = {
            "net_profit_bnb": 1.0,
            "account_multiple": 2.0,
            "max_drawdown_pct": -45.0,
            "entry_rate": 0.2,
        }

        acceptable_score = m._risk_tune_replay_score(
            {
                "risk_tune_preferred_max_drawdown_pct": -30.0,
                "risk_tune_excess_drawdown_penalty": 4.0,
                "risk_tune_drawdown_penalty": 0.0,
            },
            acceptable,
        )
        excessive_score = m._risk_tune_replay_score(
            {
                "risk_tune_preferred_max_drawdown_pct": -30.0,
                "risk_tune_excess_drawdown_penalty": 4.0,
                "risk_tune_drawdown_penalty": 0.0,
            },
            excessive,
        )

        self.assertLess(excessive_score, acceptable_score)

    def test_risk_tune_turnover_penalty_uses_entry_rate_not_absolute_count(self):
        m = _load_module()
        config = {
            "risk_tune_turnover_penalty": 1.0,
            "risk_tune_target_entry_rate": None,
            "risk_tune_entry_rate_penalty": 0.0,
        }
        small_calibration = {
            "total_trades": 10,
            "entry_count": 10,
            "entry_rate": 1.0,
            "episode_count": 10,
            "net_return_pct": 100.0,
            "max_drawdown_pct": -5.0,
        }
        large_calibration = {
            "total_trades": 1000,
            "entry_count": 1000,
            "entry_rate": 1.0,
            "episode_count": 1000,
            "net_return_pct": 100.0,
            "max_drawdown_pct": -5.0,
        }

        self.assertAlmostEqual(
            m._risk_tune_replay_score(config, small_calibration),
            m._risk_tune_replay_score(config, large_calibration),
        )

    def test_tune_buy_threshold_by_replay_rejects_entry_rate_above_max(self):
        m = _load_module()

        class _ScoreBuyModel:
            def predict_proba(self, X):
                return [[1.0 - float(row["score"]), float(row["score"])] for _, row in X.iterrows()]

        samples = []
        for token, score in [("LOW", 0.6), ("HIGH", 0.9)]:
            samples.extend(
                [
                    {
                        "features": {
                            "current_price": 1.0,
                            "score": score,
                            "launch_fee": 0.5,
                            "holder_count": 10,
                            "total_buy_volume": 10.0,
                            "total_sell_volume": 1.0,
                        },
                        "meta": {"token_address": token, "sample_time": 100},
                    },
                    {
                        "features": {
                            "current_price": 1.1,
                            "score": score,
                            "launch_fee": 0.5,
                            "holder_count": 11,
                            "total_buy_volume": 1.0,
                            "total_sell_volume": 9.0,
                        },
                        "meta": {"token_address": token, "sample_time": 110},
                    },
                ]
            )

        tuned = m._tune_buy_threshold_by_replay(
            {
                "risk_tune_buy_threshold": True,
                "risk_tune_thresholds": [0.5, 0.8],
                "risk_tune_min_trades": 1,
                "risk_tune_max_drawdown_pct": -50.0,
                "risk_tune_min_win_rate": 0.0,
                "risk_tune_max_entry_rate": 0.5,
                "position_fraction": 1.0,
            },
            {
                "model": _ScoreBuyModel(),
                "threshold": 0.5,
                "calibration_samples": samples,
            },
            {"model": None},
        )

        self.assertEqual(tuned["status"], "selected")
        self.assertGreater(tuned["threshold"], 0.5)
        self.assertLessEqual(tuned["replay"]["entry_rate"], 0.5)
        low_candidate = next(candidate for candidate in tuned["candidates"] if candidate["threshold"] == 0.5)
        self.assertFalse(low_candidate["feasible"])
        self.assertEqual(tuned["constraints"]["max_entry_rate"], 0.5)

    def test_tune_buy_threshold_by_replay_rejects_entry_rate_below_min(self):
        m = _load_module()

        class _ScoreBuyModel:
            def predict_proba(self, X):
                return [[1.0 - float(row["score"]), float(row["score"])] for _, row in X.iterrows()]

        samples = []
        for token, score in [("LOW", 0.6), ("HIGH", 0.9)]:
            samples.extend(
                [
                    {
                        "features": {
                            "current_price": 1.0,
                            "score": score,
                            "launch_fee": 0.5,
                            "holder_count": 10,
                            "total_buy_volume": 10.0,
                            "total_sell_volume": 1.0,
                        },
                        "meta": {"token_address": token, "sample_time": 100},
                    },
                    {
                        "features": {
                            "current_price": 1.1,
                            "score": score,
                            "launch_fee": 0.5,
                            "holder_count": 11,
                            "total_buy_volume": 1.0,
                            "total_sell_volume": 9.0,
                        },
                        "meta": {"token_address": token, "sample_time": 110},
                    },
                ]
            )

        tuned = m._tune_buy_threshold_by_replay(
            {
                "risk_tune_buy_threshold": True,
                "risk_tune_thresholds": [0.5, 0.8],
                "risk_tune_min_trades": 1,
                "risk_tune_max_drawdown_pct": -50.0,
                "risk_tune_min_win_rate": 0.0,
                "risk_tune_min_entry_rate": 0.75,
                "position_fraction": 1.0,
            },
            {
                "model": _ScoreBuyModel(),
                "threshold": 0.5,
                "calibration_samples": samples,
            },
            {"model": None},
        )

        self.assertEqual(tuned["status"], "selected")
        self.assertEqual(tuned["threshold"], 0.5)
        high_candidate = next(candidate for candidate in tuned["candidates"] if candidate["threshold"] == 0.8)
        self.assertFalse(high_candidate["feasible"])
        self.assertLess(high_candidate["replay"]["entry_rate"], 0.75)
        self.assertEqual(tuned["constraints"]["min_entry_rate"], 0.75)

    def test_risk_tune_reuses_buy_probabilities_across_threshold_candidates(self):
        m = _load_module()

        class _CountingBuyModel:
            def __init__(self):
                self.calls = 0

            def predict_proba(self, X):
                self.calls += 1
                return [[1.0 - float(row["score"]), float(row["score"])] for _, row in X.iterrows()]

        buy_model = _CountingBuyModel()
        samples = [
            {
                "features": {"current_price": 1.0, "score": 0.92},
                "meta": {"token_address": "0xreuse", "sample_time": 100},
            },
            {
                "features": {"current_price": 1.1, "score": 0.10},
                "meta": {"token_address": "0xreuse", "sample_time": 110},
            },
        ]

        tuned = m._tune_buy_threshold_by_replay(
            {
                "risk_tune_buy_threshold": True,
                "risk_tune_thresholds": [0.5, 0.7, 0.9],
                "risk_tune_min_threshold": 0.0,
                "risk_tune_min_trades": 1,
                "risk_tune_max_drawdown_pct": -100.0,
                "risk_tune_min_win_rate": 0.0,
                "position_fraction": 1.0,
            },
            {"model": buy_model, "threshold": 1.0, "calibration_samples": samples},
            {"model": None},
        )

        self.assertEqual(tuned["status"], "selected")
        self.assertEqual(buy_model.calls, 1)

    def test_risk_tune_threshold_candidates_do_not_include_raw_probability_grid_by_default(self):
        m = _load_module()

        probability_values = [index / 1000.0 for index in range(1000)]

        candidates = m._risk_tune_threshold_candidates(
            {
                "risk_tune_min_threshold": 0.0,
                "risk_tune_candidate_entry_rates": [0.10, 0.25, 0.50],
                "risk_tune_target_entry_rate": 0.15,
            },
            0.5,
            probability_values,
        )

        self.assertLessEqual(len(candidates), 20)

    def test_run_bc_warmstart_saves_weights(self):
        import tempfile
        m = _load_module()
        fake_model = MagicMock()
        env_bundle = {"env": object()}
        bc_artifact = {"weights": "dummy-bc.pt"}

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(m, "_torch_load", return_value={"k": 1.0}), \
                 patch.object(m, "train_ppo", return_value=fake_model) as mock_train:
                out = m.run_ppo_finetune({"output_dir": tmpdir, "total_timesteps": 64, "ppo_seed": 9}, env_bundle, bc_artifact)

        mock_train.assert_called_once()
        fake_model.save.assert_called_once()
        self.assertTrue(out["policy_path"].endswith("sell_policy.zip"))
        self.assertIs(out["model"], fake_model)

    def test_run_ab_evaluation_uses_eval_sample_features_for_buy_inference(self):
        m = _load_module()

        class _FakeBuyModel:
            def __init__(self):
                self.frames = []

            def predict_proba(self, X):
                self.frames.append(X.copy())
                return [[0.9, 0.1] for _ in range(len(X))]

        buy_model = _FakeBuyModel()
        buy_artifact = {
            "model": buy_model,
            "threshold": 0.5,
        }
        ppo_artifact = {"total_timesteps": 0}
        bc_artifact = {"bc_samples": 0}

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 123.0,
                    "total_sell_volume": 45.0,
                },
                "meta": {"token_address": "0x1", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0x1", "sample_time": 110},
            },
        ]

        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples},
            buy_artifact,
            ppo_artifact,
            bc_artifact,
        )

        self.assertEqual(out["sell_episode_count"], 1)
        self.assertGreaterEqual(len(buy_model.frames), 1)
        first_buy_input = buy_model.frames[0].iloc[0].to_dict()
        self.assertEqual(first_buy_input["total_buy_volume"], 123.0)
        self.assertEqual(first_buy_input["total_sell_volume"], 45.0)

    def test_run_ab_evaluation_batches_buy_inference_per_episode_and_skips_last_sample(self):
        m = _load_module()

        class _FakeBuyModel:
            def __init__(self):
                self.frames = []

            def predict_proba(self, X):
                self.frames.append(X.copy())
                return [[0.9, 0.1] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xbatched", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.1,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 11.0,
                    "total_sell_volume": 2.0,
                },
                "meta": {"token_address": "0xbatched", "sample_time": 110},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "launch_fee": 0.5,
                    "holder_count": 12,
                    "total_buy_volume": 12.0,
                    "total_sell_volume": 3.0,
                },
                "meta": {"token_address": "0xbatched", "sample_time": 120},
            },
        ]

        buy_model = _FakeBuyModel()
        m.run_ab_evaluation(
            {"eval_samples": eval_samples},
            {"model": buy_model, "threshold": 0.5},
            {"total_timesteps": 0},
            {"bc_samples": 0},
        )

        self.assertEqual(len(buy_model.frames), 1)
        self.assertEqual(len(buy_model.frames[0]), 2)
        self.assertEqual(list(buy_model.frames[0]["current_price"]), [1.0, 1.1])

    def test_run_ab_evaluation_reuses_buy_probabilities_for_all_in_replay(self):
        m = _load_module()

        class _FakeBuyModel:
            def __init__(self):
                self.calls = 0

            def predict_proba(self, X):
                self.calls += 1
                return [[0.1, 0.9] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {"current_price": 1.0, "signal": 0.9},
                "meta": {"token_address": "0xreuse-eval", "sample_time": 100},
            },
            {
                "features": {"current_price": 1.2, "signal": 0.8},
                "meta": {"token_address": "0xreuse-eval", "sample_time": 110},
            },
        ]

        buy_model = _FakeBuyModel()
        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples, "position_fraction": 0.1},
            {"model": buy_model, "threshold": 0.5},
            {"total_timesteps": 0},
            {"bc_samples": 0},
        )

        self.assertEqual(buy_model.calls, 1)
        self.assertEqual(out["entry_rate"], out["runtime_replay"]["entry_rate"])

    def test_run_ab_evaluation_can_skip_all_in_replay_for_fast_iteration(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {"current_price": 1.0},
                "meta": {"token_address": "0xfast-eval", "sample_time": 100},
            },
            {
                "features": {"current_price": 1.2},
                "meta": {"token_address": "0xfast-eval", "sample_time": 110},
            },
        ]

        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples, "position_fraction": 0.1, "skip_all_in_replay": True},
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 0},
            {"bc_samples": 0},
        )

        self.assertIn("runtime_replay", out)
        self.assertNotIn("all_in_replay", out)

    def test_run_ab_evaluation_passes_profit_lock_params_to_runtime_replay(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(sample_time, price):
            return {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {
                    "token_address": "0xprofit-lock",
                    "sample_time": sample_time,
                    "create_timestamp": 100,
                },
            }

        out = m.run_ab_evaluation(
            {
                "eval_samples": [
                    sample(100, 1.0),
                    sample(120, 1.26),
                    sample(130, 0.82),
                ],
                "include_trade_log": True,
                "position_fraction": 0.1,
                "stop_loss": -0.18,
                "profit_lock_take_profit_pct": 0.25,
                "profit_lock_max_hold_seconds": 60,
                "skip_all_in_replay": True,
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"model": _SellNonePolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["profit_lock_take_profit_pct"], 0.25)
        self.assertEqual(out["profit_lock_max_hold_seconds"], 60.0)
        self.assertEqual(out["profit_lock_take_profit_count"], 1)
        self.assertEqual(out["runtime_replay"]["profit_lock_take_profit_count"], 1)
        self.assertEqual(out["trade_log"][0]["exit_reason"], "PROFIT_LOCK_TAKE_PROFIT")

    def test_run_ab_evaluation_loads_ppo_policy_from_policy_path(self):
        m = _load_module()

        class _FakeBuyModel:
            def __init__(self):
                self.calls = 0

            def predict_proba(self, X):
                self.calls += 1
                if self.calls == 1:
                    return [[0.1, 0.9] for _ in range(len(X))]
                return [[0.9, 0.1] for _ in range(len(X))]

        class _FakePolicy:
            def __init__(self):
                self.predict_calls = 0

            def predict(self, obs, deterministic=True):
                self.predict_calls += 1
                return 3, None

        fake_policy = _FakePolicy()

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0x1", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0x1", "sample_time": 110},
            },
        ]

        with patch.object(m, "_load_ppo_policy", return_value=fake_policy) as mock_load:
            out = m.run_ab_evaluation(
                {"eval_samples": eval_samples},
                {"model": _FakeBuyModel(), "threshold": 0.5},
                {"policy_path": "sell_policy.zip", "total_timesteps": 128},
                {"bc_samples": 10},
            )

        mock_load.assert_called_once_with("sell_policy.zip")
        self.assertGreater(fake_policy.predict_calls, 0)

    def test_run_ab_evaluation_counts_forced_episode_end_liquidation_as_trade(self):
        m = _load_module()

        class _FakeBuyModel:
            def __init__(self):
                self.calls = 0

            def predict_proba(self, X):
                self.calls += 1
                if self.calls == 1:
                    return [[0.1, 0.9] for _ in range(len(X))]
                return [[0.9, 0.1] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0x2", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.1,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0x2", "sample_time": 110},
            },
        ]

        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples},
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128},
            {"bc_samples": 10},
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["win_rate"], 1.0)

    def test_run_ab_evaluation_partial_sell_preserves_cost_basis(self):
        m = _load_module()

        class _FakeBuyModel:
            def __init__(self):
                self.calls = 0

            def predict_proba(self, X):
                self.calls += 1
                if self.calls == 1:
                    return [[0.1, 0.9] for _ in range(len(X))]
                return [[0.9, 0.1] for _ in range(len(X))]

        class _FakePolicy:
            def __init__(self):
                self.calls = 0

            def predict(self, obs, deterministic=True):
                self.calls += 1
                if self.calls == 1:
                    return 1, None
                return 0, None

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0x3", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0x3", "sample_time": 110},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "launch_fee": 0.5,
                    "holder_count": 12,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0x3", "sample_time": 120},
            },
        ]

        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples, "allow_partial_exits": True},
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128, "model": _FakePolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["total_trades"], 2)
        self.assertEqual(out["win_rate"], 1.0)
        self.assertAlmostEqual(out["net_return_pct"], 2.0, places=6)
        self.assertTrue(out["allow_partial_exits"])

    def test_run_ab_evaluation_canonicalizes_partial_policy_actions_when_partial_exits_disabled(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellHalfPolicy:
            def predict(self, obs, deterministic=True):
                return 2, None

        eval_samples = [
            {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10 + idx,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xsingleexit", "sample_time": 100 + idx},
            }
            for idx, price in enumerate([1.0, 1.1, 1.2, 1.3])
        ]

        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples, "include_trade_log": True},
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128, "model": _SellHalfPolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["entry_count"], 1)
        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["runtime_replay"]["episode_count"], 1)
        self.assertAlmostEqual(out["runtime_replay"]["entry_rate"], 1.0)
        self.assertEqual(len(out["trade_log"]), 1)
        self.assertEqual(out["trade_log"][0]["exit_reason"], "SELL100")
        self.assertEqual(out["trade_log"][0]["requested_size_fraction"], 1.0)
        self.assertEqual(out["trade_log"][0]["size_fraction"], 1.0)
        self.assertFalse(out["allow_partial_exits"])

    def test_run_ab_evaluation_orders_exits_by_sample_time(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        def sample(token, sample_time, price):
            return {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": token, "sample_time": sample_time},
            }

        eval_samples = [
            sample("0xslow", 100, 1.0),
            sample("0xslow", 200, 2.0),
            sample("0xfast", 150, 1.0),
            sample("0xfast", 160, 2.0),
        ]

        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples, "include_trade_log": True, "position_fraction": 0.1},
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128, "model": _SellAllPolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual([trade["exit_time"] for trade in out["trade_log"]], [160, 200])

    def test_run_ab_evaluation_caps_position_stake_fraction(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        eval_samples = []
        for token_index, start_time in enumerate([100, 200]):
            for offset, price in enumerate([1.0, 2.0]):
                eval_samples.append(
                    {
                        "features": {
                            "current_price": price,
                            "launch_fee": 0.5,
                            "holder_count": 10,
                            "total_buy_volume": 10.0,
                            "total_sell_volume": 1.0,
                        },
                        "meta": {
                            "token_address": f"0xcap{token_index}",
                            "sample_time": start_time + offset,
                        },
                    }
                )

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "position_fraction": 1.0,
                "max_position_fraction": 0.1,
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128, "model": _SellAllPolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["total_trades"], 2)
        self.assertAlmostEqual(out["net_return_pct"], 20.0, places=6)
        self.assertEqual(out["max_position_fraction"], 0.1)

    def test_run_ab_evaluation_enforces_stop_loss_before_policy_exit(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _HoldPolicy:
            def __init__(self):
                self.calls = 0

            def predict(self, obs, deterministic=True):
                self.calls += 1
                return 0, None

        policy = _HoldPolicy()
        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xstop", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 0.4,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0xstop", "sample_time": 110},
            },
            {
                "features": {
                    "current_price": 2.0,
                    "launch_fee": 0.5,
                    "holder_count": 12,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xstop", "sample_time": 120},
            },
        ]

        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples, "stop_loss": -0.5},
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128, "model": policy},
            {"bc_samples": 10},
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["win_rate"], 0.0)
        self.assertAlmostEqual(out["net_return_pct"], -6.0, places=6)
        self.assertEqual(policy.calls, 0)

    def test_run_ab_evaluation_position_fraction_reduces_runtime_drawdown(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xsize", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 0.4,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0xsize", "sample_time": 110},
            },
        ]

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "stop_loss": -0.5,
                "position_fraction": 0.1,
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128},
            {"bc_samples": 10},
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertAlmostEqual(out["net_return_pct"], -6.0, places=6)
        self.assertAlmostEqual(out["max_drawdown_pct"], -6.0, places=6)
        self.assertAlmostEqual(out["all_in_replay"]["net_return_pct"], -60.0, places=6)
        self.assertAlmostEqual(out["runtime_replay"]["net_return_pct"], -6.0, places=6)

    def test_run_eval_replay_fixed_stake_does_not_compound_position_size(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        episodes = [
            [
                {"features": {"current_price": 1.0, "holder_count": 10}, "meta": {"token_address": "0xa", "sample_time": 100}},
                {"features": {"current_price": 2.0, "holder_count": 11}, "meta": {"token_address": "0xa", "sample_time": 110}},
            ],
            [
                {"features": {"current_price": 1.0, "holder_count": 10}, "meta": {"token_address": "0xb", "sample_time": 200}},
                {"features": {"current_price": 2.0, "holder_count": 11}, "meta": {"token_address": "0xb", "sample_time": 210}},
            ],
        ]

        out = m._run_eval_replay(
            episodes,
            _FakeBuyModel(),
            0.5,
            _SellAllPolicy(),
            position_fraction=1.0,
            fixed_stake_bnb=0.1,
            initial_equity_bnb=1.0,
            include_trade_log=True,
        )

        self.assertEqual(out["stake_mode"], "fixed_bnb")
        self.assertAlmostEqual(out["fixed_stake_bnb"], 0.1)
        self.assertAlmostEqual(out["initial_equity_bnb"], 1.0)
        self.assertAlmostEqual(out["net_profit_bnb"], 0.2)
        self.assertAlmostEqual(out["final_equity_bnb"], 1.2)
        self.assertAlmostEqual(out["account_multiple"], 1.2)
        self.assertEqual(len(out["trade_log"]), 2)
        self.assertTrue(all(row["stake_bnb"] == 0.1 for row in out["trade_log"]))

    def test_run_eval_replay_fixed_stake_requires_free_cash(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        episodes = [
            [
                {"features": {"current_price": 1.0, "holder_count": 10}, "meta": {"token_address": "0xa", "sample_time": 100}},
                {"features": {"current_price": 1.0, "holder_count": 11}, "meta": {"token_address": "0xa", "sample_time": 200}},
            ],
            [
                {"features": {"current_price": 1.0, "holder_count": 10}, "meta": {"token_address": "0xb", "sample_time": 101}},
                {"features": {"current_price": 1.0, "holder_count": 11}, "meta": {"token_address": "0xb", "sample_time": 201}},
            ],
        ]

        out = m._run_eval_replay(
            episodes,
            _FakeBuyModel(),
            0.5,
            None,
            fixed_stake_bnb=0.6,
            initial_equity_bnb=1.0,
            max_open_positions=8,
        )

        self.assertEqual(out["entry_count"], 1)
        self.assertAlmostEqual(out["fixed_stake_bnb"], 0.6)

    def test_run_eval_replay_fixed_execution_costs_reduce_realized_profit(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        episodes = [
            [
                {"features": {"current_price": 1.0, "holder_count": 10}, "meta": {"token_address": "0xgas", "sample_time": 100}},
                {"features": {"current_price": 2.0, "holder_count": 11}, "meta": {"token_address": "0xgas", "sample_time": 110}},
            ],
        ]

        out = m._run_eval_replay(
            episodes,
            _FakeBuyModel(),
            0.5,
            _SellAllPolicy(),
            fixed_stake_bnb=0.1,
            initial_equity_bnb=1.0,
            entry_fixed_cost_bnb=0.01,
            exit_fixed_cost_bnb=0.02,
            include_trade_log=True,
        )

        self.assertAlmostEqual(out["entry_fixed_cost_bnb"], 0.01)
        self.assertAlmostEqual(out["exit_fixed_cost_bnb"], 0.02)
        self.assertAlmostEqual(out["net_profit_bnb"], 0.07)
        self.assertAlmostEqual(out["final_equity_bnb"], 1.07)
        self.assertAlmostEqual(out["trade_log"][0]["entry_fixed_cost_bnb"], 0.01)
        self.assertAlmostEqual(out["trade_log"][0]["exit_fixed_cost_bnb"], 0.02)
        self.assertAlmostEqual(out["trade_log"][0]["return_pct"], (0.18 - 0.11) / 0.11 * 100.0)

    def test_run_eval_replay_fixed_stake_requires_cash_for_entry_cost(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        episodes = [
            [
                {"features": {"current_price": 1.0, "holder_count": 10}, "meta": {"token_address": "0xgas", "sample_time": 100}},
                {"features": {"current_price": 1.0, "holder_count": 11}, "meta": {"token_address": "0xgas", "sample_time": 110}},
            ],
        ]

        out = m._run_eval_replay(
            episodes,
            _FakeBuyModel(),
            0.5,
            None,
            fixed_stake_bnb=0.1,
            initial_equity_bnb=0.11,
            entry_fixed_cost_bnb=0.02,
        )

        self.assertEqual(out["entry_count"], 0)
        self.assertAlmostEqual(out["entry_fixed_cost_bnb"], 0.02)

    def test_run_ab_evaluation_reports_drawdown_limit_and_profit_concentration(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        eval_samples = [
            {"features": {"current_price": 1.0, "holder_count": 10}, "meta": {"token_address": "0xa", "sample_time": 100}},
            {"features": {"current_price": 2.0, "holder_count": 11}, "meta": {"token_address": "0xa", "sample_time": 110}},
            {"features": {"current_price": 1.0, "holder_count": 10}, "meta": {"token_address": "0xb", "sample_time": 200}},
            {"features": {"current_price": 0.9, "holder_count": 11}, "meta": {"token_address": "0xb", "sample_time": 210}},
        ]

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "fixed_stake_bnb": 0.1,
                "initial_equity_bnb": 1.0,
                "preferred_max_drawdown_pct": -30.0,
                "include_trade_log": True,
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"model": _SellAllPolicy(), "total_timesteps": 128},
            {"bc_samples": 10},
        )

        self.assertIn("drawdown_within_preferred_limit", out)
        self.assertTrue(out["drawdown_within_preferred_limit"])
        self.assertIn("top_trade_profit_concentration", out)
        self.assertIn("top_1_profit_share", out["top_trade_profit_concentration"])

    def test_run_ab_evaluation_applies_entry_and_exit_costs(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xcost", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 2.0,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xcost", "sample_time": 110},
            },
        ]

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "fee_bps": 100.0,
                "slippage_bps": 100.0,
                "include_trade_log": True,
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128, "model": _SellAllPolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertLess(out["net_return_pct"], 100.0)
        self.assertAlmostEqual(out["fee_bps"], 100.0)
        self.assertAlmostEqual(out["slippage_bps"], 100.0)
        self.assertLess(out["trade_log"][0]["return_pct"], 100.0)

    def test_run_ab_evaluation_entry_delay_uses_later_fill_price(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        def sample(sample_time, price, buy_volume=10.0, sell_volume=1.0):
            return {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": buy_volume,
                    "total_sell_volume": sell_volume,
                },
                "meta": {"token_address": "0xdelayentry", "sample_time": sample_time},
            }

        eval_samples = [
            sample(100, 1.0),
            sample(103, 2.0),
            sample(110, 3.0, buy_volume=1.0, sell_volume=9.0),
        ]

        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples, "include_trade_log": True, "entry_delay_seconds": 2},
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"model": _SellAllPolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["trade_log"][0]["entry_time"], 103)
        self.assertEqual(out["trade_log"][0]["entry_index"], 1)
        self.assertAlmostEqual(out["trade_log"][0]["entry_price"], 2.0)
        self.assertAlmostEqual(out["trade_log"][0]["return_pct"], 50.0)
        self.assertEqual(out["runtime_replay"]["entry_delay_seconds"], 2)

    def test_run_ab_evaluation_exit_delay_uses_later_fill_price(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        def sample(sample_time, price):
            return {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xdelayexit", "sample_time": sample_time},
            }

        eval_samples = [sample(100, 1.0), sample(110, 2.0), sample(115, 1.0)]

        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples, "include_trade_log": True, "exit_delay_seconds": 3},
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"model": _SellAllPolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["trade_log"][0]["exit_time"], 115)
        self.assertAlmostEqual(out["trade_log"][0]["exit_price"], 1.0)
        self.assertAlmostEqual(out["trade_log"][0]["return_pct"], 0.0)
        self.assertEqual(out["runtime_replay"]["exit_delay_seconds"], 3)

    def test_run_ab_evaluation_max_open_positions_limits_concurrent_entries(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        def sample(token, sample_time, price):
            return {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": token, "sample_time": sample_time},
            }

        eval_samples = [
            sample("0xcapone", 100, 1.0),
            sample("0xcaptwo", 100, 1.0),
            sample("0xcapone", 110, 2.0),
            sample("0xcaptwo", 110, 2.0),
        ]

        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples, "include_trade_log": True, "max_open_positions": 1},
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"model": _SellAllPolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["entry_count"], 1)
        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["runtime_replay"]["max_open_positions"], 1)
        self.assertEqual(out["entry_signal_count"], 2)
        self.assertEqual(out["entry_attempt_count"], 1)
        self.assertEqual(out["entry_blocked_count"], 1)
        self.assertEqual(out["entry_fill_rate"], 1.0)

    def test_run_eval_replay_max_open_positions_counts_pending_entries(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        episodes = [
            [
                {"features": {"current_price": 1.0, "holder_count": 10}, "meta": {"token_address": "0xa", "sample_time": 100}},
                {"features": {"current_price": 1.0, "holder_count": 11}, "meta": {"token_address": "0xa", "sample_time": 105}},
                {"features": {"current_price": 2.0, "holder_count": 12}, "meta": {"token_address": "0xa", "sample_time": 106}},
            ],
            [
                {"features": {"current_price": 1.0, "holder_count": 10}, "meta": {"token_address": "0xb", "sample_time": 101}},
                {"features": {"current_price": 1.0, "holder_count": 11}, "meta": {"token_address": "0xb", "sample_time": 110}},
                {"features": {"current_price": 2.0, "holder_count": 12}, "meta": {"token_address": "0xb", "sample_time": 111}},
            ],
        ]

        out = m._run_eval_replay(
            episodes,
            _FakeBuyModel(),
            0.5,
            _SellAllPolicy(),
            entry_delay_seconds=5,
            max_open_positions=1,
        )

        self.assertEqual(out["entry_count"], 1)
        self.assertEqual(out["total_trades"], 1)

    def test_run_eval_replay_entry_execution_failure_blocks_delayed_fill(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        episodes = [
            [
                {
                    "features": {"current_price": 1.0, "holder_count": 10},
                    "meta": {"token_address": "0xfailentry", "sample_time": 100},
                    "label": {
                        "live_entry_available": 1,
                        "live_entry_time": 103,
                        "live_entry_price": 1.1,
                    },
                },
                {
                    "features": {"current_price": 2.0, "holder_count": 11},
                    "meta": {"token_address": "0xfailentry", "sample_time": 110},
                },
            ],
        ]

        out = m._run_eval_replay(
            episodes,
            _FakeBuyModel(),
            0.5,
            _SellAllPolicy(),
            entry_delay_seconds=3,
            entry_execution_failure_rate=1.0,
        )

        self.assertEqual(out["entry_signal_count"], 1)
        self.assertEqual(out["entry_attempt_count"], 1)
        self.assertEqual(out["entry_execution_failure_count"], 1)
        self.assertEqual(out["entry_count"], 0)
        self.assertEqual(out["total_trades"], 0)

    def test_run_eval_replay_max_pending_entries_limits_delayed_buys(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        def sample(token, sample_time, price):
            return {
                "features": {"current_price": price, "holder_count": 10},
                "meta": {"token_address": token, "sample_time": sample_time},
            }

        episodes = [
            [
                sample("0xpendinga", 100, 1.0),
                sample("0xpendinga", 105, 1.2),
                sample("0xpendinga", 110, 2.0),
            ],
            [
                sample("0xpendingb", 100, 1.0),
                sample("0xpendingb", 110, 2.0),
            ],
        ]

        out = m._run_eval_replay(
            episodes,
            _FakeBuyModel(),
            0.5,
            _SellAllPolicy(),
            entry_delay_seconds=3,
            max_open_positions=1000,
            max_pending_entries=1,
        )

        self.assertEqual(out["entry_signal_count"], 2)
        self.assertEqual(out["entry_blocked_count"], 1)
        self.assertEqual(out["entry_count"], 1)
        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["max_pending_entries"], 1)

    def test_run_eval_replay_buy_prob_ranking_prefers_higher_score_when_slots_are_limited(self):
        m = _load_module()

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(token, sample_time, price):
            return {
                "features": {"current_price": price, "holder_count": 10},
                "meta": {"token_address": token, "sample_time": sample_time},
            }

        episodes = [
            [sample("0xa", 100, 1.0), sample("0xa", 103, 1.1)],
            [sample("0xb", 100, 1.0), sample("0xb", 103, 1.1)],
            [sample("0xc", 100, 1.0), sample("0xc", 103, 1.1)],
        ]

        out = m._run_eval_replay(
            episodes,
            None,
            0.5,
            _SellNonePolicy(),
            buy_probabilities_by_episode=[{0: 0.55}, {0: 0.9}, {0: 0.8}],
            entry_delay_seconds=3,
            max_open_positions=1,
            entry_ranking_mode="buy_prob",
            include_trade_log=True,
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["entry_count"], 1)
        self.assertEqual(out["trade_log"][0]["token"], "0xb")

    def test_run_eval_replay_entry_value_ranking_prefers_higher_value_when_slots_are_limited(self):
        m = _load_module()

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(token, sample_time, price):
            return {
                "features": {"current_price": price, "holder_count": 10},
                "meta": {"token_address": token, "sample_time": sample_time},
            }

        episodes = [
            [sample("0xa", 100, 1.0), sample("0xa", 103, 1.1)],
            [sample("0xb", 100, 1.0), sample("0xb", 103, 1.1)],
            [sample("0xc", 100, 1.0), sample("0xc", 103, 1.1)],
        ]

        out = m._run_eval_replay(
            episodes,
            None,
            0.5,
            _SellNonePolicy(),
            buy_probabilities_by_episode=[{0: 0.9}, {0: 0.9}, {0: 0.9}],
            entry_scores_by_episode=[{0: 1.0}, {0: 20.0}, {0: 5.0}],
            entry_delay_seconds=3,
            max_open_positions=1,
            entry_ranking_mode="entry_value",
            include_trade_log=True,
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["trade_log"][0]["token"], "0xb")
        self.assertEqual(out["trade_log"][0]["entry_score"], 20.0)

    def test_run_eval_replay_min_entry_score_filters_low_value_signals(self):
        m = _load_module()

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(token, sample_time, price):
            return {
                "features": {"current_price": price, "holder_count": 10},
                "meta": {"token_address": token, "sample_time": sample_time},
            }

        episodes = [
            [sample("0xa", 100, 1.0), sample("0xa", 103, 1.1)],
            [sample("0xb", 100, 1.0), sample("0xb", 103, 1.1)],
        ]

        out = m._run_eval_replay(
            episodes,
            None,
            0.5,
            _SellNonePolicy(),
            buy_probabilities_by_episode=[{0: 0.9}, {0: 0.9}],
            entry_scores_by_episode=[{0: 5.0}, {0: 20.0}],
            min_entry_score=10.0,
            position_fraction=0.1,
            include_trade_log=True,
        )

        self.assertEqual(out["entry_signal_count"], 2)
        self.assertEqual(out["entry_score_reject_count"], 1)
        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["trade_log"][0]["token"], "0xb")
        self.assertEqual(out["trade_log"][0]["entry_score"], 20.0)

    def test_run_eval_replay_accepts_qualified_near_threshold_rescue(self):
        m = _load_module()

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(token, sample_time, price, volume_30s, price_volatility):
            return {
                "features": {
                    "current_price": price,
                    "holder_count": 10,
                    "volume_30s": volume_30s,
                    "price_volatility": price_volatility,
                },
                "meta": {
                    "token_address": token,
                    "sample_time": sample_time,
                    "create_timestamp": 100,
                },
            }

        episodes = [[
            sample("0xnear", 120, 1.0, 1.25, 0.08),
            sample("0xnear", 130, 1.4, 1.40, 0.10),
        ]]

        out = m._run_eval_replay(
            episodes,
            None,
            0.98,
            _SellNonePolicy(),
            buy_probabilities_by_episode=[{0: 0.95}],
            entry_scores_by_episode=[{0: 33.0}],
            buy_near_threshold_min_prob=0.94,
            buy_near_min_pred_return=32.0,
            buy_near_min_entry_volume_30s=1.25,
            buy_near_min_entry_price_volatility=0.08,
            buy_near_min_age_seconds=0.0,
            position_fraction=0.1,
            include_trade_log=True,
        )

        self.assertEqual(out["entry_signal_count"], 1)
        self.assertEqual(out["near_threshold_signal_count"], 1)
        self.assertEqual(out["near_threshold_entry_count"], 1)
        self.assertEqual(out["near_threshold_reject_count"], 0)
        self.assertEqual(out["total_trades"], 1)
        self.assertTrue(out["trade_log"][0]["near_threshold_rescue_used"])
        self.assertEqual(out["trade_log"][0]["entry_score"], 33.0)

    def test_run_eval_replay_does_not_count_failed_near_threshold_execution_as_entry(self):
        m = _load_module()

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        episodes = [[
            {
                "features": {
                    "current_price": 1.0,
                    "holder_count": 10,
                    "volume_30s": 1.25,
                    "price_volatility": 0.08,
                },
                "meta": {
                    "token_address": "0xnear-fail",
                    "sample_time": 120,
                    "create_timestamp": 100,
                },
            }
        ]]

        out = m._run_eval_replay(
            episodes,
            None,
            0.98,
            _SellNonePolicy(),
            buy_probabilities_by_episode=[{0: 0.95}],
            entry_scores_by_episode=[{0: 33.0}],
            buy_near_threshold_min_prob=0.94,
            buy_near_min_pred_return=32.0,
            buy_near_min_entry_volume_30s=1.25,
            buy_near_min_entry_price_volatility=0.08,
            buy_near_min_age_seconds=0.0,
            entry_execution_failure_rate=1.0,
            position_fraction=0.1,
        )

        self.assertEqual(out["entry_signal_count"], 1)
        self.assertEqual(out["near_threshold_signal_count"], 1)
        self.assertEqual(out["entry_attempt_count"], 1)
        self.assertEqual(out["entry_execution_failure_count"], 1)
        self.assertEqual(out["entry_count"], 0)
        self.assertEqual(out["near_threshold_entry_count"], 0)
        self.assertEqual(out["total_trades"], 0)

    def test_run_eval_replay_accepts_qualified_primary_score_rescue(self):
        m = _load_module()

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(token, sample_time, price, volume_30s, price_volatility):
            return {
                "features": {
                    "current_price": price,
                    "holder_count": 10,
                    "volume_30s": volume_30s,
                    "price_volatility": price_volatility,
                },
                "meta": {
                    "token_address": token,
                    "sample_time": sample_time,
                    "create_timestamp": 100,
                },
            }

        episodes = [[
            sample("0xprimary", 120, 1.0, 3.5, 0.32),
            sample("0xprimary", 130, 1.4, 3.6, 0.34),
        ]]

        out = m._run_eval_replay(
            episodes,
            None,
            0.98,
            _SellNonePolicy(),
            buy_probabilities_by_episode=[{0: 0.991}],
            entry_scores_by_episode=[{0: 25.0}],
            min_entry_score=35.0,
            min_entry_volume_30s=1.5,
            min_entry_price_volatility=0.1,
            buy_primary_score_rescue_min_prob=0.985,
            buy_primary_score_rescue_min_pred_return=25.0,
            buy_primary_score_rescue_min_entry_volume_30s=3.0,
            buy_primary_score_rescue_min_entry_price_volatility=0.30,
            buy_primary_score_rescue_min_age_seconds=0.0,
            position_fraction=0.1,
            include_trade_log=True,
        )

        self.assertEqual(out["entry_signal_count"], 1)
        self.assertEqual(out["entry_score_reject_count"], 0)
        self.assertEqual(out["primary_score_rescue_signal_count"], 1)
        self.assertEqual(out["primary_score_rescue_entry_count"], 1)
        self.assertEqual(out["primary_score_rescue_reject_count"], 0)
        self.assertEqual(out["total_trades"], 1)
        self.assertTrue(out["trade_log"][0]["primary_score_rescue_used"])
        self.assertEqual(out["trade_log"][0]["entry_score"], 25.0)

    def test_run_eval_replay_rejects_low_quality_primary_score_rescue(self):
        m = _load_module()

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        episodes = [[
            {
                "features": {
                    "current_price": 1.0,
                    "holder_count": 10,
                    "volume_30s": 2.0,
                    "price_volatility": 0.32,
                },
                "meta": {
                    "token_address": "0xprimary-low-quality",
                    "sample_time": 120,
                    "create_timestamp": 100,
                },
            }
        ]]

        out = m._run_eval_replay(
            episodes,
            None,
            0.98,
            _SellNonePolicy(),
            buy_probabilities_by_episode=[{0: 0.991}],
            entry_scores_by_episode=[{0: 25.0}],
            min_entry_score=35.0,
            min_entry_volume_30s=1.5,
            min_entry_price_volatility=0.1,
            buy_primary_score_rescue_min_prob=0.985,
            buy_primary_score_rescue_min_pred_return=25.0,
            buy_primary_score_rescue_min_entry_volume_30s=3.0,
            buy_primary_score_rescue_min_entry_price_volatility=0.30,
            buy_primary_score_rescue_min_age_seconds=0.0,
            position_fraction=0.1,
        )

        self.assertEqual(out["entry_signal_count"], 1)
        self.assertEqual(out["entry_score_reject_count"], 1)
        self.assertEqual(out["primary_score_rescue_signal_count"], 1)
        self.assertEqual(out["primary_score_rescue_entry_count"], 0)
        self.assertEqual(out["primary_score_rescue_reject_count"], 1)
        self.assertEqual(out["total_trades"], 0)

    def test_run_eval_replay_min_entry_volume_30s_filters_low_quality_signals(self):
        m = _load_module()

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(token, sample_time, price, volume_30s):
            return {
                "features": {"current_price": price, "holder_count": 10, "volume_30s": volume_30s},
                "meta": {"token_address": token, "sample_time": sample_time},
            }

        episodes = [
            [sample("0xa", 100, 1.0, 1.5), sample("0xa", 103, 1.1, 1.6)],
            [sample("0xb", 100, 1.0, 2.2), sample("0xb", 103, 1.1, 2.3)],
        ]

        out = m._run_eval_replay(
            episodes,
            None,
            0.5,
            _SellNonePolicy(),
            buy_probabilities_by_episode=[{0: 0.9}, {0: 0.9}],
            min_entry_volume_30s=2.0,
            position_fraction=0.1,
            include_trade_log=True,
        )

        self.assertEqual(out["entry_signal_count"], 2)
        self.assertEqual(out["entry_quality_reject_count"], 1)
        self.assertEqual(out["entry_quality_reject_rate"], 0.5)
        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["trade_log"][0]["token"], "0xb")

    def test_run_eval_replay_min_entry_price_volatility_filters_low_quality_signals(self):
        m = _load_module()

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(token, sample_time, price, price_volatility):
            return {
                "features": {
                    "current_price": price,
                    "holder_count": 10,
                    "volume_30s": 2.0,
                    "price_volatility": price_volatility,
                },
                "meta": {"token_address": token, "sample_time": sample_time},
            }

        episodes = [
            [sample("0xa", 100, 1.0, 0.08), sample("0xa", 103, 1.1, 0.09)],
            [sample("0xb", 100, 1.0, 0.12), sample("0xb", 103, 1.1, 0.13)],
        ]

        out = m._run_eval_replay(
            episodes,
            None,
            0.5,
            _SellNonePolicy(),
            buy_probabilities_by_episode=[{0: 0.9}, {0: 0.9}],
            min_entry_price_volatility=0.10,
            position_fraction=0.1,
            include_trade_log=True,
        )

        self.assertEqual(out["entry_signal_count"], 2)
        self.assertEqual(out["entry_quality_reject_count"], 1)
        self.assertEqual(out["entry_quality_reject_rate"], 0.5)
        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["trade_log"][0]["token"], "0xb")

    def test_run_ab_evaluation_applies_entry_ranking_mode(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                by_price = {1.0: 0.55, 2.0: 0.9, 3.0: 0.8}
                return [[1.0 - by_price[float(price)], by_price[float(price)]] for price in X["current_price"]]

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(token, sample_time, price):
            return {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": token, "sample_time": sample_time},
            }

        eval_samples = [
            sample("0xa", 100, 1.0),
            sample("0xb", 100, 2.0),
            sample("0xc", 100, 3.0),
            sample("0xa", 103, 1.1),
            sample("0xb", 103, 2.2),
            sample("0xc", 103, 3.3),
        ]

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "include_trade_log": True,
                "entry_delay_seconds": 3,
                "max_open_positions": 1,
                "entry_ranking_mode": "buy_prob",
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"model": _SellNonePolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["entry_ranking_mode"], "buy_prob")
        self.assertEqual(out["runtime_replay"]["entry_ranking_mode"], "buy_prob")
        self.assertEqual(out["trade_log"][0]["token"], "0xb")

    def test_run_ab_evaluation_uses_entry_value_artifact_for_ranking(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _FakeEntryValueModel:
            def predict(self, X):
                by_price = {1.0: 1.0, 2.0: 20.0, 3.0: 5.0}
                return [by_price[float(price)] for price in X["current_price"]]

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(token, sample_time, price):
            return {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": token, "sample_time": sample_time},
            }

        eval_samples = [
            sample("0xa", 100, 1.0),
            sample("0xb", 100, 2.0),
            sample("0xc", 100, 3.0),
            sample("0xa", 103, 1.1),
            sample("0xb", 103, 2.2),
            sample("0xc", 103, 3.3),
        ]

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "include_trade_log": True,
                "entry_delay_seconds": 3,
                "max_open_positions": 1,
                "entry_ranking_mode": "entry_value",
            },
            {
                "model": _FakeBuyModel(),
                "threshold": 0.5,
                "entry_value_model": {"model": _FakeEntryValueModel()},
            },
            {"model": _SellNonePolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["entry_ranking_mode"], "entry_value")
        self.assertEqual(out["trade_log"][0]["token"], "0xb")
        self.assertEqual(out["trade_log"][0]["entry_score"], 20.0)

    def test_run_ab_evaluation_applies_min_entry_score_filter(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _FakeEntryValueModel:
            def predict(self, X):
                by_price = {1.0: 1.0, 2.0: 20.0, 3.0: 5.0}
                return [by_price[float(price)] for price in X["current_price"]]

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(token, sample_time, price):
            return {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": token, "sample_time": sample_time},
            }

        eval_samples = [
            sample("0xa", 100, 1.0),
            sample("0xb", 100, 2.0),
            sample("0xc", 100, 3.0),
            sample("0xa", 103, 1.1),
            sample("0xb", 103, 2.2),
            sample("0xc", 103, 3.3),
        ]

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "include_trade_log": True,
                "min_entry_score": 10.0,
                "position_fraction": 0.1,
            },
            {
                "model": _FakeBuyModel(),
                "threshold": 0.5,
                "entry_value_model": {"model": _FakeEntryValueModel()},
            },
            {"model": _SellNonePolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["min_entry_score"], 10.0)
        self.assertEqual(out["entry_score_reject_count"], 2)
        self.assertEqual(out["trade_log"][0]["token"], "0xb")
        self.assertEqual(out["trade_log"][0]["entry_score"], 20.0)

    def test_run_ab_evaluation_applies_near_threshold_rescue_gate(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.05, 0.95] for _ in range(len(X))]

        class _FakeEntryValueModel:
            def predict(self, X):
                return [33.0 for _ in range(len(X))]

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(sample_time, price):
            return {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                    "volume_30s": 1.25,
                    "price_volatility": 0.08,
                },
                "meta": {
                    "token_address": "0xnear",
                    "sample_time": sample_time,
                    "create_timestamp": 100,
                },
            }

        out = m.run_ab_evaluation(
            {
                "eval_samples": [sample(120, 1.0), sample(130, 1.4)],
                "include_trade_log": True,
                "entry_ranking_mode": "entry_value",
                "min_entry_score": 35.0,
                "min_entry_volume_30s": 1.5,
                "min_entry_price_volatility": 0.1,
                "buy_near_threshold_min_prob": 0.94,
                "buy_near_min_pred_return": 32.0,
                "buy_near_min_entry_volume_30s": 1.25,
                "buy_near_min_entry_price_volatility": 0.08,
                "buy_near_min_age_seconds": 0.0,
                "position_fraction": 0.1,
                "skip_all_in_replay": True,
            },
            {
                "model": _FakeBuyModel(),
                "threshold": 0.98,
                "entry_value_model": {"model": _FakeEntryValueModel()},
            },
            {"model": _SellNonePolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["buy_near_threshold_min_prob"], 0.94)
        self.assertEqual(out["runtime_replay"]["near_threshold_entry_count"], 1)
        self.assertTrue(out["trade_log"][0]["near_threshold_rescue_used"])

    def test_run_ab_evaluation_applies_primary_score_rescue_gate(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.009, 0.991] for _ in range(len(X))]

        class _FakeEntryValueModel:
            def predict(self, X):
                return [25.0 for _ in range(len(X))]

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(sample_time, price):
            return {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                    "volume_30s": 3.5,
                    "price_volatility": 0.32,
                },
                "meta": {
                    "token_address": "0xprimary",
                    "sample_time": sample_time,
                    "create_timestamp": 100,
                },
            }

        out = m.run_ab_evaluation(
            {
                "eval_samples": [sample(120, 1.0), sample(130, 1.4)],
                "include_trade_log": True,
                "entry_ranking_mode": "entry_value",
                "min_entry_score": 35.0,
                "min_entry_volume_30s": 1.5,
                "min_entry_price_volatility": 0.1,
                "buy_primary_score_rescue_min_prob": 0.985,
                "buy_primary_score_rescue_min_pred_return": 25.0,
                "buy_primary_score_rescue_min_entry_volume_30s": 3.0,
                "buy_primary_score_rescue_min_entry_price_volatility": 0.30,
                "buy_primary_score_rescue_min_age_seconds": 0.0,
                "position_fraction": 0.1,
                "skip_all_in_replay": True,
            },
            {
                "model": _FakeBuyModel(),
                "threshold": 0.98,
                "entry_value_model": {"model": _FakeEntryValueModel()},
            },
            {"model": _SellNonePolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["buy_primary_score_rescue_min_prob"], 0.985)
        self.assertEqual(out["buy_primary_score_rescue_min_pred_return"], 25.0)
        self.assertEqual(out["runtime_replay"]["primary_score_rescue_entry_count"], 1)
        self.assertTrue(out["trade_log"][0]["primary_score_rescue_used"])

    def test_run_ab_evaluation_does_not_require_entry_value_when_near_probability_disabled(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        sample = {
            "features": {
                "current_price": 1.0,
                "launch_fee": 0.5,
                "holder_count": 10,
                "total_buy_volume": 10.0,
                "total_sell_volume": 1.0,
            },
            "meta": {"token_address": "0xprimary", "sample_time": 120},
        }

        out = m.run_ab_evaluation(
            {
                "eval_samples": [sample, {**sample, "meta": {"token_address": "0xprimary", "sample_time": 130}}],
                "buy_near_min_pred_return": 32.0,
                "position_fraction": 0.1,
                "skip_all_in_replay": True,
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"model": _SellNonePolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["total_trades"], 1)

    def test_run_ab_evaluation_does_not_require_entry_value_when_primary_rescue_has_no_score_gate(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.009, 0.991] for _ in range(len(X))]

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(sample_time, price):
            return {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                    "volume_30s": 3.5,
                    "price_volatility": 0.32,
                },
                "meta": {
                    "token_address": "0xprimary",
                    "sample_time": sample_time,
                    "create_timestamp": 100,
                },
            }

        out = m.run_ab_evaluation(
            {
                "eval_samples": [sample(120, 1.0), sample(130, 1.2)],
                "include_trade_log": True,
                "entry_ranking_mode": "chronological",
                "min_entry_score": None,
                "buy_primary_score_rescue_min_prob": 0.985,
                "buy_primary_score_rescue_min_pred_return": 25.0,
                "position_fraction": 0.1,
                "skip_all_in_replay": True,
            },
            {
                "model": _FakeBuyModel(),
                "threshold": 0.98,
            },
            {"model": _SellNonePolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["primary_score_rescue_signal_count"], 0)
        self.assertIsNone(out["buy_near_threshold_min_prob"])

    def test_run_eval_replay_rejects_primary_score_rescue_probability_live_would_reject(self):
        m = _load_module()

        with self.assertRaisesRegex(ValueError, "buy_primary_score_rescue_min_prob"):
            m._run_eval_replay(
                [[]],
                None,
                0.98,
                None,
                buy_primary_score_rescue_min_prob=0.0,
                buy_primary_score_rescue_min_pred_return=25.0,
            )

    def test_run_eval_replay_zero_max_open_positions_means_unlimited_capacity(self):
        m = _load_module()

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(token, sample_time):
            return {
                "features": {"current_price": 1.0, "holder_count": 10},
                "meta": {
                    "token_address": token,
                    "sample_time": sample_time,
                    "create_timestamp": 100,
                },
            }

        out = m._run_eval_replay(
            [[sample("0xA", 120)], [sample("0xB", 120)]],
            None,
            0.5,
            _SellNonePolicy(),
            buy_probabilities_by_episode=[{0: 0.9}, {0: 0.9}],
            max_open_positions=0,
            max_hold_seconds=300,
            position_fraction=0.1,
        )

        self.assertIsNone(out["max_open_positions"])
        self.assertEqual(out["total_trades"], 2)

    def test_run_eval_replay_exit_execution_failure_retries_pending_exit(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        def sample(sample_time, price):
            return {
                "features": {"current_price": price, "holder_count": 10},
                "meta": {"token_address": "0xfailexit", "sample_time": sample_time},
            }

        episodes = [[sample(100, 1.0), sample(110, 2.0), sample(114, 3.0), sample(120, 4.0)]]

        out = m._run_eval_replay(
            episodes,
            _FakeBuyModel(),
            0.5,
            _SellAllPolicy(),
            exit_delay_seconds=3,
            exit_execution_failure_rate=1.0,
            include_trade_log=True,
        )

        self.assertGreaterEqual(out["exit_execution_failure_count"], 1)
        self.assertEqual(out["exit_fill_count"], 0)
        self.assertEqual(out["trade_log"][0]["exit_reason"], "REPLAY_END")

    def test_run_eval_replay_entry_fill_times_out_after_max_wait(self):
        m = _load_module()

        class _UnusedBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        episodes = [
            [
                {"features": {"current_price": 1.0, "holder_count": 10}, "meta": {"token_address": "0xtimeout", "sample_time": 100}},
                {"features": {"current_price": 2.0, "holder_count": 11}, "meta": {"token_address": "0xtimeout", "sample_time": 110}},
            ]
        ]

        out = m._run_eval_replay(
            episodes,
            _UnusedBuyModel(),
            0.5,
            None,
            buy_probabilities_by_episode=[{0: 0.9}],
            entry_delay_seconds=3,
            entry_max_fill_wait_seconds=3,
        )

        self.assertEqual(out["total_trades"], 0)
        self.assertEqual(out["entry_count"], 0)
        self.assertEqual(out["entry_signal_count"], 1)
        self.assertEqual(out["entry_attempt_count"], 1)
        self.assertEqual(out["entry_timeout_count"], 1)
        self.assertEqual(out["entry_timeout_rate"], 1.0)
        self.assertEqual(out["entry_fill_count"], 0)
        self.assertEqual(out["entry_fill_rate"], 0.0)

    def test_run_eval_replay_uses_live_entry_label_for_delayed_fill_between_sparse_samples(self):
        m = _load_module()

        class _UnusedBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        episodes = [
            [
                {
                    "features": {"current_price": 1.0, "holder_count": 10},
                    "label": {
                        "live_entry_available": 1,
                        "live_entry_time": 103,
                        "live_entry_price": 1.2,
                        "live_entry_wait_seconds": 3,
                    },
                    "meta": {"token_address": "0xlivefill", "sample_time": 100},
                },
                {
                    "features": {"current_price": 2.0, "holder_count": 11},
                    "meta": {"token_address": "0xlivefill", "sample_time": 110},
                },
            ]
        ]

        out = m._run_eval_replay(
            episodes,
            _UnusedBuyModel(),
            0.5,
            None,
            buy_probabilities_by_episode=[{0: 0.9}],
            entry_delay_seconds=3,
            entry_max_fill_wait_seconds=3,
            include_trade_log=True,
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["entry_count"], 1)
        self.assertEqual(out["entry_timeout_count"], 0)
        self.assertEqual(out["entry_fill_count"], 1)
        self.assertEqual(out["avg_entry_wait_seconds"], 3.0)
        self.assertEqual(out["avg_entry_fill_lag_seconds"], 0.0)
        self.assertEqual(out["trade_log"][0]["entry_time"], 103)
        self.assertEqual(out["trade_log"][0]["entry_price"], 1.2)

    def test_run_eval_replay_quick_profit_confirmation_delay_uses_later_fill(self):
        m = _load_module()

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(sample_time, price):
            return {
                "features": {
                    "current_price": price,
                    "holder_count": 10,
                    "volume_30s": 2.0,
                    "price_volatility": 0.12,
                },
                "meta": {
                    "token_address": "0xquickconfirm",
                    "sample_time": sample_time,
                    "create_timestamp": 90,
                },
            }

        out = m._run_eval_replay(
            [[sample(100, 1.0), sample(108, 1.03), sample(120, 1.4)]],
            None,
            0.98,
            _SellNonePolicy(),
            buy_probabilities_by_episode=[{0: 0.991}],
            entry_scores_by_episode=[{0: 31.0}],
            entry_delay_seconds=3,
            min_entry_score=35.0,
            min_entry_volume_30s=1.25,
            min_entry_price_volatility=0.08,
            buy_quick_profit_overlay_min_prob=0.985,
            buy_quick_profit_overlay_min_pred_return=30.0,
            buy_quick_profit_overlay_max_pred_return=35.0,
            buy_quick_profit_overlay_min_entry_volume_30s=1.25,
            buy_quick_profit_overlay_min_entry_price_volatility=0.08,
            buy_quick_profit_overlay_max_age_seconds=60.0,
            buy_quick_profit_overlay_take_profit_pct=0.25,
            buy_quick_profit_overlay_max_hold_seconds=120.0,
            buy_quick_profit_overlay_confirmation_delay_seconds=5,
            buy_quick_profit_overlay_max_confirmation_drawdown_pct=0.05,
            buy_quick_profit_overlay_max_confirmation_chase_pct=0.10,
            position_fraction=0.1,
            include_trade_log=True,
        )

        self.assertEqual(out["quick_profit_overlay_signal_count"], 1)
        self.assertEqual(out["quick_profit_overlay_confirmation_reject_count"], 0)
        self.assertEqual(out["quick_profit_overlay_entry_count"], 1)
        self.assertEqual(out["trade_log"][0]["entry_time"], 108)
        self.assertEqual(out["trade_log"][0]["entry_index"], 1)
        self.assertAlmostEqual(out["trade_log"][0]["entry_price"], 1.03)

    def test_run_eval_replay_quick_profit_confirmation_rejects_failed_hold(self):
        m = _load_module()

        class _SellNonePolicy:
            def predict(self, obs, deterministic=True):
                return 0, None

        def sample(sample_time, price):
            return {
                "features": {
                    "current_price": price,
                    "holder_count": 10,
                    "volume_30s": 2.0,
                    "price_volatility": 0.12,
                },
                "meta": {
                    "token_address": "0xquickreject",
                    "sample_time": sample_time,
                    "create_timestamp": 90,
                },
            }

        out = m._run_eval_replay(
            [[sample(100, 1.0), sample(108, 0.90), sample(120, 1.4)]],
            None,
            0.98,
            _SellNonePolicy(),
            buy_probabilities_by_episode=[{0: 0.991}],
            entry_scores_by_episode=[{0: 31.0}],
            entry_delay_seconds=3,
            min_entry_score=35.0,
            min_entry_volume_30s=1.25,
            min_entry_price_volatility=0.08,
            buy_quick_profit_overlay_min_prob=0.985,
            buy_quick_profit_overlay_min_pred_return=30.0,
            buy_quick_profit_overlay_max_pred_return=35.0,
            buy_quick_profit_overlay_min_entry_volume_30s=1.25,
            buy_quick_profit_overlay_min_entry_price_volatility=0.08,
            buy_quick_profit_overlay_max_age_seconds=60.0,
            buy_quick_profit_overlay_take_profit_pct=0.25,
            buy_quick_profit_overlay_max_hold_seconds=120.0,
            buy_quick_profit_overlay_confirmation_delay_seconds=5,
            buy_quick_profit_overlay_max_confirmation_drawdown_pct=0.05,
            buy_quick_profit_overlay_max_confirmation_chase_pct=0.10,
            position_fraction=0.1,
            include_trade_log=True,
        )

        self.assertEqual(out["quick_profit_overlay_signal_count"], 1)
        self.assertEqual(out["quick_profit_overlay_confirmation_reject_count"], 1)
        self.assertEqual(out["quick_profit_overlay_entry_count"], 0)
        self.assertEqual(out["entry_attempt_count"], 0)
        self.assertEqual(out["total_trades"], 0)

    def test_run_eval_replay_checks_exit_on_sample_that_confirms_delayed_fill(self):
        m = _load_module()

        class _UnusedBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        episodes = [
            [
                {
                    "features": {"current_price": 1.0, "holder_count": 10},
                    "label": {
                        "live_entry_available": 1,
                        "live_entry_time": 103,
                        "live_entry_price": 1.0,
                    },
                    "meta": {"token_address": "0xliveexit", "sample_time": 100},
                },
                {
                    "features": {"current_price": 1.2, "holder_count": 11},
                    "meta": {"token_address": "0xliveexit", "sample_time": 110},
                },
            ]
        ]

        out = m._run_eval_replay(
            episodes,
            _UnusedBuyModel(),
            0.5,
            _SellAllPolicy(),
            buy_probabilities_by_episode=[{0: 0.9}],
            entry_delay_seconds=3,
            entry_max_fill_wait_seconds=3,
            include_trade_log=True,
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["trade_log"][0]["entry_time"], 103)
        self.assertEqual(out["trade_log"][0]["exit_time"], 110)
        self.assertEqual(out["trade_log"][0]["exit_reason"], "SELL100")

    def test_run_eval_replay_entry_price_protection_skips_chase(self):
        m = _load_module()

        class _UnusedBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        episodes = [
            [
                {"features": {"current_price": 1.0, "holder_count": 10}, "meta": {"token_address": "0xprotect", "sample_time": 100}},
                {"features": {"current_price": 1.4, "holder_count": 11}, "meta": {"token_address": "0xprotect", "sample_time": 103}},
                {"features": {"current_price": 2.0, "holder_count": 12}, "meta": {"token_address": "0xprotect", "sample_time": 110}},
            ]
        ]

        out = m._run_eval_replay(
            episodes,
            _UnusedBuyModel(),
            0.5,
            None,
            buy_probabilities_by_episode=[{0: 0.9}],
            entry_delay_seconds=3,
            entry_price_protection_pct=0.25,
        )

        self.assertEqual(out["total_trades"], 0)
        self.assertEqual(out["entry_signal_count"], 1)
        self.assertEqual(out["entry_attempt_count"], 1)
        self.assertEqual(out["entry_price_protection_skip_count"], 1)
        self.assertEqual(out["entry_price_protection_skip_rate"], 1.0)
        self.assertEqual(out["entry_fill_count"], 0)

    def test_run_eval_replay_reports_entry_funnel_for_successful_fill(self):
        m = _load_module()

        class _UnusedBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        episodes = [
            [
                {"features": {"current_price": 1.0, "holder_count": 10}, "meta": {"token_address": "0xfunnel", "sample_time": 100}},
                {"features": {"current_price": 1.1, "holder_count": 11}, "meta": {"token_address": "0xfunnel", "sample_time": 103}},
            ]
        ]

        out = m._run_eval_replay(
            episodes,
            _UnusedBuyModel(),
            0.5,
            None,
            buy_probabilities_by_episode=[{0: 0.9}],
            entry_delay_seconds=3,
        )

        self.assertEqual(out["entry_signal_count"], 1)
        self.assertEqual(out["entry_attempt_count"], 1)
        self.assertEqual(out["entry_fill_count"], 1)
        self.assertEqual(out["entry_signal_rate"], 1.0)
        self.assertEqual(out["entry_fill_rate"], 1.0)
        self.assertEqual(out["entry_timeout_rate"], 0.0)
        self.assertEqual(out["entry_price_protection_skip_rate"], 0.0)

    def test_run_eval_replay_exit_fill_timeout_is_reported(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        episodes = [
            [
                {"features": {"current_price": 1.0, "holder_count": 10}, "meta": {"token_address": "0xexittimeout", "sample_time": 100}},
                {"features": {"current_price": 1.0, "holder_count": 11}, "meta": {"token_address": "0xexittimeout", "sample_time": 101}},
                {"features": {"current_price": 2.0, "holder_count": 12}, "meta": {"token_address": "0xexittimeout", "sample_time": 111}},
            ]
        ]

        out = m._run_eval_replay(
            episodes,
            _FakeBuyModel(),
            0.5,
            _SellAllPolicy(),
            buy_probabilities_by_episode=[{0: 0.9}],
            exit_delay_seconds=3,
            exit_max_fill_wait_seconds=3,
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["exit_timeout_count"], 1)
        self.assertGreaterEqual(out["max_exit_wait_seconds"], 7)

    def test_run_eval_replay_replay_end_liquidation_does_not_double_count_open_position(self):
        m = _load_module()

        class _UnusedBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        episodes = [
            [
                {
                    "features": {"current_price": 1.0, "holder_count": 10},
                    "meta": {"token_address": "0xflat", "sample_time": 100},
                }
            ]
        ]

        out = m._run_eval_replay(
            episodes,
            _UnusedBuyModel(),
            0.5,
            None,
            buy_probabilities_by_episode=[{0: 0.9}],
            fixed_stake_bnb=0.1,
            initial_equity_bnb=1.0,
            fee_bps=0.0,
            slippage_bps=0.0,
            include_trade_log=True,
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertAlmostEqual(out["final_equity_bnb"], 1.0)
        self.assertAlmostEqual(out["net_profit_bnb"], 0.0)
        self.assertAlmostEqual(out["account_multiple"], 1.0)
        self.assertEqual(out["trade_log"][0]["exit_reason"], "REPLAY_END")
        self.assertAlmostEqual(out["trade_log"][0]["return_pct"], 0.0)

    def test_run_ab_evaluation_exit_delay_closes_at_token_end_before_next_entry(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        def sample(token, sample_time, price):
            return {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": token, "sample_time": sample_time},
            }

        eval_samples = [
            sample("0xfirst", 100, 1.0),
            sample("0xfirst", 110, 2.0),
            sample("0xsecond", 200, 1.0),
            sample("0xsecond", 210, 2.0),
        ]

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "include_trade_log": True,
                "exit_delay_seconds": 5,
                "max_open_positions": 1,
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"model": _SellAllPolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["entry_count"], 2)
        self.assertEqual(out["total_trades"], 2)
        self.assertEqual([trade["exit_time"] for trade in out["trade_log"]], [110, 210])

    def test_run_ab_evaluation_one_entry_per_token_blocks_reentry(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        eval_samples = [
            {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10 + idx,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xrepeat", "sample_time": 100 + idx},
            }
            for idx, price in enumerate([1.0, 1.1, 1.2, 1.3])
        ]

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "one_entry_per_token": True,
                "include_trade_log": True,
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128, "model": _SellAllPolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(len(out["trade_log"]), 1)
        self.assertTrue(out["one_entry_per_token"])

    def test_run_ab_evaluation_includes_trade_log_when_requested(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                    "lifecycle_status_chain_lag_seconds": 7.5,
                },
                "meta": {"token_address": "0xlog", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 0.4,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0xlog", "sample_time": 110},
            },
        ]

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "stop_loss": -0.5,
                "position_fraction": 0.1,
                "include_trade_log": True,
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128},
            {"bc_samples": 10},
        )

        self.assertEqual(len(out["trade_log"]), 1)
        trade = out["trade_log"][0]
        self.assertEqual(trade["token"], "0xlog")
        self.assertEqual(trade["exit_reason"], "STOP_LOSS")
        self.assertAlmostEqual(trade["entry_price"], 1.0)
        self.assertAlmostEqual(trade["exit_price"], 0.4)
        self.assertAlmostEqual(trade["return_pct"], -60.0)
        self.assertAlmostEqual(trade["buy_prob"], 0.9)
        self.assertAlmostEqual(trade["lifecycle_status_chain_lag_seconds"], 7.5)
        self.assertAlmostEqual(trade["max_adverse_excursion_pct"], -60.0)
        self.assertAlmostEqual(trade["max_favorable_excursion_pct"], 0.0)

    def test_run_ab_evaluation_min_policy_hold_delays_policy_sell(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _ImmediateSellPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xminhold", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.1,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xminhold", "sample_time": 103},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "launch_fee": 0.5,
                    "holder_count": 12,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xminhold", "sample_time": 110},
            },
        ]

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "include_trade_log": True,
                "min_policy_hold_seconds": 5,
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"model": _ImmediateSellPolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(out["trade_log"][0]["exit_time"], 110)
        self.assertEqual(out["trade_log"][0]["exit_reason"], "SELL100")
        self.assertEqual(out["min_policy_hold_seconds"], 5)

    def test_run_ab_evaluation_trailing_stop_exits_before_policy_hold(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _HoldPolicy:
            def __init__(self):
                self.calls = 0

            def predict(self, obs, deterministic=True):
                self.calls += 1
                return 0, None

        policy = _HoldPolicy()
        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xtrail", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.5,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xtrail", "sample_time": 110},
            },
            {
                "features": {
                    "current_price": 1.1,
                    "launch_fee": 0.5,
                    "holder_count": 12,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xtrail", "sample_time": 120},
            },
        ]

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "include_trade_log": True,
                "trailing_start_pct": 0.2,
                "trailing_stop_pct": 0.2,
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128, "model": policy},
            {"bc_samples": 10},
        )

        self.assertEqual(out["trade_log"][0]["exit_reason"], "TRAILING_STOP")
        self.assertEqual(policy.calls, 2)

    def test_run_ab_evaluation_rug_guard_exits_before_policy_hold(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _HoldPolicy:
            def __init__(self):
                self.calls = 0

            def predict(self, obs, deterministic=True):
                self.calls += 1
                return 0, None

        policy = _HoldPolicy()
        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xrug", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 0.9,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 99.0,
                },
                "meta": {"token_address": "0xrug", "sample_time": 110},
            },
        ]

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "include_trade_log": True,
                "rug_sell_pressure": 0.95,
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128, "model": policy},
            {"bc_samples": 10},
        )

        self.assertEqual(out["trade_log"][0]["exit_reason"], "RUG_EXIT")
        self.assertEqual(policy.calls, 0)

    def test_run_ab_evaluation_passes_position_state_to_sell_policy(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _CapturePolicy:
            def __init__(self):
                self.observations = []

            def predict(self, obs, deterministic=True):
                self.observations.append(list(obs))
                return 0, None

        policy = _CapturePolicy()
        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xstate", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xstate", "sample_time": 110},
            },
        ]

        m.run_ab_evaluation(
            {"eval_samples": eval_samples},
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128, "model": policy},
            {"bc_samples": 10},
        )

        self.assertEqual(len(policy.observations), 2)
        obs = policy.observations[0]
        self.assertEqual(len(obs), 11)
        self.assertAlmostEqual(float(obs[5]), 10.0)
        self.assertAlmostEqual(float(obs[6]), 10.0)
        self.assertAlmostEqual(float(obs[7]), 0.2, places=6)
        self.assertAlmostEqual(float(obs[8]), 0.2, places=6)
        self.assertAlmostEqual(float(obs[9]), 0.0, places=6)
        self.assertAlmostEqual(float(obs[10]), 1.0)

    def test_run_ab_evaluation_skips_last_sample_instant_entry(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0x4", "sample_time": 100},
            }
        ]

        with self.assertRaisesRegex(ValueError, "no eval episodes"):
            m.run_ab_evaluation(
                {"eval_samples": eval_samples},
                {"model": _FakeBuyModel(), "threshold": 0.5},
                {"total_timesteps": 128},
                {"bc_samples": 10},
            )

    def test_run_ab_evaluation_aligns_eval_features_to_training_schema(self):
        m = _load_module()

        class _FakeBuyModel:
            def __init__(self):
                self.frames = []

            def predict_proba(self, X):
                self.frames.append(X.copy())
                return [[0.9, 0.1] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "holder_count": 10.0,
                    "launch_fee": 0.5,
                    "total_buy_volume": 123.0,
                    "total_sell_volume": 45.0,
                },
                "meta": {"token_address": "0x5", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "holder_count": 11.0,
                    "launch_fee": 0.6,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0x5", "sample_time": 110},
            },
        ]

        buy_model = _FakeBuyModel()
        m.run_ab_evaluation(
            {"eval_samples": eval_samples},
            {
                "model": buy_model,
                "threshold": 0.5,
                "feature_names": ["current_price", "holder_count", "launch_fee", "total_buy_volume", "total_sell_volume"],
            },
            {"total_timesteps": 128},
            {"bc_samples": 10},
        )

        first_frame = buy_model.frames[0]
        self.assertEqual(
            list(first_frame.columns),
            ["current_price", "holder_count", "launch_fee", "total_buy_volume", "total_sell_volume"],
        )
        self.assertEqual(first_frame.iloc[0]["holder_count"], 10.0)
        self.assertEqual(first_frame.iloc[0]["launch_fee"], 0.5)

    def test_run_ab_evaluation_batches_schema_validation_for_buy_inference(self):
        import pandas as pd

        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {"current_price": 1.0, "signal": 0.8},
                "meta": {"token_address": "0xbatch-schema", "sample_time": 100},
            },
            {
                "features": {"current_price": 1.2, "signal": 0.7},
                "meta": {"token_address": "0xbatch-schema", "sample_time": 110},
            },
            {
                "features": {"current_price": 1.1, "signal": 0.6},
                "meta": {"token_address": "0xbatch-schema", "sample_time": 120},
            },
        ]

        def _batch_feature_frame(rows, feature_names=None, ignored_feature_names=None):
            return pd.DataFrame(list(rows), columns=feature_names)

        with patch.object(m, "build_feature_frame", side_effect=AssertionError("single-row validation used")), \
             patch.object(m, "build_feature_frame_many", side_effect=_batch_feature_frame, create=True) as mock_batch:
            out = m.run_ab_evaluation(
                {"eval_samples": eval_samples},
                {
                    "model": _FakeBuyModel(),
                    "threshold": 0.5,
                    "feature_names": ["signal", "current_price"],
                },
                {"total_timesteps": 128},
                {"bc_samples": 10},
            )

        self.assertEqual(out["total_trades"], 1)
        self.assertGreaterEqual(mock_batch.call_count, 1)

    def test_run_ab_evaluation_raises_on_missing_eval_features_for_training_schema(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.9, 0.1] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "total_buy_volume": 123.0,
                    "total_sell_volume": 45.0,
                },
                "meta": {"token_address": "0x5a", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0x5a", "sample_time": 110},
            },
        ]

        with self.assertRaisesRegex(ValueError, "Missing expected features: holder_count, launch_fee"):
            m.run_ab_evaluation(
                {"eval_samples": eval_samples},
                {
                    "model": _FakeBuyModel(),
                    "threshold": 0.5,
                    "feature_names": ["current_price", "holder_count", "launch_fee", "total_buy_volume", "total_sell_volume"],
                },
                {"total_timesteps": 128},
                {"bc_samples": 10},
            )

    def test_run_ab_evaluation_raises_on_extra_eval_features_for_training_schema(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.9, 0.1] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "holder_count": 10.0,
                    "launch_fee": 0.5,
                    "total_buy_volume": 123.0,
                    "total_sell_volume": 45.0,
                    "unexpected_feature": 7.0,
                },
                "meta": {"token_address": "0x5b", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "holder_count": 11.0,
                    "launch_fee": 0.6,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                    "unexpected_feature": 8.0,
                },
                "meta": {"token_address": "0x5b", "sample_time": 110},
            },
        ]

        with self.assertRaisesRegex(ValueError, "Unexpected extra features: unexpected_feature"):
            m.run_ab_evaluation(
                {"eval_samples": eval_samples},
                {
                    "model": _FakeBuyModel(),
                    "threshold": 0.5,
                    "feature_names": ["current_price", "holder_count", "launch_fee", "total_buy_volume", "total_sell_volume"],
                },
                {"total_timesteps": 128},
                {"bc_samples": 10},
            )

    def test_run_ab_evaluation_raises_on_malformed_inline_feature_schema(self):
        m = _load_module()

        class _FakeBuyModel:
            def __init__(self):
                self.frames = []

            def predict_proba(self, X):
                self.frames.append(X.copy())
                return [[0.9, 0.1] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "total_buy_volume": 123.0,
                    "total_sell_volume": 45.0,
                },
                "meta": {"token_address": "0x6a", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0x6a", "sample_time": 110},
            },
        ]

        with self.assertRaisesRegex(ValueError, "feature_names must be a list"):
            m.run_ab_evaluation(
                {"eval_samples": eval_samples},
                {
                    "model": _FakeBuyModel(),
                    "threshold": 0.5,
                    "feature_names": "current_price",
                },
                {"total_timesteps": 128},
                {"bc_samples": 10},
            )

    def test_run_ab_evaluation_loads_feature_schema_from_artifact_path(self):
        import json
        import tempfile

        m = _load_module()

        class _FakeBuyModel:
            def __init__(self):
                self.frames = []

            def predict_proba(self, X):
                self.frames.append(X.copy())
                return [[0.9, 0.1] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "total_buy_volume": 123.0,
                    "total_sell_volume": 45.0,
                },
                "meta": {"token_address": "0x6", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0x6", "sample_time": 110},
            },
        ]

        buy_model = _FakeBuyModel()
        with tempfile.TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / "feature_schema.json"
            schema_path.write_text(
                json.dumps({"feature_names": ["current_price", "total_buy_volume", "total_sell_volume"]}),
                encoding="utf-8",
            )
            m.run_ab_evaluation(
                {"eval_samples": eval_samples},
                {
                    "model": buy_model,
                    "threshold": 0.5,
                    "feature_schema_path": str(schema_path),
                },
                {"total_timesteps": 128},
                {"bc_samples": 10},
            )

        first_frame = buy_model.frames[0]
        self.assertEqual(
            list(first_frame.columns),
            ["current_price", "total_buy_volume", "total_sell_volume"],
        )

    def test_run_ab_evaluation_ignores_schema_dropped_features(self):
        m = _load_module()

        class _FakeBuyModel:
            def __init__(self):
                self.frames = []

            def predict_proba(self, X):
                self.frames.append(X.copy())
                return [[0.1, 0.9] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "future_window": 240,
                    "constant_feature": 7.0,
                },
                "meta": {"token_address": "0x6drop", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.1,
                    "future_window": 240,
                    "constant_feature": 7.0,
                },
                "meta": {"token_address": "0x6drop", "sample_time": 110},
            },
        ]

        buy_model = _FakeBuyModel()
        m.run_ab_evaluation(
            {"eval_samples": eval_samples},
            {
                "model": buy_model,
                "threshold": 0.5,
                "feature_names": ["current_price"],
                "dropped_features": {
                    "invalid": ["future_window"],
                    "constant": ["constant_feature"],
                },
            },
            {"total_timesteps": 128},
            {"bc_samples": 10},
        )

        first_frame = buy_model.frames[0]
        self.assertEqual(list(first_frame.columns), ["current_price"])

    def test_run_ab_evaluation_ignores_optional_flow_features_for_non_flow_model(self):
        m = _load_module()

        class _FakeBuyModel:
            def __init__(self):
                self.frames = []

            def predict_proba(self, X):
                self.frames.append(X.copy())
                return [[0.1, 0.9] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "sell_volume_30s": 0.25,
                    "total_flow_volume_30s": 1.25,
                    "sell_pressure_30s": 0.2,
                    "signed_imbalance_30s": 0.6,
                },
                "meta": {"token_address": "0x6flow", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.1,
                    "sell_volume_30s": 0.2,
                    "total_flow_volume_30s": 1.0,
                    "sell_pressure_30s": 0.2,
                    "signed_imbalance_30s": 0.6,
                },
                "meta": {"token_address": "0x6flow", "sample_time": 110},
            },
        ]

        buy_model = _FakeBuyModel()
        m.run_ab_evaluation(
            {"eval_samples": eval_samples, "include_flow_features": True},
            {
                "model": buy_model,
                "threshold": 0.5,
                "feature_names": ["current_price"],
                "dropped_features": [],
            },
            {"total_timesteps": 128},
            {"bc_samples": 10},
        )

        first_frame = buy_model.frames[0]
        self.assertEqual(list(first_frame.columns), ["current_price"])

    def test_run_ab_evaluation_raises_on_invalid_feature_schema_file_metadata(self):
        import json
        import tempfile

        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.9, 0.1] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "total_buy_volume": 123.0,
                    "total_sell_volume": 45.0,
                },
                "meta": {"token_address": "0x6b", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0x6b", "sample_time": 110},
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / "feature_schema.json"
            schema_path.write_text(json.dumps({"feature_names": "bad"}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "feature_schema.json field 'feature_names' must be a list"):
                m.run_ab_evaluation(
                    {"eval_samples": eval_samples},
                    {
                        "model": _FakeBuyModel(),
                        "threshold": 0.5,
                        "feature_schema_path": str(schema_path),
                    },
                    {"total_timesteps": 128},
                    {"bc_samples": 10},
                )

    def test_run_ab_evaluation_raises_on_unreadable_feature_schema_file(self):
        import tempfile

        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.9, 0.1] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "total_buy_volume": 123.0,
                    "total_sell_volume": 45.0,
                },
                "meta": {"token_address": "0x6c", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0x6c", "sample_time": 110},
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / "feature_schema.json"
            schema_path.write_text("{not-json", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "failed to read feature schema"):
                m.run_ab_evaluation(
                    {"eval_samples": eval_samples},
                    {
                        "model": _FakeBuyModel(),
                        "threshold": 0.5,
                        "feature_schema_path": str(schema_path),
                    },
                    {"total_timesteps": 128},
                    {"bc_samples": 10},
                )

    def test_run_ab_evaluation_does_not_open_on_last_sample_only_signal(self):
        m = _load_module()

        class _FakeBuyModel:
            def __init__(self):
                self.calls = 0

            def predict_proba(self, X):
                self.calls += 1
                if self.calls == 1:
                    return [[0.9, 0.1] for _ in range(len(X))]
                return [[0.1, 0.9] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0x7", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.3,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 20.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0x7", "sample_time": 110},
            },
        ]

        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples},
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128},
            {"bc_samples": 10},
        )

        self.assertEqual(out["total_trades"], 0)
        self.assertEqual(out["net_return_pct"], 0.0)

    def test_run_ab_evaluation_returns_zero_metrics_when_threshold_blocks_all_entries(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.9, 0.1] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0x1", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.1,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 11.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0x1", "sample_time": 110},
            },
        ]

        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples},
            {"model": _FakeBuyModel(), "threshold": 0.95},
            {"total_timesteps": 128},
            {"bc_samples": 10},
        )

        self.assertEqual(out["total_trades"], 0)
        self.assertEqual(out["win_rate"], 0.0)
        self.assertEqual(out["net_return_pct"], 0.0)
        self.assertEqual(out["max_drawdown_pct"], 0.0)
        self.assertEqual(out["sortino_ratio"], 0.0)

    def test_run_ab_evaluation_reports_walk_forward_segments(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xwf1", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.1,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0xwf1", "sample_time": 110},
            },
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 20,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xwf2", "sample_time": 200},
            },
            {
                "features": {
                    "current_price": 0.9,
                    "launch_fee": 0.5,
                    "holder_count": 21,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0xwf2", "sample_time": 210},
            },
        ]

        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples, "walk_forward_segments": 2, "position_fraction": 0.1},
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128},
            {"bc_samples": 10},
        )

        self.assertEqual(len(out["walk_forward"]), 2)
        self.assertEqual(out["walk_forward"][0]["segment_index"], 0)
        self.assertEqual(out["walk_forward"][0]["episode_count"], 1)
        self.assertEqual(out["walk_forward"][1]["segment_index"], 1)
        self.assertEqual(out["walk_forward"][1]["episode_count"], 1)
        self.assertEqual(out["walk_forward_segment_count"], 2)
        self.assertEqual(
            out["walk_forward_worst_net_return_pct"],
            min(segment["net_return_pct"] for segment in out["walk_forward"]),
        )
        self.assertEqual(
            out["walk_forward_worst_max_drawdown_pct"],
            min(segment["max_drawdown_pct"] for segment in out["walk_forward"]),
        )
        self.assertEqual(
            out["walk_forward_min_win_rate"],
            min(segment["win_rate"] for segment in out["walk_forward"]),
        )

    def test_run_ab_evaluation_reports_rolling_validation_summary(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xwf1", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.1,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0xwf1", "sample_time": 110},
            },
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 20,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0xwf2", "sample_time": 200},
            },
            {
                "features": {
                    "current_price": 0.9,
                    "launch_fee": 0.5,
                    "holder_count": 21,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0xwf2", "sample_time": 210},
            },
        ]

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "walk_forward_segments": 2,
                "position_fraction": 0.1,
                "risk_tune_min_win_rate": 0.0,
                "preferred_max_drawdown_pct": -30.0,
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128},
            {"bc_samples": 10},
        )

        self.assertIn("rolling_validation", out)
        self.assertEqual(out["rolling_validation"]["segment_count"], 2)
        self.assertEqual(out["rolling_validation"]["min_win_rate_threshold"], 0.0)
        self.assertEqual(out["rolling_validation"]["max_drawdown_threshold_pct"], -30.0)
        self.assertEqual(len(out["rolling_validation"]["segments"]), 2)
        self.assertEqual(out["rolling_validation"]["segments"][0]["segment_index"], 0)
        self.assertIsInstance(out["rolling_validation"]["passed"], bool)

    def test_run_ab_evaluation_reports_stress_replay_scenarios(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        class _SellAllPolicy:
            def predict(self, obs, deterministic=True):
                return 3, None

        def sample(token, sample_time, price):
            return {
                "features": {
                    "current_price": price,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": token, "sample_time": sample_time},
            }

        eval_samples = [
            sample("0xstress", 100, 1.0),
            sample("0xstress", 103, 2.0),
            sample("0xstress", 110, 3.0),
        ]

        out = m.run_ab_evaluation(
            {
                "eval_samples": eval_samples,
                "position_fraction": 0.1,
                "stress_replay_scenarios": [
                    {
                        "name": "mild",
                        "entry_delay_seconds": 2,
                        "exit_delay_seconds": 2,
                        "slippage_bps": 100.0,
                        "max_open_positions": 3,
                    }
                ],
            },
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"model": _SellAllPolicy()},
            {"bc_samples": 10},
        )

        self.assertEqual(len(out["stress_replay"]), 1)
        scenario = out["stress_replay"][0]
        self.assertEqual(scenario["name"], "mild")
        self.assertEqual(scenario["entry_delay_seconds"], 2)
        self.assertEqual(scenario["exit_delay_seconds"], 2)
        self.assertEqual(scenario["max_open_positions"], 3)
        self.assertAlmostEqual(scenario["slippage_bps"], 100.0)
        self.assertEqual(scenario["total_trades"], 1)

    def test_stress_replay_default_scenarios_separate_friction_and_capacity(self):
        m = _load_module()

        scenarios = m._stress_replay_scenarios({"stress_replay": True})

        self.assertEqual(
            [scenario["name"] for scenario in scenarios],
            ["mild_friction", "harsh_friction", "mild_capacity", "harsh_execution"],
        )
        self.assertNotIn("max_open_positions", scenarios[0])
        self.assertNotIn("max_open_positions", scenarios[1])
        self.assertEqual(scenarios[0]["entry_delay_seconds"], 3)
        self.assertEqual(scenarios[1]["entry_delay_seconds"], 3)
        self.assertEqual(scenarios[2]["max_pending_entries"], 10)
        self.assertEqual(scenarios[3]["max_pending_entries"], 10)
        self.assertEqual(scenarios[3]["entry_execution_failure_rate"], 0.20)

    def test_build_eval_episodes_sorts_by_episode_start_time(self):
        m = _load_module()

        eval_samples = [
            {
                "features": {"current_price": 2.0},
                "meta": {"token_address": "0xb", "sample_time": 200},
            },
            {
                "features": {"current_price": 1.0},
                "meta": {"token_address": "0xa", "sample_time": 100},
            },
            {
                "features": {"current_price": 1.1},
                "meta": {"token_address": "0xa", "sample_time": 110},
            },
            {
                "features": {"current_price": 2.1},
                "meta": {"token_address": "0xb", "sample_time": 210},
            },
        ]

        episodes = m._build_eval_episodes(eval_samples)

        self.assertEqual([episode[0]["meta"]["token_address"] for episode in episodes], ["0xa", "0xb"])

    def test_run_ab_evaluation_non_zero_replay_metrics(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                probs = []
                for i in range(len(X)):
                    if i == 0:
                        probs.append([0.1, 0.9])
                    else:
                        probs.append([0.9, 0.1])
                return probs

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0x1", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0x1", "sample_time": 110},
            },
            {
                "features": {
                    "current_price": 0.8,
                    "launch_fee": 0.5,
                    "holder_count": 12,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0x1", "sample_time": 120},
            },
        ]

        out = m.run_ab_evaluation(
            {"eval_samples": eval_samples},
            {"model": _FakeBuyModel(), "threshold": 0.5},
            {"total_timesteps": 128},
            {"bc_samples": 10},
        )

        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["win_rate"], 1.0)
        self.assertAlmostEqual(out["net_return_pct"], 2.0, places=6)
        self.assertLessEqual(out["max_drawdown_pct"], 0.0)
        self.assertEqual(out["sortino_ratio"], 0.0)

    def test_run_ab_evaluation_raises_when_no_eval_episodes_built(self):
        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.1, 0.9] for _ in range(len(X))]

        with self.assertRaisesRegex(ValueError, "no eval episodes"):
            m.run_ab_evaluation(
                {"eval_samples": []},
                {"model": _FakeBuyModel(), "threshold": 0.5},
                {"total_timesteps": 128},
                {"bc_samples": 10},
            )

    def test_run_hybrid_training_orchestrates_train_eval_file_partitions(self):
        import json
        import tempfile

        m = _load_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            lifecycle_dir = tmp_path / "lifecycle"
            lifecycle_dir.mkdir(parents=True, exist_ok=True)

            all_files = [
                lifecycle_dir / "lifecycle_incremental_001.jsonl",
                lifecycle_dir / "lifecycle_incremental_002.jsonl",
                lifecycle_dir / "lifecycle_incremental_003.jsonl",
            ]
            for path in all_files:
                path.write_text("\n", encoding="utf-8")

            train_files = all_files[:2]
            eval_files = all_files[2:]
            eval_samples = [
                {
                    "features": {"current_price": 1.0, "total_buy_volume": 10.0, "total_sell_volume": 1.0},
                    "meta": {"token_address": "0xe1", "sample_time": 100},
                },
                {
                    "features": {"current_price": 1.1, "total_buy_volume": 1.0, "total_sell_volume": 9.0},
                    "meta": {"token_address": "0xe1", "sample_time": 110},
                },
            ]

            def _fake_eval(eval_config, buy_artifact, ppo_artifact, bc_artifact):
                self.assertEqual(eval_config.get("eval_samples"), eval_samples)
                self.assertEqual(eval_config.get("train_file_count"), 2)
                self.assertEqual(eval_config.get("eval_file_count"), 1)
                self.assertEqual(eval_config.get("overlap_token_count"), 0)
                self.assertEqual(eval_config.get("raw_overlap_token_count"), 7)
                self.assertEqual(eval_config.get("excluded_eval_token_count"), 7)
                return {
                    "total_trades": 1,
                    "win_rate": 1.0,
                    "net_return_pct": 10.0,
                    "max_drawdown_pct": -1.0,
                    "sortino_ratio": 0.5,
                    "buy_threshold": 0.5,
                    "sell_episode_count": 1,
                    "bc_samples": 10,
                    "ppo_total_timesteps": 128,
                    "train_file_count": eval_config["train_file_count"],
                    "eval_file_count": eval_config["eval_file_count"],
                    "overlap_token_count": eval_config["overlap_token_count"],
                    "raw_overlap_token_count": eval_config["raw_overlap_token_count"],
                    "excluded_eval_token_count": eval_config["excluded_eval_token_count"],
                    "pipeline_status": "ok",
                }

            with patch.object(m, "_discover_lifecycle_files", return_value=all_files), \
                 patch.object(m, "_split_lifecycle_files", return_value=(train_files, eval_files, 7)), \
                 patch.object(m, "_collect_raw_token_addresses", return_value={"0xe1"}) as mock_collect_train_tokens, \
                 patch.object(
                     m,
                     "train_buy_model",
                     side_effect=lambda cfg: {
                         "model_path": "buy_model.cbm",
                         "threshold": 0.5,
                         "threshold_path": "buy_threshold.json",
                         "feature_schema_path": "feature_schema.json",
                         "feature_names": ["current_price", "launch_fee", "holder_count", "total_buy_volume", "total_sell_volume"],
                         "model": MagicMock(),
                     },
                 ) as mock_train_buy, \
                 patch.object(m, "build_sell_env", return_value={"env": object(), "episodes": [[{}]], "episode_count": 1}), \
                 patch.object(m, "run_bc_warmstart", return_value={"weights": "bc.pt", "bc_samples": 10}), \
                 patch.object(m, "run_ppo_finetune", return_value={"policy_path": "sell_policy.zip", "total_timesteps": 128}), \
                 patch.object(m, "_load_samples", return_value=eval_samples) as mock_load_samples, \
                 patch.object(m, "run_ab_evaluation", side_effect=_fake_eval):
                result = m.run_hybrid_training({"output_dir": tmpdir, "lifecycle_dir": str(lifecycle_dir)})

            train_cfg = mock_train_buy.call_args.args[0]
            self.assertEqual(train_cfg.get("lifecycle_paths"), train_files)
            mock_collect_train_tokens.assert_called_once_with(train_files)
            self.assertEqual(mock_load_samples.call_count, 1)
            self.assertEqual(mock_load_samples.call_args.args[0].get("lifecycle_paths"), eval_files)
            self.assertEqual(mock_load_samples.call_args.args[0].get("exclude_token_addresses"), {"0xe1"})

            required = {
                "total_trades",
                "win_rate",
                "net_return_pct",
                "max_drawdown_pct",
                "sortino_ratio",
                "buy_threshold",
                "sell_episode_count",
                "bc_samples",
                "ppo_total_timesteps",
                "train_file_count",
                "eval_file_count",
                "overlap_token_count",
                "raw_overlap_token_count",
                "excluded_eval_token_count",
                "pipeline_status",
            }
            self.assertTrue(required.issubset(set(result["evaluation"].keys())))
            self.assertEqual(result["evaluation"]["train_file_count"], 2)
            self.assertEqual(result["evaluation"]["eval_file_count"], 1)
            self.assertEqual(result["evaluation"]["overlap_token_count"], 0)
            self.assertEqual(result["evaluation"]["raw_overlap_token_count"], 7)
            self.assertEqual(result["evaluation"]["excluded_eval_token_count"], 7)

            manifest = json.loads((Path(tmpdir) / "hybrid_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(required.issubset(set(manifest["evaluation"].keys())))
            self.assertEqual(manifest["evaluation"]["train_file_count"], 2)
            self.assertEqual(manifest["evaluation"]["eval_file_count"], 1)
            self.assertEqual(manifest["evaluation"]["overlap_token_count"], 0)
            self.assertEqual(manifest["evaluation"]["raw_overlap_token_count"], 7)
            self.assertEqual(manifest["evaluation"]["excluded_eval_token_count"], 7)


    def test_run_hybrid_training_respects_explicit_lifecycle_paths(self):
        import tempfile

        m = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            explicit_files = [
                Path(tmpdir) / "lifecycle_incremental_003.jsonl",
                Path(tmpdir) / "lifecycle_incremental_001.jsonl",
                Path(tmpdir) / "lifecycle_incremental_002.jsonl",
            ]
            for path in explicit_files:
                path.write_text("{}\n", encoding="utf-8")

            observed = {}

            def _fake_train_buy_model(cfg):
                observed["train_config"] = dict(cfg)
                return {
                    "model_path": "buy_model.cbm",
                    "threshold": 0.42,
                    "threshold_path": "buy_threshold.json",
                    "feature_schema_path": "feature_schema.json",
                    "feature_names": ["current_price"],
                    "model": MagicMock(),
                }

            def _fake_run_ab_evaluation(eval_config, buy_artifact, ppo_artifact, bc_artifact):
                observed["eval_config"] = dict(eval_config)
                return {
                    "total_trades": 0,
                    "win_rate": 0.0,
                    "net_return_pct": 0.0,
                    "max_drawdown_pct": 0.0,
                    "sortino_ratio": 0.0,
                    "buy_threshold": buy_artifact["threshold"],
                    "sell_episode_count": 0,
                    "bc_samples": bc_artifact["bc_samples"],
                    "ppo_total_timesteps": ppo_artifact["total_timesteps"],
                    "train_file_count": eval_config["train_file_count"],
                    "eval_file_count": eval_config["eval_file_count"],
                    "overlap_token_count": eval_config["overlap_token_count"],
                    "pipeline_status": "ok",
                }

            fake_eval_samples = [
                {"features": {"current_price": 1.0}, "meta": {"token_address": "0x1", "sample_time": 100}},
                {"features": {"current_price": 1.1}, "meta": {"token_address": "0x1", "sample_time": 110}},
            ]

            with patch.object(m, "_discover_lifecycle_files") as mock_discover, \
                 patch.object(m, "_load_samples", return_value=fake_eval_samples), \
                 patch.object(m, "train_buy_model", side_effect=_fake_train_buy_model), \
                 patch.object(m, "build_sell_env", return_value={"env": object(), "episodes": [[{}]], "episode_count": 1}), \
                 patch.object(m, "run_bc_warmstart", return_value={"weights": "bc.pt", "bc_samples": 10}), \
                 patch.object(m, "run_ppo_finetune", return_value={"policy_path": "sell_policy.zip", "total_timesteps": 128, "model": object()}), \
                 patch.object(m, "run_ab_evaluation", side_effect=_fake_run_ab_evaluation):
                m.run_hybrid_training({
                    "output_dir": tmpdir,
                    "lifecycle_paths": explicit_files,
                    "train_split_ratio": 2 / 3,
                    "min_eval_files": 1,
                })

            mock_discover.assert_not_called()
            self.assertEqual(observed["train_config"]["lifecycle_paths"], [explicit_files[1], explicit_files[2]])
            self.assertEqual(observed["eval_config"]["lifecycle_paths"], [explicit_files[0]])

    def test_run_hybrid_training_preserves_explicit_eval_samples(self):
        import tempfile

        m = _load_module()
        explicit_eval_samples = [
            {"features": {"current_price": 1.0}, "meta": {"token_address": "0x9", "sample_time": 100}},
            {"features": {"current_price": 1.1}, "meta": {"token_address": "0x9", "sample_time": 110}},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_files = [Path(tmpdir) / "lifecycle_incremental_001.jsonl", Path(tmpdir) / "lifecycle_incremental_002.jsonl"]
            for path in fake_files:
                path.write_text("{}\n", encoding="utf-8")

            def _fake_eval(eval_config, buy_artifact, ppo_artifact, bc_artifact):
                self.assertEqual(eval_config.get("eval_samples"), explicit_eval_samples)
                return {
                    "total_trades": 0,
                    "win_rate": 0.0,
                    "net_return_pct": 0.0,
                    "max_drawdown_pct": 0.0,
                    "sortino_ratio": 0.0,
                    "buy_threshold": buy_artifact["threshold"],
                    "sell_episode_count": 0,
                    "bc_samples": bc_artifact["bc_samples"],
                    "ppo_total_timesteps": ppo_artifact["total_timesteps"],
                    "train_file_count": eval_config["train_file_count"],
                    "eval_file_count": eval_config["eval_file_count"],
                    "overlap_token_count": eval_config["overlap_token_count"],
                    "pipeline_status": "ok",
                }

            with patch.object(m, "_discover_lifecycle_files", return_value=fake_files), \
                 patch.object(m, "_split_lifecycle_files", return_value=([fake_files[0]], [fake_files[1]], 0)), \
                 patch.object(m, "_load_samples") as mock_load_samples, \
                 patch.object(m, "train_buy_model", return_value={"model_path": "buy_model.cbm", "threshold": 0.5, "threshold_path": "buy_threshold.json", "feature_schema_path": "feature_schema.json", "feature_names": ["current_price"], "model": MagicMock()}), \
                 patch.object(m, "build_sell_env", return_value={"env": object(), "episodes": [[{}]], "episode_count": 1}), \
                 patch.object(m, "run_bc_warmstart", return_value={"weights": "bc.pt", "bc_samples": 10}), \
                 patch.object(m, "run_ppo_finetune", return_value={"policy_path": "sell_policy.zip", "total_timesteps": 128, "model": object()}), \
                 patch.object(m, "run_ab_evaluation", side_effect=_fake_eval):
                m.run_hybrid_training({"output_dir": tmpdir, "eval_samples": explicit_eval_samples})

            mock_load_samples.assert_not_called()

    def test_run_hybrid_training_preserves_explicit_empty_eval_samples(self):
        import tempfile

        m = _load_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_files = [Path(tmpdir) / "lifecycle_incremental_001.jsonl", Path(tmpdir) / "lifecycle_incremental_002.jsonl"]
            for path in fake_files:
                path.write_text("{}\n", encoding="utf-8")

            def _fake_eval(eval_config, buy_artifact, ppo_artifact, bc_artifact):
                self.assertEqual(eval_config.get("eval_samples"), [])
                return {
                    "total_trades": 0,
                    "win_rate": 0.0,
                    "net_return_pct": 0.0,
                    "max_drawdown_pct": 0.0,
                    "sortino_ratio": 0.0,
                    "buy_threshold": buy_artifact["threshold"],
                    "sell_episode_count": 0,
                    "bc_samples": bc_artifact["bc_samples"],
                    "ppo_total_timesteps": ppo_artifact["total_timesteps"],
                    "train_file_count": eval_config["train_file_count"],
                    "eval_file_count": eval_config["eval_file_count"],
                    "overlap_token_count": eval_config["overlap_token_count"],
                    "pipeline_status": "ok",
                }

            with patch.object(m, "_discover_lifecycle_files", return_value=fake_files), \
                 patch.object(m, "_split_lifecycle_files", return_value=([fake_files[0]], [fake_files[1]], 0)), \
                 patch.object(m, "_load_samples") as mock_load_samples, \
                 patch.object(m, "train_buy_model", return_value={"model_path": "buy_model.cbm", "threshold": 0.5, "threshold_path": "buy_threshold.json", "feature_schema_path": "feature_schema.json", "feature_names": ["current_price", "launch_fee", "holder_count", "total_buy_volume", "total_sell_volume"], "model": MagicMock()}), \
                 patch.object(m, "build_sell_env", return_value={"env": object(), "episodes": [[{}]], "episode_count": 1}), \
                 patch.object(m, "run_bc_warmstart", return_value={"weights": "bc.pt", "bc_samples": 10}), \
                 patch.object(m, "run_ppo_finetune", return_value={"policy_path": "sell_policy.zip", "total_timesteps": 128, "model": object()}), \
                 patch.object(m, "run_ab_evaluation", side_effect=_fake_eval):
                m.run_hybrid_training({"output_dir": tmpdir, "eval_samples": []})

            mock_load_samples.assert_not_called()

    def test_run_hybrid_training_defers_eval_sample_loading_until_training_succeeds(self):
        import tempfile

        m = _load_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_files = [Path(tmpdir) / "lifecycle_incremental_001.jsonl", Path(tmpdir) / "lifecycle_incremental_002.jsonl"]
            for path in fake_files:
                path.write_text("{}\n", encoding="utf-8")

            with patch.object(m, "_discover_lifecycle_files", return_value=fake_files), \
                 patch.object(m, "_split_lifecycle_files", return_value=([fake_files[0]], [fake_files[1]], 0)), \
                 patch.object(m, "_load_samples") as mock_load_samples, \
                 patch.object(m, "train_buy_model", side_effect=RuntimeError("train failed")):
                with self.assertRaisesRegex(RuntimeError, "train failed"):
                    m.run_hybrid_training({"output_dir": tmpdir})

            mock_load_samples.assert_not_called()

    def test_run_hybrid_training_calls_run_ab_evaluation_without_env_bundle(self):
        import tempfile

        m = _load_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_files = [Path(tmpdir) / "lifecycle_incremental_001.jsonl", Path(tmpdir) / "lifecycle_incremental_002.jsonl"]
            for path in fake_files:
                path.write_text("{}\n", encoding="utf-8")

            observed = {}

            def _fake_eval(eval_config, buy_artifact, ppo_artifact, bc_artifact):
                observed["args"] = (eval_config, buy_artifact, ppo_artifact, bc_artifact)
                return {
                    "total_trades": 0,
                    "win_rate": 0.0,
                    "net_return_pct": 0.0,
                    "max_drawdown_pct": 0.0,
                    "sortino_ratio": 0.0,
                    "buy_threshold": buy_artifact["threshold"],
                    "sell_episode_count": 0,
                    "bc_samples": bc_artifact["bc_samples"],
                    "ppo_total_timesteps": ppo_artifact["total_timesteps"],
                    "train_file_count": eval_config["train_file_count"],
                    "eval_file_count": eval_config["eval_file_count"],
                    "overlap_token_count": eval_config["overlap_token_count"],
                    "pipeline_status": "ok",
                }

            with patch.object(m, "_discover_lifecycle_files", return_value=fake_files), \
                 patch.object(m, "_split_lifecycle_files", return_value=([fake_files[0]], [fake_files[1]], 0)), \
                 patch.object(m, "_load_samples", return_value=[]), \
                 patch.object(m, "train_buy_model", return_value={"model_path": "buy_model.cbm", "threshold": 0.5, "threshold_path": "buy_threshold.json", "feature_schema_path": "feature_schema.json", "feature_names": ["current_price"], "model": MagicMock()}), \
                 patch.object(m, "build_sell_env", return_value={"env": object(), "episodes": [[{}]], "episode_count": 1}), \
                 patch.object(m, "run_bc_warmstart", return_value={"weights": "bc.pt", "bc_samples": 10}), \
                 patch.object(m, "run_ppo_finetune", return_value={"policy_path": "sell_policy.zip", "total_timesteps": 128, "model": object()}), \
                 patch.object(m, "run_ab_evaluation", side_effect=_fake_eval):
                m.run_hybrid_training({"output_dir": tmpdir})

            self.assertEqual(len(observed["args"]), 4)

    def test_run_hybrid_training_rewrites_threshold_after_risk_tuning(self):
        import json
        import tempfile

        m = _load_module()

        explicit_eval_samples = [
            {"features": {"current_price": 1.0}, "meta": {"token_address": "0xeval", "sample_time": 100}},
            {"features": {"current_price": 1.1}, "meta": {"token_address": "0xeval", "sample_time": 110}},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            threshold_path = Path(tmpdir) / "buy_threshold.json"
            threshold_path.write_text(json.dumps({"threshold": 0.5}), encoding="utf-8")
            fake_files = [Path(tmpdir) / "lifecycle_incremental_001.jsonl", Path(tmpdir) / "lifecycle_incremental_002.jsonl"]
            for path in fake_files:
                path.write_text("{}\n", encoding="utf-8")

            def _fake_eval(eval_config, buy_artifact, ppo_artifact, bc_artifact):
                self.assertEqual(buy_artifact["threshold"], 0.8)
                saved = json.loads(threshold_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["threshold"], 0.8)
                return {
                    "total_trades": 1,
                    "win_rate": 1.0,
                    "net_return_pct": 10.0,
                    "max_drawdown_pct": -1.0,
                    "sortino_ratio": 0.5,
                    "buy_threshold": 0.8,
                    "sell_episode_count": 1,
                    "bc_samples": bc_artifact["bc_samples"],
                    "ppo_total_timesteps": ppo_artifact["total_timesteps"],
                    "train_file_count": eval_config["train_file_count"],
                    "eval_file_count": eval_config["eval_file_count"],
                    "overlap_token_count": eval_config["overlap_token_count"],
                    "pipeline_status": "ok",
                }

            with patch.object(m, "_discover_lifecycle_files", return_value=fake_files), \
                 patch.object(m, "_split_lifecycle_files", return_value=([fake_files[0]], [fake_files[1]], 0)), \
                 patch.object(
                     m,
                     "train_buy_model",
                     return_value={
                         "model_path": "buy_model.cbm",
                         "threshold": 0.5,
                         "threshold_path": str(threshold_path),
                         "feature_schema_path": "feature_schema.json",
                         "feature_names": ["current_price"],
                         "model": MagicMock(),
                         "samples": [],
                         "calibration_samples": [],
                     },
                 ), \
                 patch.object(m, "build_sell_env", return_value={"env": object(), "episodes": [[{}]], "episode_count": 1}), \
                 patch.object(m, "run_bc_warmstart", return_value={"weights": "bc.pt", "bc_samples": 10}), \
                 patch.object(m, "run_ppo_finetune", return_value={"policy_path": "sell_policy.zip", "total_timesteps": 128, "model": object()}), \
                 patch.object(
                     m,
                     "_tune_buy_threshold_by_replay",
                     return_value={"status": "selected", "threshold": 0.8, "previous_threshold": 0.5, "replay": {"total_trades": 1}},
                 ) as mock_tune, \
                 patch.object(m, "run_ab_evaluation", side_effect=_fake_eval):
                result = m.run_hybrid_training(
                    {
                        "output_dir": tmpdir,
                        "eval_samples": explicit_eval_samples,
                        "risk_tune_buy_threshold": True,
                    }
                )

            mock_tune.assert_called_once()
            self.assertEqual(result["artifacts"]["buy_model"]["threshold"], 0.8)
            self.assertEqual(result["artifacts"]["buy_model"]["risk_tuning"]["status"], "selected")

    def test_run_hybrid_training_preserves_explicit_empty_lifecycle_paths(self):
        m = _load_module()

        with patch.object(m, "_discover_lifecycle_files") as mock_discover:
            with self.assertRaisesRegex(ValueError, "no lifecycle files found"):
                m.run_hybrid_training({"output_dir": "/tmp/out", "lifecycle_paths": []})

        mock_discover.assert_not_called()

    def test_run_hybrid_training_manifest_omits_non_serializable_ppo_model(self):
        import json
        import tempfile

        m = _load_module()

        class _FakeBuyModel:
            def predict_proba(self, X):
                return [[0.9, 0.1] for _ in range(len(X))]

        class _NonSerializablePolicy:
            pass

        eval_samples = [
            {
                "features": {
                    "current_price": 1.0,
                    "launch_fee": 0.5,
                    "holder_count": 10,
                    "total_buy_volume": 10.0,
                    "total_sell_volume": 1.0,
                },
                "meta": {"token_address": "0x8", "sample_time": 100},
            },
            {
                "features": {
                    "current_price": 1.2,
                    "launch_fee": 0.5,
                    "holder_count": 11,
                    "total_buy_volume": 1.0,
                    "total_sell_volume": 9.0,
                },
                "meta": {"token_address": "0x8", "sample_time": 110},
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_files = [Path(tmpdir) / "lifecycle_incremental_001.jsonl", Path(tmpdir) / "lifecycle_incremental_002.jsonl"]
            with patch.object(m, "_discover_lifecycle_files", return_value=fake_files), \
                 patch.object(m, "_split_lifecycle_files", return_value=(fake_files[:1], fake_files[1:], 0)), \
                 patch.object(m, "_load_samples", return_value=eval_samples), \
                 patch.object(
                     m,
                     "train_buy_model",
                     return_value={
                         "model_path": "buy_model.cbm",
                         "threshold": 0.5,
                         "threshold_path": "buy_threshold.json",
                         "feature_schema_path": "feature_schema.json",
                         "feature_names": ["current_price", "launch_fee", "holder_count", "total_buy_volume", "total_sell_volume"],
                         "model": _FakeBuyModel(),
                     },
                 ), patch.object(m, "build_sell_env", return_value={"env": object(), "episodes": [[{}]], "episode_count": 1}), patch.object(
                     m,
                     "run_bc_warmstart",
                     return_value={"weights": "bc.pt", "bc_samples": 10},
                 ), patch.object(
                     m,
                     "run_ppo_finetune",
                     return_value={"policy_path": "sell_policy.zip", "total_timesteps": 128, "model": _NonSerializablePolicy()},
                 ):
                result = m.run_hybrid_training({"output_dir": tmpdir, "eval_samples": eval_samples})

            manifest = json.loads(Path(tmpdir, "hybrid_manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["artifacts"]["sell_policy"], {"policy_path": "sell_policy.zip", "total_timesteps": 128})
        self.assertEqual(result["artifacts"]["sell_policy"], {"policy_path": "sell_policy.zip", "total_timesteps": 128})

    def test_run_hybrid_training_writes_trade_log_sidecar(self):
        import json
        import tempfile

        m = _load_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_files = [Path(tmpdir) / "lifecycle_incremental_001.jsonl", Path(tmpdir) / "lifecycle_incremental_002.jsonl"]
            for path in fake_files:
                path.write_text("{}\n", encoding="utf-8")

            evaluation = {
                "total_trades": 2,
                "win_rate": 0.5,
                "net_return_pct": 1.0,
                "max_drawdown_pct": -2.0,
                "sortino_ratio": 0.1,
                "buy_threshold": 0.8,
                "sell_episode_count": 1,
                "bc_samples": 1,
                "ppo_total_timesteps": 1,
                "train_file_count": 1,
                "eval_file_count": 1,
                "overlap_token_count": 0,
                "pipeline_status": "ok",
                "trade_log": [
                    {"token": "0x1", "return_pct": 10.0, "exit_reason": "SELL100"},
                    {"token": "0x2", "return_pct": -20.0, "exit_reason": "STOP_LOSS"},
                ],
            }

            with patch.object(m, "_discover_lifecycle_files", return_value=fake_files), \
                 patch.object(m, "_split_lifecycle_files", return_value=(fake_files[:1], fake_files[1:], 0)), \
                 patch.object(m, "_load_samples", return_value=[]), \
                 patch.object(m, "train_buy_model", return_value={"model_path": "buy_model.cbm", "threshold": 0.8, "threshold_path": "buy_threshold.json", "feature_schema_path": "feature_schema.json", "feature_names": ["current_price"], "model": MagicMock()}), \
                 patch.object(m, "build_sell_env", return_value={"env": object(), "episodes": [[{}]], "episode_count": 1}), \
                 patch.object(m, "run_bc_warmstart", return_value={"weights": "bc.pt", "bc_samples": 1}), \
                 patch.object(m, "run_ppo_finetune", return_value={"policy_path": "sell_policy.zip", "total_timesteps": 1, "model": object()}), \
                 patch.object(m, "run_ab_evaluation", return_value=evaluation):
                result = m.run_hybrid_training({"output_dir": tmpdir, "include_trade_log": True})

            manifest = json.loads(Path(tmpdir, "hybrid_manifest.json").read_text(encoding="utf-8"))
            sidecar_path = Path(result["evaluation"]["trade_log_path"])
            self.assertNotIn("trade_log", result["evaluation"])
            self.assertNotIn("trade_log", manifest["evaluation"])
            self.assertEqual(result["evaluation"]["trade_log_count"], 2)
            self.assertTrue(sidecar_path.exists())
            sidecar_rows = [json.loads(line) for line in sidecar_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(sidecar_rows), 2)
            self.assertEqual(result["evaluation"]["worst_trades"][0]["token"], "0x2")
            self.assertEqual(result["evaluation"]["exit_reason_summary"]["SELL100"]["count"], 1)
            self.assertEqual(result["evaluation"]["exit_reason_summary"]["STOP_LOSS"]["count"], 1)
            self.assertEqual(result["evaluation"]["exit_reason_summary"]["STOP_LOSS"]["mean_return_pct"], -20.0)

    def test_pipeline_reuses_dataset_builder_lifecycle_order_helper(self):
        import tempfile
        import types

        with tempfile.TemporaryDirectory() as tmpdir:
            helper_path = Path(tmpdir) / "dataset_builder_helper.py"
            helper_path.write_text(
                "calls = []\n"
                "def stable_lifecycle_order(files, *, log=None):\n"
                "    calls.append([str(x) for x in files])\n"
                "    return list(reversed(files))\n",
                encoding="utf-8",
            )
            spec = importlib.util.spec_from_file_location("dataset_builder_helper", helper_path)
            helper_module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(helper_module)

            stub_dataset_builder = types.ModuleType("src.data.dataset_builder")
            stub_dataset_builder.DatasetBuilder = object
            stub_dataset_builder.stable_lifecycle_order = helper_module.stable_lifecycle_order

            fake_buy_catboost = types.ModuleType("src.model.buy_catboost")
            fake_buy_catboost.BuyCatBoostModel = object
            fake_buy_catboost.EntryValueCatBoostModel = object

            fake_hybrid_inference = types.ModuleType("src.model.hybrid_inference")
            fake_hybrid_inference.build_feature_frame = lambda features, feature_names=None: features
            fake_hybrid_inference.coerce_action = int
            fake_hybrid_inference.load_feature_names_from_schema = lambda path: None
            fake_hybrid_inference.normalize_feature_names = lambda names, **kwargs: names

            fake_trading_env = types.ModuleType("src.rl.trading_env")
            fake_trading_env.MultiEpisodeTradingEnv = object
            fake_trading_env.build_sell_observation = lambda event: event
            fake_trading_env.sell_fraction_for_action = lambda action: float(action)

            fake_train_ppo = types.ModuleType("src.rl.train_ppo")
            fake_train_ppo.train_ppo = lambda *args, **kwargs: None

            with patch.dict(
                sys.modules,
                {
                    "src.data.dataset_builder": stub_dataset_builder,
                    "src.model.buy_catboost": fake_buy_catboost,
                    "src.model.hybrid_inference": fake_hybrid_inference,
                    "src.rl.trading_env": fake_trading_env,
                    "src.rl.train_ppo": fake_train_ppo,
                },
            ):
                m = _load_module()

            files = [Path("/tmp/lifecycle_incremental_003.jsonl"), Path("/tmp/lifecycle_incremental_001.jsonl")]
            ordered = m._stable_lifecycle_order(files)

        self.assertEqual(ordered, list(reversed(files)))
        self.assertEqual(helper_module.calls, [[str(x) for x in files]])

    def test_stable_order_prefers_filename_order(self):
        import tempfile

        m = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            f3 = tmp / "lifecycle_incremental_003.jsonl"
            f1 = tmp / "lifecycle_incremental_001.jsonl"
            f2 = tmp / "lifecycle_incremental_002.jsonl"
            for p in (f1, f2, f3):
                p.write_text("", encoding="utf-8")

            ordered = m._stable_lifecycle_order([f3, f1, f2])

        self.assertEqual(ordered, [f1, f2, f3])

    def test_stable_order_handles_timestamp_style_incremental_filenames(self):
        import tempfile

        m = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            newer = tmp / "lifecycle_incremental_20260321_123456.jsonl"
            older = tmp / "lifecycle_incremental_20260320_235959.jsonl"
            older.write_text("", encoding="utf-8")
            newer.write_text("", encoding="utf-8")

            ordered = m._stable_lifecycle_order([newer, older])

        self.assertEqual(ordered, [older, newer])

    def test_stable_order_places_incremental_part_files_after_base_file(self):
        import tempfile

        m = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            base = tmp / "lifecycle_incremental_20260406_212641.jsonl"
            part2 = tmp / "lifecycle_incremental_20260406_212641_part002.jsonl"
            part1 = tmp / "lifecycle_incremental_20260406_212641_part001.jsonl"
            newer = tmp / "lifecycle_incremental_20260407_000000.jsonl"
            for path in (newer, part2, base, part1):
                path.write_text("", encoding="utf-8")

            ordered = m._stable_lifecycle_order([newer, part2, base, part1])

        self.assertEqual(ordered, [base, part1, part2, newer])

    def test_stable_order_falls_back_to_mtime_and_logs(self):
        import os
        import tempfile

        m = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            older = tmp / "alpha.jsonl"
            newer = tmp / "beta.jsonl"
            older.write_text("", encoding="utf-8")
            newer.write_text("", encoding="utf-8")
            os.utime(older, (1000, 1000))
            os.utime(newer, (2000, 2000))

            with patch.object(m.logger, "info") as log_info:
                ordered = m._stable_lifecycle_order([newer, older])

        self.assertEqual(ordered, [older, newer])
        log_info.assert_any_call("Lifecycle ordering fallback to mtime for non-standard filenames")

    def test_stable_order_keeps_standard_files_first_and_fallbacks_non_standard_by_mtime(self):
        import os
        import tempfile

        m = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            std2 = tmp / "lifecycle_incremental_002.jsonl"
            std1 = tmp / "lifecycle_incremental_001.jsonl"
            weird_old = tmp / "alpha.jsonl"
            weird_new = tmp / "beta.jsonl"
            for p in (std1, std2, weird_old, weird_new):
                p.write_text("", encoding="utf-8")
            os.utime(weird_old, (1000, 1000))
            os.utime(weird_new, (2000, 2000))

            with patch.object(m.logger, "info") as log_info:
                ordered = m._stable_lifecycle_order([weird_new, std2, weird_old, std1])

        self.assertEqual(ordered, [std1, std2, weird_old, weird_new])
        log_info.assert_any_call("Lifecycle ordering fallback to mtime for non-standard filenames")

    def test_discover_lifecycle_files_supports_timestamp_style_snapshot_when_no_incrementals_exist(self):
        import tempfile

        m = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            older = tmp / "lifecycle_20260320_235959.jsonl"
            newer = tmp / "lifecycle_20260321_123456.jsonl"
            older.write_text("{}\n", encoding="utf-8")
            newer.write_text("{}\n", encoding="utf-8")

            discovered = m._discover_lifecycle_files(tmpdir)

        self.assertEqual(discovered, [older, newer])

    def test_discover_lifecycle_files_prefers_incremental_files_over_snapshot_duplicates(self):
        import tempfile

        m = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            inc1 = tmp / "lifecycle_incremental_001.jsonl"
            inc2 = tmp / "lifecycle_incremental_002.jsonl"
            snapshot = tmp / "lifecycle_999999.jsonl"
            inc1.write_text("{}\n", encoding="utf-8")
            inc2.write_text("{}\n", encoding="utf-8")
            snapshot.write_text("{}\n", encoding="utf-8")

            discovered = m._discover_lifecycle_files(tmpdir)

        self.assertEqual(discovered, [inc1, inc2])

    def test_discover_lifecycle_files_raises_when_no_files_found(self):
        import tempfile

        m = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, "no lifecycle files found"):
                m._discover_lifecycle_files(tmpdir)

    def test_split_lifecycle_files_raises_on_invalid_split_ratio(self):
        m = _load_module()
        files = [
            Path("/tmp/lifecycle_incremental_001.jsonl"),
            Path("/tmp/lifecycle_incremental_002.jsonl"),
        ]

        with self.assertRaisesRegex(ValueError, "train_split_ratio must be between 0 and 1"):
            m._split_lifecycle_files(files, train_split_ratio=-0.1, min_eval_files=1)

        with self.assertRaisesRegex(ValueError, "train_split_ratio must be between 0 and 1"):
            m._split_lifecycle_files(files, train_split_ratio=1.1, min_eval_files=1)

    def test_split_lifecycle_files_raises_when_no_train_files_after_split(self):
        m = _load_module()
        files = [Path("/tmp/lifecycle_incremental_001.jsonl")]
        with self.assertRaisesRegex(ValueError, "train_split_ratio must be between 0 and 1"):
            m._split_lifecycle_files(files, train_split_ratio=0.0, min_eval_files=1)

    def test_split_lifecycle_files_raises_when_eval_split_is_empty(self):
        m = _load_module()
        files = [
            Path("/tmp/lifecycle_incremental_001.jsonl"),
            Path("/tmp/lifecycle_incremental_002.jsonl"),
        ]
        with self.assertRaisesRegex(ValueError, "train_split_ratio must be between 0 and 1"):
            m._split_lifecycle_files(files, train_split_ratio=1.0, min_eval_files=1)


    def test_split_lifecycle_files_raises_when_train_eval_tokens_overlap(self):
        import json
        import tempfile

        m = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            train_path = tmp_path / "lifecycle_incremental_001.jsonl"
            eval_path = tmp_path / "lifecycle_incremental_002.jsonl"
            train_path.write_text(json.dumps({"token_address": "0xabc"}) + "\n", encoding="utf-8")
            eval_path.write_text(json.dumps({"token_address": "0xAbC"}) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "train/eval leakage detected"):
                m._split_lifecycle_files([train_path, eval_path], train_split_ratio=0.5, min_eval_files=1)

    def test_split_lifecycle_files_can_report_overlap_without_raising(self):
        import json
        import tempfile

        m = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            train_path = tmp_path / "lifecycle_incremental_001.jsonl"
            eval_path = tmp_path / "lifecycle_incremental_002.jsonl"
            train_path.write_text(json.dumps({"token_address": "0xabc"}) + "\n", encoding="utf-8")
            eval_path.write_text(json.dumps({"token_address": "0xAbC"}) + "\n", encoding="utf-8")

            train_files, eval_files, overlap = m._split_lifecycle_files(
                [train_path, eval_path],
                train_split_ratio=0.5,
                min_eval_files=1,
                enforce_no_overlap=False,
            )

        self.assertEqual(train_files, [train_path])
        self.assertEqual(eval_files, [eval_path])
        self.assertEqual(overlap, 1)

    def test_split_lifecycle_files_raises_when_multiple_train_eval_tokens_overlap(self):
        import json
        import tempfile

        m = _load_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            train_path = tmp_path / "lifecycle_incremental_001.jsonl"
            eval_path = tmp_path / "lifecycle_incremental_002.jsonl"

            train_rows = [
                {"token_address": "0xAAA"},
                {"token_address": "0xaaa"},
                {"token_address": "0xBBB"},
            ]
            eval_rows = [
                {"token_address": "0xAaA"},
                {"token_address": "0xCCC"},
            ]

            train_path.write_text("\n".join(json.dumps(r) for r in train_rows) + "\n", encoding="utf-8")
            eval_path.write_text("\n".join(json.dumps(r) for r in eval_rows) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "train/eval leakage detected"):
                m._split_lifecycle_files(
                    [train_path, eval_path],
                    train_split_ratio=0.5,
                    min_eval_files=1,
                )

    def test_sample_to_event_path_can_reach_highest_bc_action_bucket(self):
        m = _load_module()

        sample = {
            "features": {
                "current_price": 1.0,
                "launch_fee": 0.5,
                "holder_count": 10,
                "total_buy_volume": 0.0,
                "total_sell_volume": 10.0,
            },
            "meta": {"sample_time": 123},
        }

        event = m._sample_to_event(sample)
        _, actions = m._build_bc_arrays([[event]])

        self.assertLessEqual(event["sell_pressure"], 1.0)
        self.assertEqual(int(actions[0]), 3)

    def test_build_bc_arrays_reuses_rule_exit_action_thresholds(self):
        m = _load_module()
        event = {
            "mid_price": 1.0,
            "lp_depth": 1.0,
            "sell_pressure": 0.92,
            "buy_sell_ratio": 0.5,
            "holders": 10,
        }

        _, actions = m._build_bc_arrays([[event]])

        self.assertEqual(int(actions[0]), 3)

    def test_profit_path_bc_label_holds_despite_sell_pressure_when_future_upside_remains(self):
        m = _load_module()
        episode = [
            {
                "mid_price": 1.0,
                "lp_depth": 1.0,
                "sell_pressure": 0.95,
                "buy_sell_ratio": 0.1,
                "holders": 10,
                "ts": 100,
            },
            {
                "mid_price": 1.2,
                "lp_depth": 1.0,
                "sell_pressure": 0.95,
                "buy_sell_ratio": 0.1,
                "holders": 12,
                "ts": 110,
            },
            {
                "mid_price": 1.7,
                "lp_depth": 1.0,
                "sell_pressure": 0.2,
                "buy_sell_ratio": 2.0,
                "holders": 14,
                "ts": 120,
            },
        ]

        _, actions = m._build_bc_arrays(
            [episode],
            {"bc_label_mode": "profit_path", "bc_profit_path_sell_margin_pct": 0.05},
        )

        self.assertEqual([int(value) for value in actions[:2]], [0, 0])

    def test_profit_path_bc_label_sells_near_future_peak(self):
        m = _load_module()
        episode = [
            {
                "mid_price": 1.0,
                "lp_depth": 1.0,
                "sell_pressure": 0.1,
                "buy_sell_ratio": 2.0,
                "holders": 10,
                "ts": 100,
            },
            {
                "mid_price": 1.3,
                "lp_depth": 1.0,
                "sell_pressure": 0.1,
                "buy_sell_ratio": 2.0,
                "holders": 12,
                "ts": 110,
            },
            {
                "mid_price": 1.8,
                "lp_depth": 1.0,
                "sell_pressure": 0.1,
                "buy_sell_ratio": 2.0,
                "holders": 14,
                "ts": 120,
            },
            {
                "mid_price": 1.75,
                "lp_depth": 1.0,
                "sell_pressure": 0.1,
                "buy_sell_ratio": 2.0,
                "holders": 16,
                "ts": 130,
            },
        ]

        _, actions = m._build_bc_arrays(
            [episode],
            {
                "bc_label_mode": "profit_path",
                "bc_profit_path_sell_margin_pct": 0.05,
                "bc_profit_path_sell100_pct": 0.75,
            },
        )

        self.assertEqual([int(value) for value in actions[:3]], [0, 0, 3])


if __name__ == "__main__":
    unittest.main()

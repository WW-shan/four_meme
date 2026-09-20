from __future__ import annotations

import hashlib
import json
import logging
import math
import pickle
import re
from datetime import datetime, timezone
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from src.data.dataset_builder import DatasetBuilder, stable_lifecycle_order
from src.data.feature_extractor import OPTIONAL_FLOW_FEATURE_NAMES, REPLAY_CONTEXT_FEATURE_NAMES, requires_flow_features
from src.model.buy_catboost import BuyCatBoostModel, EntryValueCatBoostModel
from src.model.hybrid_inference import (
    build_feature_frame,
    coerce_action,
    load_feature_names_from_schema,
    normalize_feature_names,
)
try:
    from src.model.hybrid_inference import normalize_ignored_feature_names
except Exception:  # pragma: no cover - compatibility with older/stubbed inference modules
    def normalize_ignored_feature_names(feature_names):
        if feature_names is None:
            return []
        if isinstance(feature_names, dict):
            names = []
            for value in feature_names.values():
                names.extend(normalize_ignored_feature_names(value))
            return sorted(set(names))
        if isinstance(feature_names, (list, tuple, set)):
            return sorted({str(name) for name in feature_names})
        raise ValueError("ignored feature names must be a list or mapping when provided")
try:
    from src.model.hybrid_inference import build_feature_frame_many
except Exception:  # pragma: no cover - compatibility with older/stubbed inference modules
    def build_feature_frame_many(feature_rows, feature_names=None, ignored_feature_names=None):
        rows = list(feature_rows or [])
        if feature_names is None:
            return pd.DataFrame(rows)
        validated_rows = []
        for row in rows:
            try:
                validated = build_feature_frame(row, feature_names, ignored_feature_names)
            except TypeError:
                validated = build_feature_frame(row, feature_names)
            if hasattr(validated, "iloc"):
                validated_rows.append(validated.iloc[0].to_dict())
            else:
                validated_rows.append(validated)
        return pd.DataFrame(validated_rows, columns=feature_names)
try:
    from src.model.hybrid_inference import load_feature_schema_from_file
except Exception:  # pragma: no cover - compatibility with older/stubbed inference modules
    def load_feature_schema_from_file(schema_path):
        return {
            "feature_names": load_feature_names_from_schema(schema_path),
            "ignored_feature_names": [],
        }
from src.rl.trading_env import MultiEpisodeTradingEnv, build_sell_observation, sell_fraction_for_action
try:
    from src.rl.trading_env import canonical_sell_action
except Exception:  # pragma: no cover - compatibility with older/stubbed env modules
    def canonical_sell_action(action, *, allow_partial_exits=True):
        action_value = int(action)
        if allow_partial_exits:
            return action_value
        return 0 if action_value == 0 else 3
from src.rl.train_ppo import train_ppo

logger = logging.getLogger(__name__)

_SAMPLE_CACHE_VERSION = 3
_TRAINING_ARTIFACT_FILES = (
    "buy_model.cbm",
    "buy_threshold.json",
    "feature_schema.json",
    "entry_value_model.cbm",
    "bc.pt",
    "sell_policy.zip",
)

_FILENAME_ORDER_PATTERNS = (
    re.compile(r"^lifecycle_incremental_(?P<order>\d{8}_\d{6}|\d+)(?:_part(?P<part>\d+))?\.jsonl$"),
    re.compile(r"^lifecycle_(?P<order>\d{8}_\d{6}|\d+)\.jsonl$"),
)


def _filename_sort_key(path: Path):
    name = path.name
    for idx, pattern in enumerate(_FILENAME_ORDER_PATTERNS):
        match = pattern.match(name)
        if match:
            raw_value = match.group("order")
            normalized_value = raw_value.replace("_", "")
            part_value = match.groupdict().get("part") or "0"
            return idx, int(normalized_value), int(part_value), name
    return None


def _stable_lifecycle_order(files):
    return stable_lifecycle_order(files, log=logger)


def _discover_lifecycle_files(lifecycle_dir):
    base = Path(lifecycle_dir)
    incremental_files = sorted(base.glob("lifecycle_incremental_*.jsonl"))
    if incremental_files:
        return _stable_lifecycle_order(incremental_files)

    snapshot_files = sorted(base.glob("lifecycle_*.jsonl"))
    snapshot_files = [path for path in snapshot_files if _filename_sort_key(path) is not None and path.name.startswith("lifecycle_") and not path.name.startswith("lifecycle_incremental_")]
    if snapshot_files:
        return _stable_lifecycle_order(snapshot_files)

    raise ValueError(f"no lifecycle files found under {base}")


def _split_lifecycle_files(files, train_split_ratio, min_eval_files, *, enforce_no_overlap=True, return_token_sets=False):
    ordered = _stable_lifecycle_order(files)
    if not ordered:
        raise ValueError("no lifecycle files found")

    ratio = float(train_split_ratio)
    if ratio <= 0.0 or ratio >= 1.0:
        raise ValueError(f"train_split_ratio must be between 0 and 1 (exclusive), got {ratio}")
    required_eval = int(min_eval_files)
    if required_eval < 1:
        required_eval = 1

    train_count = int(len(ordered) * ratio)
    train_files = ordered[:train_count]
    eval_files = ordered[train_count:]

    if not train_files:
        raise ValueError("split produced no train files")
    if len(eval_files) < required_eval:
        raise ValueError(f"split produced insufficient eval files: {len(eval_files)} < {required_eval}")

    train_tokens, eval_tokens, overlap_token_count = _raw_token_split_details(train_files, eval_files)
    if enforce_no_overlap and overlap_token_count > 0:
        raise ValueError(
            f"train/eval leakage detected: overlap_token_count={overlap_token_count}; adjust lifecycle partitions before training"
        )

    if return_token_sets:
        return train_files, eval_files, overlap_token_count, train_tokens, eval_tokens

    return train_files, eval_files, overlap_token_count


def _split_lifecycle_files_three_way(
    files,
    train_split_ratio,
    validation_split_ratio,
    min_validation_files,
    min_eval_files,
    *,
    enforce_no_overlap=True,
):
    ordered = _stable_lifecycle_order(files)
    if not ordered:
        raise ValueError("no lifecycle files found")

    train_ratio = float(train_split_ratio)
    validation_ratio = float(validation_split_ratio)
    if train_ratio <= 0.0 or train_ratio >= 1.0:
        raise ValueError(f"train_split_ratio must be between 0 and 1 (exclusive), got {train_ratio}")
    if validation_ratio <= 0.0 or validation_ratio >= 1.0:
        raise ValueError(
            f"validation_split_ratio must be between 0 and 1 (exclusive), got {validation_ratio}"
        )
    if train_ratio + validation_ratio >= 1.0:
        raise ValueError(
            "train_split_ratio + validation_split_ratio must leave room for final evaluation"
        )

    required_validation = max(1, int(min_validation_files))
    required_eval = max(1, int(min_eval_files))
    train_count = max(1, int(len(ordered) * train_ratio))
    validation_count = max(required_validation, int(len(ordered) * validation_ratio))

    max_pre_eval_count = len(ordered) - required_eval
    if max_pre_eval_count < 2:
        raise ValueError(
            f"split produced insufficient files for train/validation/eval: total={len(ordered)}"
        )
    if train_count + validation_count > max_pre_eval_count:
        overflow = train_count + validation_count - max_pre_eval_count
        train_shrink = min(max(0, train_count - 1), overflow)
        train_count -= train_shrink
        overflow -= train_shrink
        validation_shrink = min(max(0, validation_count - required_validation), overflow)
        validation_count -= validation_shrink
        overflow -= validation_shrink
        if overflow > 0:
            raise ValueError(
                "split produced insufficient files for requested validation and final evaluation minimums"
            )

    eval_count = len(ordered) - train_count - validation_count
    if train_count < 1:
        raise ValueError("split produced no train files")
    if validation_count < required_validation:
        raise ValueError(
            f"split produced insufficient validation files: {validation_count} < {required_validation}"
        )
    if eval_count < required_eval:
        raise ValueError(f"split produced insufficient eval files: {eval_count} < {required_eval}")

    train_files = ordered[:train_count]
    validation_files = ordered[train_count:train_count + validation_count]
    eval_files = ordered[train_count + validation_count:]

    train_tokens = _collect_raw_token_addresses(train_files)
    validation_tokens = _collect_raw_token_addresses(validation_files)
    eval_tokens = _collect_raw_token_addresses(eval_files)
    train_validation_overlap = len(train_tokens.intersection(validation_tokens))
    train_eval_overlap = len(train_tokens.intersection(eval_tokens))
    validation_eval_overlap = len(validation_tokens.intersection(eval_tokens))
    final_overlap = len(eval_tokens.intersection(train_tokens.union(validation_tokens)))

    if enforce_no_overlap and (train_validation_overlap or train_eval_overlap or validation_eval_overlap):
        raise ValueError(
            "train/validation/eval leakage detected: "
            f"train_validation={train_validation_overlap}, "
            f"train_eval={train_eval_overlap}, validation_eval={validation_eval_overlap}"
        )

    return {
        "train_files": train_files,
        "validation_files": validation_files,
        "eval_files": eval_files,
        "raw_train_validation_overlap_count": int(train_validation_overlap),
        "raw_train_eval_overlap_count": int(train_eval_overlap),
        "raw_validation_eval_overlap_count": int(validation_eval_overlap),
        "raw_final_overlap_token_count": int(final_overlap),
        "train_raw_tokens": train_tokens,
        "validation_raw_tokens": validation_tokens,
        "eval_raw_tokens": eval_tokens,
    }


def _collect_raw_token_addresses(paths):
    addresses = set()
    for path in paths:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    payload = json.loads(line.strip())
                except Exception:
                    continue
                token_address = str(payload.get("token_address") or "").strip().lower()
                if token_address:
                    addresses.add(token_address)
    return addresses


def _raw_overlap_token_count(train_files, eval_files):
    return _raw_token_split_details(train_files, eval_files)[2]


def _raw_token_split_details(train_files, eval_files):
    train_tokens = _collect_raw_token_addresses(train_files)
    eval_tokens = _collect_raw_token_addresses(eval_files)
    return train_tokens, eval_tokens, len(train_tokens.intersection(eval_tokens))


try:
    from src.rl.warmstart_bc import train_bc
except Exception:  # pragma: no cover - fallback when torch is unavailable
    def train_bc(*args, **kwargs):  # type: ignore[no-redef]
        raise ModuleNotFoundError("torch is required to train BC warm-start policy")


def _prepare_training_rows(samples, target_label_column, target_threshold_value):
    if not samples:
        raise ValueError("no samples generated from DatasetBuilder")

    feature_rows, labels, metas = [], [], []
    missing_target_count = 0
    for sample in samples:
        sample_labels = sample.get("label", {}) or {}
        if target_label_column not in sample_labels:
            missing_target_count += 1
            continue
        label_value = float(sample_labels.get(target_label_column, 0.0))
        feature_rows.append(dict(sample.get("features", {})))
        labels.append(1 if label_value >= float(target_threshold_value) else 0)
        metas.append(dict(sample.get("meta", {})))

    if missing_target_count:
        raise ValueError(
            f"missing target label column: {target_label_column} "
            f"({missing_target_count}/{len(samples)} samples)"
        )

    if len(set(labels)) < 2:
        raise ValueError("buy target has single class; cannot train classifier")

    return feature_rows, labels, metas


def _prepare_regression_rows(samples, target_label_column):
    feature_rows, targets, metas = [], [], []
    missing_target_count = 0
    for sample in samples:
        features = sample.get("features", {})
        if not isinstance(features, dict):
            continue
        sample_labels = sample.get("label", {}) or {}
        if target_label_column not in sample_labels:
            missing_target_count += 1
            continue
        try:
            target = float(sample_labels.get(target_label_column))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(target):
            continue
        feature_rows.append(dict(features))
        targets.append(float(target))
        metas.append(dict(sample.get("meta", {})))

    if missing_target_count:
        raise ValueError(
            f"missing regression target label column: {target_label_column} "
            f"({missing_target_count}/{len(samples)} samples)"
        )

    if not feature_rows:
        raise ValueError(f"no samples with finite regression target: {target_label_column}")

    return feature_rows, targets, metas


_INVALID_FEATURE_PREFIXES = ("future_", "target_", "label_")


def _is_invalid_feature_name(name):
    value = str(name)
    return value.startswith(_INVALID_FEATURE_PREFIXES) or value in REPLAY_CONTEXT_FEATURE_NAMES


def _prune_training_feature_rows(rows, *, drop_constant=True):
    raw_feature_names = sorted({str(name) for row in rows for name in row.keys()})
    invalid_features = [name for name in raw_feature_names if _is_invalid_feature_name(name)]
    valid_feature_names = [name for name in raw_feature_names if name not in set(invalid_features)]

    frame = pd.DataFrame(rows)
    constant_features = []
    if drop_constant and valid_feature_names:
        for name in valid_feature_names:
            series = frame[name] if name in frame.columns else pd.Series([np.nan] * len(rows))
            if int(series.nunique(dropna=False)) <= 1:
                constant_features.append(name)

    constant_set = set(constant_features)
    feature_names = [name for name in valid_feature_names if name not in constant_set]
    if not feature_names:
        raise ValueError("no usable training features after pruning invalid and constant columns")

    pruned_rows = []
    for row in rows:
        pruned_rows.append({name: row.get(name) for name in feature_names})

    return pruned_rows, feature_names, {
        "invalid": invalid_features,
        "constant": constant_features,
    }


def _indices_have_two_classes(indices, labels):
    return len({int(labels[index]) for index in indices}) >= 2


def _split_samples_for_calibration(samples, labels, *, ratio=0.2, min_samples=20, random_state=42):
    sample_count = len(samples)
    all_indices = list(range(sample_count))
    split_ratio = float(ratio)
    if split_ratio <= 0.0:
        return all_indices, []
    if split_ratio >= 1.0:
        raise ValueError(f"buy_calibration_ratio must be less than 1.0, got {split_ratio}")

    groups = {}
    for idx, sample in enumerate(samples):
        token = _sample_token(sample) or f"__row_{idx}"
        groups.setdefault(token, []).append(idx)

    tokens = sorted(groups.keys())
    if len(tokens) < 2:
        return all_indices, []

    rng = np.random.default_rng(int(random_state))
    shuffled_tokens = list(tokens)
    rng.shuffle(shuffled_tokens)

    required_min_samples = max(1, int(min_samples))
    target_samples = max(required_min_samples, int(round(sample_count * split_ratio)))
    target_samples = min(target_samples, sample_count - 1)

    token_class_counts = {}
    total_class_counts = {}
    token_sample_counts = {}
    for token in shuffled_tokens:
        class_counts = {}
        indices = groups[token]
        token_sample_counts[token] = len(indices)
        for index in indices:
            label = int(labels[index])
            class_counts[label] = class_counts.get(label, 0) + 1
            total_class_counts[label] = total_class_counts.get(label, 0) + 1
        token_class_counts[token] = class_counts

    best = None
    calibration_sample_count = 0
    calibration_class_counts = {}
    for split_count, token in enumerate(shuffled_tokens[:-1], start=1):
        calibration_sample_count += int(token_sample_counts[token])
        for label, count in token_class_counts[token].items():
            calibration_class_counts[label] = calibration_class_counts.get(label, 0) + int(count)

        if calibration_sample_count < required_min_samples:
            continue
        fit_sample_count = sample_count - calibration_sample_count
        if fit_sample_count <= 0:
            continue
        if sum(1 for count in calibration_class_counts.values() if count > 0) < 2:
            continue

        fit_class_count = 0
        for label, total_count in total_class_counts.items():
            if total_count - calibration_class_counts.get(label, 0) > 0:
                fit_class_count += 1
        if fit_class_count < 2:
            continue

        distance = abs(calibration_sample_count - target_samples)
        candidate = (distance, calibration_sample_count, split_count)
        if best is None or candidate < best:
            best = candidate

    if best is None:
        return all_indices, []

    split_count = int(best[2])
    calibration_tokens = set(shuffled_tokens[:split_count])
    calibration_indices = sorted(
        index for token in calibration_tokens for index in groups[token]
    )
    fit_indices = sorted(index for token in shuffled_tokens[split_count:] for index in groups[token])
    return fit_indices, calibration_indices


def _take_indices(values, indices):
    return [values[index] for index in indices]


def _positive_probabilities(prob):
    prob_arr = np.asarray(prob, dtype=float)
    if prob_arr.ndim == 2:
        return prob_arr[:, 1]
    return prob_arr


def _coerce_float_list(raw, default=None):
    if raw is None:
        return list(default or [])
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.split(",")]
    else:
        parts = list(raw)
    values = []
    for part in parts:
        if part == "":
            continue
        try:
            values.append(float(part))
        except (TypeError, ValueError):
            continue
    return values


def _optional_runtime_probability(value, name: str):
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0.0 or number > 1.0:
        raise ValueError(f"{name} must be positive and <= 1.0")
    return number


def _optional_runtime_bool(value, name: str):
    if value is None:
        return None
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{name} must be boolean-like")
    return bool(value)


def _optional_nonnegative_finite(value, name: str):
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return number


def _optional_finite(value, name: str):
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _finite_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_unit_interval(value, name: str):
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise ValueError(f"{name} must be finite and between 0.0 and 1.0")
    return number


def _optional_signed_unit_interval(value, name: str):
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < -1.0 or number > 1.0:
        raise ValueError(f"{name} must be finite and between -1.0 and 1.0")
    return number


def _open_position_cap(value):
    if value is None:
        return None
    cap = int(value)
    return None if cap <= 0 else cap


def _threshold_classification_metrics(y_true, prob, threshold):
    y_arr = np.asarray(y_true, dtype=int)
    pos_prob = _positive_probabilities(prob)
    pred = pos_prob >= float(threshold)
    tp = int(np.sum((pred == 1) & (y_arr == 1)))
    fp = int(np.sum((pred == 1) & (y_arr == 0)))
    fn = int(np.sum((pred == 0) & (y_arr == 1)))
    pred_count = int(np.sum(pred == 1))
    positive_count = int(np.sum(y_arr == 1))
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    return {
        "sample_count": int(len(y_arr)),
        "positive_count": positive_count,
        "predicted_positive_count": pred_count,
        "precision": float(precision),
        "recall": float(recall),
    }


def _stop_loss_config_to_pct(value):
    stop_loss = float(value)
    if -1.0 <= stop_loss <= 1.0:
        return stop_loss * 100.0
    return stop_loss


def _json_cache_value(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(_json_cache_value(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_json_cache_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_cache_value(value[key]) for key in sorted(value)}
    return value


def _sample_cache_lifecycle_files(config):
    lifecycle_paths = config.get("lifecycle_paths") or []
    if lifecycle_paths:
        return [Path(path) for path in lifecycle_paths]
    return _discover_lifecycle_files(config.get("lifecycle_dir", "data/training"))


def _sample_cache_key(config, lifecycle_files):
    fixed_stake_bnb = config.get("label_fixed_stake_bnb")
    if fixed_stake_bnb is None:
        runtime_fixed_stake = config.get("fixed_stake_bnb")
        if runtime_fixed_stake is not None:
            fixed_stake_bnb = runtime_fixed_stake
        else:
            initial_equity = float(config.get("initial_equity_bnb", 1.0))
            position_fraction = float(config.get("position_fraction", 0.1))
            fixed_stake_bnb = initial_equity * position_fraction
    label_stop_loss_pct = config.get("label_stop_loss_pct")
    if label_stop_loss_pct is None:
        label_stop_loss_pct = _stop_loss_config_to_pct(config.get("stop_loss", -0.50))

    entry_age_seconds = int(config.get("max_entry_age_seconds", config.get("max_sample_age_seconds", 300)))
    hold_seconds = max(0, int(config.get("max_hold_seconds", 300)))
    dataset_max_age_seconds = int(
        config.get("dataset_max_sample_age_seconds", entry_age_seconds + hold_seconds)
    )

    file_metadata = []
    for path in lifecycle_files:
        path = Path(path)
        stat = path.stat()
        file_metadata.append(
            {
                "path": str(path.resolve()),
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
        )

    payload = {
        "version": _SAMPLE_CACHE_VERSION,
        "files": file_metadata,
        "config": {
            "sample_mode": config.get("sample_mode", "trade_event"),
            "dataset_max_sample_age_seconds": dataset_max_age_seconds,
            "future_windows": _json_cache_value(config.get("future_windows", [300])),
            "max_samples_per_token": config.get("max_samples_per_token"),
            "label_fee_bps": float(config.get("label_fee_bps", config.get("fee_bps", 0.0))),
            "label_slippage_bps": float(config.get("label_slippage_bps", config.get("slippage_bps", 0.0))),
            "label_stop_loss_pct": float(label_stop_loss_pct),
            "label_target_return_pct": float(
                config.get("label_target_return_pct", config.get("target_threshold_value", 80.0))
            ),
            "label_entry_delay_seconds": int(
                config.get("label_entry_delay_seconds", config.get("entry_delay_seconds", 0)) or 0
            ),
            "label_exit_delay_seconds": int(
                config.get("label_exit_delay_seconds", config.get("exit_delay_seconds", 0)) or 0
            ),
            "label_live_downside_penalty_weight": float(config.get("label_live_downside_penalty_weight", 0.0)),
            "label_delay_robust_entry_delay_seconds": _json_cache_value(
                config.get("label_delay_robust_entry_delay_seconds")
            ),
            "label_delay_robust_min_weight": float(config.get("label_delay_robust_min_weight", 1.0)),
            "label_fixed_stake_bnb": fixed_stake_bnb,
            "label_entry_fixed_cost_bnb": float(
                config.get("label_entry_fixed_cost_bnb", config.get("entry_fixed_cost_bnb", 0.0)) or 0.0
            ),
            "label_exit_fixed_cost_bnb": float(
                config.get("label_exit_fixed_cost_bnb", config.get("exit_fixed_cost_bnb", 0.0)) or 0.0
            ),
            "label_entry_price_protection_pct": config.get(
                "label_entry_price_protection_pct",
                config.get("entry_price_protection_pct"),
            ),
            "include_flow_features": bool(config.get("include_flow_features", False)),
            "min_entry_unique_buyers": int(config.get("min_entry_unique_buyers", 3) or 3),
            "min_entry_buy_count": int(config.get("min_entry_buy_count", 5) or 5),
            "include_token_addresses": sorted(_normalize_token_set(config.get("include_token_addresses"))),
            "exclude_token_addresses": sorted(_normalize_token_set(config.get("exclude_token_addresses"))),
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sample_cache_path(config, lifecycle_files):
    cache_dir = config.get("sample_cache_dir")
    if cache_dir in (None, False, ""):
        return None
    cache_base = Path(cache_dir)
    return cache_base / f"{_sample_cache_key(config, lifecycle_files)}.pkl"


def _artifact_checksums(output_dir):
    output_dir = Path(output_dir)
    checksums = {}
    for name in _TRAINING_ARTIFACT_FILES:
        path = output_dir / name
        if not path.exists():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        checksums[name] = digest.hexdigest()
    return checksums


def _read_sample_cache(path):
    if path is None or not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            return pickle.load(handle)
    except Exception as exc:
        logger.warning("Ignoring unreadable sample cache %s: %s", path, exc)
        return None


def _write_sample_cache(path, samples):
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("wb") as handle:
        pickle.dump(list(samples), handle, protocol=pickle.HIGHEST_PROTOCOL)
    tmp_path.replace(path)


def _load_samples(config):
    if "samples" in config:
        samples = list(config.get("samples") or [])
    else:
        lifecycle_paths = list(config.get("lifecycle_paths") or [])
        cache_path = None
        if config.get("sample_cache_dir") not in (None, False, ""):
            cache_lifecycle_paths = lifecycle_paths or _sample_cache_lifecycle_files(config)
            cache_path = _sample_cache_path(config, cache_lifecycle_paths)
            cached_samples = _read_sample_cache(cache_path)
            if cached_samples is not None:
                logger.info("Loaded %d training samples from cache %s", len(cached_samples), cache_path)
                return list(cached_samples)

        entry_age_seconds = int(config.get("max_entry_age_seconds", config.get("max_sample_age_seconds", 300)))
        hold_seconds = max(0, int(config.get("max_hold_seconds", 300)))
        dataset_max_age_seconds = int(
            config.get("dataset_max_sample_age_seconds", entry_age_seconds + hold_seconds)
        )
        fixed_stake_bnb = config.get("label_fixed_stake_bnb")
        if fixed_stake_bnb is None:
            runtime_fixed_stake = config.get("fixed_stake_bnb")
            if runtime_fixed_stake is not None:
                fixed_stake_bnb = runtime_fixed_stake
            else:
                initial_equity = float(config.get("initial_equity_bnb", 1.0))
                position_fraction = float(config.get("position_fraction", 0.1))
                fixed_stake_bnb = initial_equity * position_fraction
        label_stop_loss_pct = config.get("label_stop_loss_pct")
        if label_stop_loss_pct is None:
            label_stop_loss_pct = _stop_loss_config_to_pct(config.get("stop_loss", -0.50))
        builder = DatasetBuilder(
            lifecycle_dir=config.get("lifecycle_dir", "data/training"),
            sample_mode=config.get("sample_mode", "trade_event"),
            max_sample_age_seconds=dataset_max_age_seconds,
            future_windows=config.get("future_windows", [300]),
            max_samples_per_token=config.get("max_samples_per_token"),
            label_fee_bps=float(config.get("label_fee_bps", config.get("fee_bps", 0.0))),
            label_slippage_bps=float(config.get("label_slippage_bps", config.get("slippage_bps", 0.0))),
            label_stop_loss_pct=float(label_stop_loss_pct),
            label_target_return_pct=float(config.get("label_target_return_pct", config.get("target_threshold_value", 80.0))),
            label_entry_delay_seconds=int(
                config.get("label_entry_delay_seconds", config.get("entry_delay_seconds", 0)) or 0
            ),
            label_exit_delay_seconds=int(
                config.get("label_exit_delay_seconds", config.get("exit_delay_seconds", 0)) or 0
            ),
            label_live_downside_penalty_weight=float(config.get("label_live_downside_penalty_weight", 0.0)),
            label_delay_robust_entry_delay_seconds=config.get("label_delay_robust_entry_delay_seconds"),
            label_delay_robust_min_weight=float(config.get("label_delay_robust_min_weight", 1.0)),
            label_fixed_stake_bnb=fixed_stake_bnb,
            label_entry_fixed_cost_bnb=float(
                config.get("label_entry_fixed_cost_bnb", config.get("entry_fixed_cost_bnb", 0.0)) or 0.0
            ),
            label_exit_fixed_cost_bnb=float(
                config.get("label_exit_fixed_cost_bnb", config.get("exit_fixed_cost_bnb", 0.0)) or 0.0
            ),
            label_entry_price_protection_pct=config.get(
                "label_entry_price_protection_pct",
                config.get("entry_price_protection_pct"),
            ),
            min_entry_unique_buyers=int(config.get("min_entry_unique_buyers", 3) or 3),
            min_entry_buy_count=int(config.get("min_entry_buy_count", 5) or 5),
            include_flow_features=bool(config.get("include_flow_features", False)),
        )
        if lifecycle_paths:
            builder.load_lifecycle_paths(lifecycle_paths)
        else:
            builder.load_lifecycle_files()
        samples = builder.samples

    samples = _filter_samples_by_tokens(
        samples,
        include_tokens=config.get("include_token_addresses"),
        exclude_tokens=config.get("exclude_token_addresses"),
    )
    samples = _limit_samples_per_token(samples, config.get("max_samples_per_token"))
    if "samples" not in config:
        _write_sample_cache(cache_path, samples)
        if cache_path is not None:
            logger.info("Saved %d training samples to cache %s", len(samples), cache_path)
    return samples


def git_metadata(repo_dir="."):
    root = Path(repo_dir)

    def _run(args):
        result = subprocess.run(
            args,
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.stdout.strip()

    return {
        "commit": _run(["git", "rev-parse", "HEAD"]),
        "branch": _run(["git", "branch", "--show-current"]),
        "dirty": bool(_run(["git", "status", "--short"])),
    }


def _normalize_token_set(tokens):
    normalized = set()
    for token in tokens or []:
        value = str(token or "").strip().lower()
        if value:
            normalized.add(value)
    return normalized


def _sample_token(sample):
    return str(sample.get("meta", {}).get("token_address") or "").strip().lower()


def _sample_age_seconds(sample):
    meta = sample.get("meta", {}) or {}
    if "sample_interval" in meta:
        try:
            return int(meta.get("sample_interval") or 0)
        except Exception:
            return 0
    if "create_timestamp" in meta and "sample_time" in meta:
        try:
            return int(meta.get("sample_time") or 0) - int(meta.get("create_timestamp") or 0)
        except Exception:
            return 0
    return 0


def _max_entry_age_seconds(config):
    return int(config.get("max_entry_age_seconds", config.get("max_sample_age_seconds", 300)))


def _filter_samples_by_entry_window(samples, config):
    max_age = _max_entry_age_seconds(config)
    return [sample for sample in samples if _sample_age_seconds(sample) <= max_age]


def _filter_samples_by_tokens(samples, include_tokens=None, exclude_tokens=None):
    include = _normalize_token_set(include_tokens)
    exclude = _normalize_token_set(exclude_tokens)
    if not include and not exclude:
        return list(samples)

    filtered = []
    for sample in samples:
        token = _sample_token(sample)
        if include and token not in include:
            continue
        if exclude and token in exclude:
            continue
        filtered.append(sample)
    return filtered


def _sample_overlap_token_count(train_samples, eval_samples):
    train_tokens = {_sample_token(sample) for sample in train_samples or []}
    eval_tokens = {_sample_token(sample) for sample in eval_samples or []}
    train_tokens.discard("")
    eval_tokens.discard("")
    return len(train_tokens.intersection(eval_tokens))


def _samples_for_tokens(samples, tokens):
    normalized = _normalize_token_set(tokens)
    return [sample for sample in samples if _sample_token(sample) in normalized]


def _limit_samples_per_token(samples, max_samples_per_token=None):
    if max_samples_per_token is None:
        return list(samples)
    limit = int(max_samples_per_token)
    if limit <= 0:
        return list(samples)

    grouped = {}
    for original_index, sample in enumerate(samples):
        token = _sample_token(sample) or f"__row_{original_index}"
        grouped.setdefault(token, []).append((original_index, sample))

    limited = []
    for values in grouped.values():
        ordered = sorted(
            values,
            key=lambda item: (
                int(item[1].get("meta", {}).get("sample_time", 0) or 0),
                item[0],
            ),
        )
        if len(ordered) <= limit:
            limited.extend(ordered)
            continue

        selected_positions = sorted({int(round(pos)) for pos in np.linspace(0, len(ordered) - 1, limit)})
        limited.extend(ordered[position] for position in selected_positions)

    return [sample for _original_index, sample in sorted(limited, key=lambda item: item[0])]


def _token_balanced_sample_weights(samples):
    if not samples:
        return [], {
            "mode": "token_balanced",
            "sample_count": 0,
            "token_count": 0,
        }

    token_counts = {}
    sample_tokens = []
    for original_index, sample in enumerate(samples):
        token = _sample_token(sample) or f"__row_{original_index}"
        sample_tokens.append(token)
        token_counts[token] = token_counts.get(token, 0) + 1

    sample_count = len(samples)
    token_count = len(token_counts)
    weights = [
        float(sample_count / (token_count * token_counts[token]))
        for token in sample_tokens
    ]
    return weights, {
        "mode": "token_balanced",
        "sample_count": int(sample_count),
        "token_count": int(token_count),
        "min_weight": float(min(weights)),
        "max_weight": float(max(weights)),
    }


def _recency_decay_sample_weights(samples, half_life_hours):
    if not samples:
        return [], {
            "mode": "recency_decay",
            "sample_count": 0,
            "half_life_hours": float(half_life_hours),
        }

    half_life_seconds = max(float(half_life_hours), 1e-9) * 3600.0
    sample_times = [
        float(sample.get("meta", {}).get("sample_time", 0.0) or 0.0)
        for sample in samples
    ]
    latest_time = max(sample_times)
    raw_weights = [
        0.5 ** max(0.0, (latest_time - sample_time) / half_life_seconds)
        for sample_time in sample_times
    ]
    mean_weight = float(sum(raw_weights) / len(raw_weights)) if raw_weights else 1.0
    weights = [float(weight / mean_weight) for weight in raw_weights]
    return weights, {
        "mode": "recency_decay",
        "sample_count": int(len(samples)),
        "half_life_hours": float(half_life_hours),
        "latest_sample_time": float(latest_time),
        "min_weight": float(min(weights)),
        "max_weight": float(max(weights)),
    }


def _buy_sample_weights(samples, mode, *, recency_half_life_hours=24.0):
    normalized = str(mode or "none").strip().lower()
    if normalized in {"", "none", "off", "false"}:
        return None, {
            "mode": "none",
            "sample_count": int(len(samples)),
            "token_count": 0,
        }
    if normalized == "token_balanced":
        return _token_balanced_sample_weights(samples)
    if normalized == "recency_decay":
        return _recency_decay_sample_weights(samples, recency_half_life_hours)
    raise ValueError(f"unsupported buy_sample_weighting: {mode}")


def train_buy_model(config):
    output_dir = Path(config.get("output_dir", "data/models"))
    output_dir.mkdir(parents=True, exist_ok=True)

    samples = _load_samples(config)
    buy_samples = _filter_samples_by_entry_window(samples, config)
    buy_max_samples_per_token = config.get("buy_max_samples_per_token")
    if buy_max_samples_per_token is not None:
        buy_samples = _limit_samples_per_token(buy_samples, buy_max_samples_per_token)
    target_label_column = config.get("target_label_column", "executable_return_pct")
    target_threshold_value = float(config.get("target_threshold_value", 80.0))
    rows, labels, metas = _prepare_training_rows(
        buy_samples,
        target_label_column,
        target_threshold_value,
    )

    rows, feature_names, dropped_features = _prune_training_feature_rows(
        rows,
        drop_constant=bool(config.get("drop_constant_features", True)),
    )
    X = pd.DataFrame(rows)
    X = X.reindex(columns=feature_names)
    y = np.asarray(labels, dtype=int)

    fit_indices, calibration_indices = _split_samples_for_calibration(
        buy_samples,
        labels,
        ratio=float(config.get("buy_calibration_ratio", 0.2)),
        min_samples=int(config.get("min_calibration_samples", 20)),
        random_state=int(config.get("buy_random_state", config.get("random_state", 42))),
    )

    X_fit = X.iloc[fit_indices].reset_index(drop=True)
    y_fit = y[fit_indices]
    calibration_used = bool(calibration_indices)
    if calibration_used:
        X_threshold = X.iloc[calibration_indices].reset_index(drop=True)
        y_threshold = y[calibration_indices]
        eval_set = (X_threshold, y_threshold)
        threshold_source = "calibration"
    else:
        X_threshold = X
        y_threshold = y
        eval_set = None
        threshold_source = "train"

    buy_sample_weights, sample_weighting = _buy_sample_weights(
        buy_samples,
        config.get("buy_sample_weighting", "none"),
        recency_half_life_hours=float(config.get("buy_recency_half_life_hours", 24.0)),
    )
    fit_sample_weights = (
        None
        if buy_sample_weights is None
        else [buy_sample_weights[index] for index in fit_indices]
    )

    model = BuyCatBoostModel(
        cat_feature_names=config.get("cat_feature_names", []),
        random_state=int(config.get("buy_random_state", config.get("random_state", 42))),
        catboost_params=config.get("catboost_params"),
    )
    fit_kwargs = {"eval_set": eval_set}
    if fit_sample_weights is not None:
        fit_kwargs["sample_weight"] = fit_sample_weights
    model.fit(X_fit, y_fit, **fit_kwargs)
    proba = model.predict_proba(X_threshold)
    threshold = model.select_threshold(
        y_threshold,
        proba,
        min_precision=float(config.get("buy_min_precision", 0.50)),
        min_threshold=float(config.get("buy_min_threshold", 0.5)),
        min_predictions=int(config.get("buy_min_calibration_predictions", 20)),
    )
    threshold_metrics = _threshold_classification_metrics(y_threshold, proba, threshold)

    model_path = output_dir / "buy_model.cbm"
    model.model.save_model(str(model_path))

    threshold_path = output_dir / "buy_threshold.json"
    threshold_path.write_text(json.dumps({"threshold": float(threshold)}, ensure_ascii=False, indent=2), encoding="utf-8")

    feature_schema_path = output_dir / "feature_schema.json"
    feature_schema_path.write_text(
        json.dumps(
            {
                "feature_names": feature_names,
                "dropped_features": dropped_features,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "model_path": str(model_path),
        "threshold": float(threshold),
        "threshold_path": str(threshold_path),
        "feature_schema_path": str(feature_schema_path),
        "feature_names": feature_names,
        "dropped_features": dropped_features,
        "target_label_column": target_label_column,
        "target_threshold_value": target_threshold_value,
        "threshold_source": threshold_source,
        "sample_weighting": sample_weighting,
        "calibration": {
            "source": threshold_source,
            "sample_count": int(len(y_threshold)),
            "fit_sample_count": int(len(y_fit)),
            "token_count": int(len({_sample_token(buy_samples[index]) for index in calibration_indices})) if calibration_used else 0,
            "metrics": threshold_metrics,
        },
        "sell_training_samples": _samples_for_tokens(
            samples,
            {_sample_token(buy_samples[index]) for index in fit_indices},
        ),
        "calibration_samples": _samples_for_tokens(
            samples,
            {_sample_token(buy_samples[index]) for index in calibration_indices},
        ) if calibration_used else [],
        "model": model,
        "samples": buy_samples,
        "all_samples": samples,
        "labels": labels,
        "meta": metas,
    }


def train_entry_value_model(config, buy_artifact):
    output_dir = Path(config.get("output_dir", "data/models"))
    output_dir.mkdir(parents=True, exist_ok=True)

    samples = list(buy_artifact.get("samples") or [])
    if not samples:
        samples = _filter_samples_by_entry_window(_load_samples(config), config)

    target_label_column = config.get("entry_value_target_label_column", "live_risk_adjusted_return_pct")
    rows, targets, metas = _prepare_regression_rows(samples, target_label_column)

    feature_names = buy_artifact.get("feature_names")
    dropped_features = buy_artifact.get("dropped_features", {})
    if feature_names:
        feature_names = list(feature_names)
        X = build_feature_frame_many(rows, feature_names, dropped_features)
    else:
        rows, feature_names, dropped_features = _prune_training_feature_rows(
            rows,
            drop_constant=bool(config.get("drop_constant_features", True)),
        )
        X = pd.DataFrame(rows)
        X = X.reindex(columns=feature_names)

    y = np.asarray(targets, dtype=float)
    model = EntryValueCatBoostModel(
        cat_feature_names=config.get("cat_feature_names", []),
        random_state=int(config.get("entry_value_random_state", config.get("random_state", 42))),
        catboost_params=config.get("entry_value_catboost_params", config.get("catboost_params")),
    )
    model.fit(X, y)

    model_path = output_dir / "entry_value_model.cbm"
    model.model.save_model(str(model_path))

    return {
        "model_path": str(model_path),
        "feature_schema_path": buy_artifact.get("feature_schema_path"),
        "feature_names": feature_names,
        "dropped_features": dropped_features,
        "target_label_column": target_label_column,
        "sample_count": int(len(y)),
        "model": model,
        "meta": metas,
    }


def _sample_to_event(sample):
    f = sample.get("features", {})
    buy_vol = float(f.get("total_buy_volume", 0.0))
    sell_vol = float(f.get("total_sell_volume", 0.0))
    buy_sell_ratio = buy_vol / max(sell_vol, 1e-9)
    sell_pressure = sell_vol / max(buy_vol + sell_vol, 1e-9)
    return {
        "mid_price": float(f.get("current_price", 0.0)),
        "lp_depth": float(f.get("launch_fee", 0.0)),
        "sell_pressure": float(sell_pressure),
        "buy_sell_ratio": float(buy_sell_ratio),
        "holders": float(f.get("holder_count", 0.0)),
        "ts": int(sample.get("meta", {}).get("sample_time", 0) or 0),
    }


def build_sell_env(config, buy_artifact):
    grouped = {}
    sell_samples = buy_artifact.get("sell_training_samples") or buy_artifact.get("samples", [])
    for sample in sell_samples:
        token = str(sample.get("meta", {}).get("token_address", "")).strip().lower()
        if not token:
            continue
        grouped.setdefault(token, []).append(sample)

    episodes = []
    for token_samples in grouped.values():
        ordered = sorted(token_samples, key=lambda s: int(s.get("meta", {}).get("sample_time", 0) or 0))
        episode = [_sample_to_event(s) for s in ordered]
        if len(episode) >= 2:
            episodes.append(episode)

    if not episodes:
        raise ValueError("no sell episodes could be built from samples")

    env = MultiEpisodeTradingEnv(
        episodes,
        liquidity_floor=float(config.get("liquidity_floor", 0.05)),
        stall_steps=int(config.get("stall_steps", 3)),
        fee_bps=float(config.get("fee_bps", 0.0)),
        slippage_bps=float(config.get("slippage_bps", 0.0)),
        drawdown_penalty_weight=float(config.get("sell_drawdown_penalty_weight", 0.0)),
        hold_penalty_per_step=float(config.get("sell_hold_penalty_per_step", 0.0)),
        turnover_penalty=float(config.get("sell_turnover_penalty", 0.0)),
        allow_partial_exits=bool(config.get("allow_partial_exits", False)),
    )
    return {"env": env, "episodes": episodes, "episode_count": len(episodes)}


def _torch_save(obj, path):
    import torch
    torch.save(obj, path)


def _event_price(event):
    return float(event.get("mid_price", 0.0) or 0.0)


def _event_ts(event):
    return float(event.get("ts", 0.0) or 0.0)


def _profit_path_exit_action(episode, index, config, *, entry_price, peak_price, entry_ts):
    event = episode[index]
    price = _event_price(event)
    if entry_price <= 0.0 or price <= 0.0:
        return 0

    current_return = (price / entry_price) - 1.0
    peak_return = (peak_price / entry_price) - 1.0 if peak_price > 0.0 else current_return
    drawdown_from_peak = (price / peak_price) - 1.0 if peak_price > 0.0 else 0.0
    age_seconds = max(0.0, _event_ts(event) - float(entry_ts))

    stop_loss = float(config.get("stop_loss", -0.35))
    if current_return <= stop_loss:
        return 3

    min_hold_seconds = float(config.get("bc_profit_path_min_hold_seconds", 0.0))
    if age_seconds < min_hold_seconds:
        return 0

    trailing_start = config.get("bc_profit_path_trailing_start_pct", config.get("trailing_start_pct", 0.25))
    trailing_stop = config.get("bc_profit_path_trailing_stop_pct", config.get("trailing_stop_pct", 0.20))
    if (
        trailing_start is not None
        and trailing_stop is not None
        and peak_return >= float(trailing_start)
        and drawdown_from_peak <= -float(trailing_stop)
    ):
        return 3

    max_hold_seconds = config.get("max_hold_seconds")
    future_events = []
    for future_event in episode[index:]:
        if max_hold_seconds is not None and _event_ts(future_event) - float(entry_ts) > float(max_hold_seconds):
            break
        future_events.append(future_event)

    if not future_events:
        return 3 if current_return > 0.0 else 0

    future_returns = [
        (_event_price(future_event) / entry_price) - 1.0
        for future_event in future_events
        if _event_price(future_event) > 0.0
    ]
    if not future_returns:
        return 0

    future_best_return = max(future_returns)
    sell_margin = float(config.get("bc_profit_path_sell_margin_pct", 0.05))
    if future_best_return >= current_return + sell_margin:
        return 0

    if index >= len(episode) - 1:
        return 3

    sell100_pct = float(config.get("bc_profit_path_sell100_pct", 0.80))
    if current_return >= sell100_pct:
        return 3

    if bool(config.get("allow_partial_exits", False)):
        sell50_pct = float(config.get("bc_profit_path_sell50_pct", 0.50))
        sell25_pct = float(config.get("bc_profit_path_sell25_pct", 0.20))
        if current_return >= sell50_pct:
            return 2
        if current_return >= sell25_pct:
            return 1

    final_future_return = future_returns[-1]
    if current_return > 0.0 and final_future_return <= current_return - sell_margin:
        return 3

    return 0


def _build_bc_arrays(episodes, config=None):
    config = dict(config or {})
    label_mode = str(config.get("bc_label_mode", "sell_pressure") or "sell_pressure").strip().lower()
    obs_rows, action_rows = [], []
    for ep in episodes:
        if label_mode == "profit_path":
            entry_event = ep[0] if ep else {}
            entry_price = max(_event_price(entry_event), 1e-9)
            entry_ts = _event_ts(entry_event)
            peak_price = entry_price
            for index, event in enumerate(ep):
                price = _event_price(event)
                if price > 0.0:
                    peak_price = max(peak_price, price)
                obs_rows.append(
                    build_sell_observation(
                        event,
                        entry_price=entry_price,
                        peak_price=peak_price,
                        position_remaining=1.0,
                        entry_ts=entry_ts,
                        episode_start_ts=entry_ts,
                    )
                )
                action_rows.append(
                    _profit_path_exit_action(
                        ep,
                        index,
                        config,
                        entry_price=entry_price,
                        peak_price=peak_price,
                        entry_ts=entry_ts,
                    )
                )
            continue

        for event in ep:
            obs_rows.append(build_sell_observation(event))
            action_rows.append(_rule_exit_action(event))
    return np.asarray(obs_rows, dtype=np.float32), np.asarray(action_rows, dtype=np.int64)


def _as_torch_tensors(obs_arr, act_arr):
    import torch
    obs = torch.tensor(obs_arr, dtype=torch.float32)
    actions = torch.tensor(act_arr, dtype=torch.long)
    return obs, actions


def run_bc_warmstart(config, env_bundle):
    episodes = env_bundle.get("episodes", [])
    obs_arr, act_arr = _build_bc_arrays(episodes, config)
    if obs_arr.size == 0:
        raise ValueError("no BC samples generated from episodes")

    obs, actions = _as_torch_tensors(obs_arr, act_arr)

    state = train_bc(
        obs,
        actions,
        hidden_dim=int(config.get("bc_hidden_dim", 64)),
        epochs=int(config.get("bc_epochs", 20)),
        lr=float(config.get("bc_lr", 1e-3)),
    )

    output_dir = Path(config.get("output_dir", "data/models"))
    output_dir.mkdir(parents=True, exist_ok=True)
    weight_path = output_dir / "bc.pt"
    _torch_save(state, str(weight_path))

    return {
        "weights": str(weight_path),
        "bc_samples": int(obs_arr.shape[0]),
        "bc_label_mode": str(config.get("bc_label_mode", "sell_pressure") or "sell_pressure"),
    }


def _torch_load(path):
    import torch
    return torch.load(path, map_location="cpu")


def run_ppo_finetune(config, env_bundle, bc_artifact):
    env = env_bundle.get("env")
    if env is None:
        raise ValueError("env bundle missing env")

    bc_path = bc_artifact.get("weights")
    bc_state = _torch_load(bc_path) if bc_path else None

    model = train_ppo(
        env,
        total_timesteps=int(config.get("total_timesteps", 20000)),
        seed=int(config.get("ppo_seed", 42)),
        policy_kwargs={"net_arch": list(config.get("ppo_policy_net_arch", [128, 128]))},
        bc_state_dict=bc_state,
    )

    output_dir = Path(config.get("output_dir", "data/models"))
    output_dir.mkdir(parents=True, exist_ok=True)
    policy_path = output_dir / "sell_policy.zip"
    model.save(str(policy_path))

    return {
        "policy_path": str(policy_path),
        "total_timesteps": int(config.get("total_timesteps", 20000)),
        "model": model,
    }


def _build_eval_episodes(eval_samples):
    grouped = {}
    for sample in eval_samples:
        token = str(sample.get("meta", {}).get("token_address", "")).strip().lower()
        if not token:
            continue
        grouped.setdefault(token, []).append(sample)

    episodes = []
    for token_samples in grouped.values():
        ordered = sorted(token_samples, key=lambda s: int(s.get("meta", {}).get("sample_time", 0) or 0))
        if len(ordered) >= 2:
            episodes.append(ordered)
    return sorted(episodes, key=_episode_start_time)


def _episode_start_time(episode):
    if not episode:
        return 0
    return int(episode[0].get("meta", {}).get("sample_time", 0) or 0)


PATH_STATE_EPISODE_META_KEY = "__episode_meta__"


def _episode_token(episode):
    if not episode:
        return ""
    return str(episode[0].get("meta", {}).get("token_address", "") or "").strip().lower()


def _path_state_episode_metadata(episode):
    ordered = list(episode or [])
    if not ordered:
        return {"token": "", "sample_count": 0, "start_time": 0, "end_time": 0}
    return {
        "token": _episode_token(ordered),
        "sample_count": int(len(ordered)),
        "start_time": int(ordered[0].get("meta", {}).get("sample_time", 0) or 0),
        "end_time": int(ordered[-1].get("meta", {}).get("sample_time", 0) or 0),
    }


def _normalized_path_state_score_map(score_map):
    normalized = {}
    for key, value in dict(score_map or {}).items():
        if key == PATH_STATE_EPISODE_META_KEY:
            continue
        if isinstance(key, int):
            normalized[int(key)] = value
        elif isinstance(key, str) and key.isdigit():
            normalized[int(key)] = value
    return normalized


ACTION_POLICY_ROUTE_EPISODE_META_KEY = "__episode_meta__"


def _normalized_action_policy_route_map(route_map):
    normalized = {}
    for key, value in dict(route_map or {}).items():
        if key == ACTION_POLICY_ROUTE_EPISODE_META_KEY:
            continue
        if isinstance(key, int):
            route = value
        elif isinstance(key, str) and key.isdigit():
            route = value
        else:
            continue
        if isinstance(route, str):
            normalized[int(key)] = {"route": route, "confidence": None}
        elif isinstance(route, dict):
            normalized[int(key)] = {
                "route": str(route.get("route") or ""),
                "confidence": route.get("confidence"),
                "prob": route.get("prob"),
                "pred_return": route.get("pred_return"),
                "volume_30s": route.get("volume_30s"),
                "price_volatility": route.get("price_volatility"),
                "age_seconds": route.get("age_seconds"),
            }
    return normalized


def _validated_action_policy_routes_by_episode(episodes, route_maps, *, gate_enabled: bool):
    maps = list(route_maps or [])
    if not gate_enabled:
        if not maps:
            return [{} for _episode in episodes]
        if len(maps) < len(episodes):
            maps.extend({} for _episode in episodes[len(maps):])
        elif len(maps) > len(episodes):
            maps = maps[:len(episodes)]
        return maps

    if len(maps) != len(episodes):
        raise ValueError(
            "action_policy_routes_by_episode length mismatch: "
            f"expected {len(episodes)}, got {len(maps)}"
        )
    for episode_index, (episode, routes) in enumerate(zip(episodes, maps)):
        if not isinstance(routes, dict):
            raise ValueError(f"action_policy_routes_by_episode[{episode_index}] must be a dict")
        metadata = routes.get(ACTION_POLICY_ROUTE_EPISODE_META_KEY)
        if metadata is None:
            raise ValueError(
                "action_policy_routes_by_episode metadata missing "
                f"at episode {episode_index}"
            )
        if not isinstance(metadata, dict) or dict(metadata) != _path_state_episode_metadata(episode):
            raise ValueError(
                "action_policy_routes_by_episode metadata mismatch "
                f"at episode {episode_index}: expected {_path_state_episode_metadata(episode)}, got {metadata}"
            )
    return maps


def _validated_path_state_scores_by_episode(episodes, score_maps, *, gate_enabled: bool):
    maps = list(score_maps or [])
    if not gate_enabled:
        if not maps:
            return [{} for _episode in episodes]
        if len(maps) < len(episodes):
            maps.extend({} for _episode in episodes[len(maps):])
        elif len(maps) > len(episodes):
            maps = maps[:len(episodes)]
        return maps

    if len(maps) != len(episodes):
        raise ValueError(
            "path_state_scores_by_episode length mismatch: "
            f"expected {len(episodes)}, got {len(maps)}"
        )
    for episode_index, (episode, scores) in enumerate(zip(episodes, maps)):
        if not isinstance(scores, dict):
            raise ValueError(f"path_state_scores_by_episode[{episode_index}] must be a dict")
        metadata = scores.get(PATH_STATE_EPISODE_META_KEY)
        if metadata is None:
            raise ValueError(
                "path_state_scores_by_episode metadata missing "
                f"at episode {episode_index}"
            )
        if not isinstance(metadata, dict) or dict(metadata) != _path_state_episode_metadata(episode):
            raise ValueError(
                "path_state_scores_by_episode metadata mismatch "
                f"at episode {episode_index}: expected {_path_state_episode_metadata(episode)}, got {metadata}"
            )
    return maps


def _split_episodes_for_walk_forward(episodes, segment_count):
    count = max(0, int(segment_count))
    if count <= 1:
        return []
    ordered = sorted(episodes, key=_episode_start_time)
    if not ordered:
        return []

    segment_size = int(np.ceil(len(ordered) / count))
    segments = []
    for index in range(count):
        start = index * segment_size
        end = min(len(ordered), start + segment_size)
        segment = ordered[start:end]
        if segment:
            segments.append((index, segment))
    return segments


def _stable_unit_interval(*parts):
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _stress_replay_scenarios(config):
    configured = config.get("stress_replay_scenarios")
    if configured:
        raw_scenarios = configured
    elif bool(config.get("stress_replay", False)):
        raw_scenarios = [
            {
                "name": "mild_friction",
                "entry_delay_seconds": 3,
                "exit_delay_seconds": 3,
                "slippage_bps": 300.0,
            },
            {
                "name": "harsh_friction",
                "entry_delay_seconds": 3,
                "exit_delay_seconds": 3,
                "slippage_bps": 500.0,
                "entry_execution_failure_rate": 0.10,
                "exit_execution_failure_rate": 0.03,
            },
            {
                "name": "mild_capacity",
                "entry_delay_seconds": 3,
                "exit_delay_seconds": 3,
                "slippage_bps": 300.0,
                "max_pending_entries": 10,
            },
            {
                "name": "harsh_execution",
                "entry_delay_seconds": 3,
                "exit_delay_seconds": 3,
                "slippage_bps": 500.0,
                "entry_execution_failure_rate": 0.20,
                "exit_execution_failure_rate": 0.05,
                "max_pending_entries": 10,
            },
        ]
    else:
        return []

    scenarios = []
    for index, raw in enumerate(raw_scenarios):
        if not isinstance(raw, dict):
            raise ValueError("stress replay scenarios must be dictionaries")
        scenario = dict(raw)
        scenario["name"] = str(scenario.get("name") or f"stress_{index}")
        scenarios.append(scenario)
    return scenarios


def _rule_exit_action(event):
    sp = float(event.get("sell_pressure", 0.0))
    if sp >= 0.9:
        return 3
    if sp >= 0.8:
        return 2
    if sp >= 0.5:
        return 1
    return 0


def _choose_exit_action(
    event,
    sell_policy,
    *,
    entry_price=None,
    peak_price=None,
    position_remaining=1.0,
    entry_ts=None,
    episode_start_ts=None,
):
    if sell_policy is not None and hasattr(sell_policy, "predict"):
        obs = build_sell_observation(
            event,
            entry_price=entry_price,
            peak_price=peak_price,
            position_remaining=position_remaining,
            entry_ts=entry_ts,
            episode_start_ts=episode_start_ts,
        )
        action, _ = sell_policy.predict(obs, deterministic=True)
        return coerce_action(action)
    return _rule_exit_action(event)


def _sell_exit_reason(action, *, allow_partial_exits):
    action_value = int(action)
    if action_value == 1:
        return "SELL25" if allow_partial_exits else "SELL25_FULL_EXIT"
    if action_value == 2:
        return "SELL50" if allow_partial_exits else "SELL50_FULL_EXIT"
    if action_value == 3:
        return "SELL100"
    return "SELL"


def _max_drawdown_pct(equity_curve):
    if not equity_curve:
        return 0.0
    peak = float(equity_curve[0])
    worst_dd = 0.0
    for eq in equity_curve:
        eqf = float(eq)
        if eqf > peak:
            peak = eqf
        if peak > 0:
            dd = (eqf / peak) - 1.0
            if dd < worst_dd:
                worst_dd = dd
    return float(worst_dd * 100.0)


def _sortino_ratio_from_returns(returns):
    if not returns:
        return 0.0
    arr = np.asarray(returns, dtype=float)
    downside = arr[arr < 0.0]
    if downside.size == 0:
        return 0.0
    downside_dev = float(np.sqrt(np.mean(np.square(downside))))
    if downside_dev <= 0.0:
        return 0.0
    return float(arr.mean() / downside_dev)


def _episode_buy_probabilities(
    episodes,
    buy_model,
    *,
    feature_names=None,
    ignored_feature_names=None,
    max_entry_age_seconds=None,
):
    entry_age_limit = None if max_entry_age_seconds is None else int(max_entry_age_seconds)
    probabilities_by_episode = []

    for episode in episodes:
        candidate_indices = []
        candidate_rows = []
        for idx, sample in enumerate(episode[:-1]):
            event = _sample_to_event(sample)
            if float(event.get("mid_price", 0.0)) <= 0.0:
                continue
            if entry_age_limit is not None and _sample_age_seconds(sample) > entry_age_limit:
                continue
            candidate_indices.append(idx)
            candidate_rows.append(dict(sample.get("features", {})))

        buy_prob_by_index = {}
        if candidate_rows:
            X = build_feature_frame_many(candidate_rows, feature_names, ignored_feature_names)
            proba = buy_model.predict_proba(X)
            positive_probabilities = np.asarray(_positive_probabilities(proba), dtype=float).reshape(-1)
            for idx, probability in zip(candidate_indices, positive_probabilities):
                buy_prob_by_index[idx] = float(probability)

        probabilities_by_episode.append(buy_prob_by_index)

    return probabilities_by_episode


def _episode_entry_scores(
    episodes,
    entry_value_model,
    *,
    feature_names=None,
    ignored_feature_names=None,
    max_entry_age_seconds=None,
):
    if entry_value_model is None:
        return [{} for _episode in episodes]

    entry_age_limit = None if max_entry_age_seconds is None else int(max_entry_age_seconds)
    scores_by_episode = []

    for episode in episodes:
        candidate_indices = []
        candidate_rows = []
        for idx, sample in enumerate(episode[:-1]):
            event = _sample_to_event(sample)
            if float(event.get("mid_price", 0.0)) <= 0.0:
                continue
            if entry_age_limit is not None and _sample_age_seconds(sample) > entry_age_limit:
                continue
            candidate_indices.append(idx)
            candidate_rows.append(dict(sample.get("features", {})))

        score_by_index = {}
        if candidate_rows:
            X = build_feature_frame_many(candidate_rows, feature_names, ignored_feature_names)
            predictions = np.asarray(entry_value_model.predict(X), dtype=float).reshape(-1)
            for idx, score in zip(candidate_indices, predictions):
                score_by_index[idx] = float(score)

        scores_by_episode.append(score_by_index)

    return scores_by_episode


def _run_eval_replay(
    episodes,
    buy_model,
    threshold,
    sell_policy,
    feature_names=None,
    ignored_feature_names=None,
    stop_loss=-0.50,
    position_fraction=1.0,
    include_trade_log=False,
    trailing_start_pct=None,
    trailing_stop_pct=None,
    rug_sell_pressure=None,
    fee_bps=0.0,
    slippage_bps=0.0,
    one_entry_per_token=True,
    max_trades_per_token=None,
    max_entry_age_seconds=None,
    max_hold_seconds=None,
    min_policy_hold_seconds=0,
    max_position_fraction=None,
    allow_partial_exits=False,
    buy_probabilities_by_episode=None,
    entry_scores_by_episode=None,
    shadow_scores_by_episode=None,
    low_volume_rescue_scores_by_episode=None,
    entry_delay_seconds=0,
    exit_delay_seconds=0,
    max_open_positions=None,
    initial_equity_bnb=1.0,
    fixed_stake_bnb=None,
    entry_fixed_cost_bnb=0.0,
    exit_fixed_cost_bnb=0.0,
    entry_max_fill_wait_seconds=None,
    exit_max_fill_wait_seconds=None,
    entry_price_protection_pct=None,
    entry_execution_failure_rate=0.0,
    exit_execution_failure_rate=0.0,
    max_pending_entries=None,
    entry_ranking_mode="chronological",
    min_entry_score=None,
    min_entry_volume_30s=None,
    min_entry_price_volatility=None,
    buy_near_threshold_min_prob=None,
    buy_near_min_pred_return=None,
    buy_near_min_entry_volume_30s=None,
    buy_near_min_entry_price_volatility=None,
    buy_near_min_age_seconds=None,
    buy_primary_score_rescue_min_prob=None,
    buy_primary_score_rescue_min_pred_return=None,
    buy_primary_score_rescue_min_entry_volume_30s=None,
    buy_primary_score_rescue_min_entry_price_volatility=None,
    buy_primary_score_rescue_min_age_seconds=None,
    buy_low_volume_rescue_min_prob=None,
    buy_low_volume_rescue_min_entry_volume_30s=None,
    buy_low_volume_rescue_max_entry_volume_30s=None,
    buy_low_volume_rescue_min_entry_price_volatility=None,
    buy_low_volume_rescue_max_age_seconds=None,
    buy_low_volume_rescue_take_profit_pct=None,
    buy_low_volume_rescue_min_action_score=None,
    buy_quick_profit_overlay_min_prob=None,
    buy_quick_profit_overlay_min_pred_return=None,
    buy_quick_profit_overlay_max_pred_return=None,
    buy_quick_profit_overlay_min_entry_volume_30s=None,
    buy_quick_profit_overlay_min_entry_price_volatility=None,
    buy_quick_profit_overlay_min_interval_regularity=None,
    buy_quick_profit_overlay_max_age_seconds=None,
    buy_quick_profit_overlay_take_profit_pct=None,
    buy_quick_profit_overlay_max_hold_seconds=None,
    buy_quick_profit_overlay_min_total_buys=None,
    buy_quick_profit_overlay_min_flow_event_count_30s=None,
    buy_quick_profit_overlay_max_buy_sell_overlap_ratio_60s=None,
    buy_quick_profit_overlay_max_recent_seller_reentry_ratio_30s=None,
    buy_quick_profit_overlay_confirmation_delay_seconds=None,
    buy_quick_profit_overlay_max_confirmation_drawdown_pct=None,
    buy_quick_profit_overlay_max_confirmation_chase_pct=None,
    buy_shadow_meta_gate_min_prob=None,
    buy_shadow_meta_gate_max_entry_score=None,
    buy_shadow_meta_gate_min_entry_volume_30s=None,
    buy_shadow_meta_gate_min_entry_price_volatility=None,
    buy_shadow_meta_gate_max_age_seconds=None,
    buy_shadow_meta_gate_min_score=None,
    path_state_scores_by_episode=None,
    buy_path_state_meta_gate_min_score=None,
    action_policy_routes_by_episode=None,
    buy_action_policy_router_min_confidence=None,
    buy_action_policy_router_min_prob=None,
    buy_action_policy_router_max_pred_return=None,
    buy_action_policy_router_skip_passthrough=None,
    buy_action_policy_continue_hold_activation_pct=None,
    buy_action_policy_continue_hold_take_profit_pct=None,
    buy_action_policy_continue_hold_release_pct=None,
    buy_flow_activation_min_prob=None,
    buy_flow_activation_min_pred_return=None,
    buy_flow_activation_max_age_seconds=None,
    buy_flow_activation_lookback_seconds=None,
    buy_flow_activation_min_volume_ramp_ratio=None,
    buy_flow_activation_min_volume_ramp_delta=None,
    buy_flow_activation_min_pred_return_delta=None,
    buy_flow_activation_min_price_volatility_delta=None,
    buy_flow_activation_min_current_volume_30s=None,
    buy_dead_flow_exit_min_hold_seconds=None,
    buy_dead_flow_exit_max_mfe_pct=None,
    buy_flow_abstention_min_prob=None,
    buy_flow_abstention_max_age_seconds=None,
    buy_flow_abstention_min_entry_volume_30s=None,
    buy_flow_abstention_min_entry_price_volatility=None,
    buy_flow_abstention_max_buy_sell_ratio_30s=None,
    buy_flow_abstention_min_sell_pressure_30s=None,
    buy_flow_abstention_max_signed_imbalance_30s=None,
    buy_flow_abstention_max_buy_sell_ratio_60s=None,
    buy_flow_abstention_min_sell_pressure_60s=None,
    buy_flow_abstention_max_signed_imbalance_60s=None,
    buy_flow_abstention_min_buy_sell_overlap_ratio_60s=None,
    buy_flow_abstention_min_event_count_10s=None,
    buy_flow_abstention_min_toxic_entry_volume_30s=None,
    profit_lock_take_profit_pct=None,
    profit_lock_max_hold_seconds=None,
    buy_late_pump_veto_min_age_seconds=None,
    buy_late_pump_veto_extension_window_seconds=None,
    buy_late_pump_veto_min_price_extension_pct=None,
    buy_late_pump_veto_min_drawdown_from_peak_pct=None,
    buy_late_pump_veto_min_entry_volume_30s=None,
    buy_late_pump_veto_min_entry_price_volatility=None,
    buy_entry_slippage_risk_veto_min_age_seconds=None,
    buy_entry_slippage_risk_veto_extension_window_seconds=None,
    buy_entry_slippage_risk_veto_min_price_extension_pct=None,
    buy_entry_slippage_risk_veto_min_drawdown_from_peak_pct=None,
    buy_entry_slippage_risk_veto_min_recent_jump_pct=None,
    buy_entry_slippage_risk_veto_min_entry_volume_30s=None,
    buy_entry_slippage_risk_veto_min_entry_price_volatility=None,
    buy_dead_bounce_veto_max_age_seconds=None,
    buy_dead_bounce_veto_min_peak_drawdown_pct=None,
    buy_dead_bounce_veto_min_creator_sell_volume_bnb=None,
    buy_dead_bounce_veto_max_buy_pressure=None,
    buy_dead_bounce_veto_min_entry_volume_30s=None,
    buy_dead_bounce_veto_min_entry_price_volatility=None,
):
    initial_equity = max(1e-12, float(initial_equity_bnb or 1.0))
    episode_count = int(len(episodes or []))
    cash = initial_equity
    positions = {}
    latest_prices = {}
    latest_sample_times = {}
    equity_curve = [initial_equity]
    step_returns = []
    trade_returns = []
    trade_log = []
    stake_fraction = max(0.0, min(1.0, float(position_fraction)))
    max_stake_fraction = None if max_position_fraction is None else max(0.0, float(max_position_fraction))
    fixed_stake = None if fixed_stake_bnb is None else max(0.0, float(fixed_stake_bnb))
    stake_mode = "fixed_bnb" if fixed_stake is not None else "fraction"
    entry_fixed_cost = max(0.0, float(entry_fixed_cost_bnb or 0.0))
    exit_fixed_cost = max(0.0, float(exit_fixed_cost_bnb or 0.0))
    fee_rate = max(0.0, float(fee_bps)) / 10000.0
    slippage_rate = max(0.0, float(slippage_bps)) / 10000.0
    max_entries_per_token = None
    if max_trades_per_token is not None:
        max_entries_per_token = max(0, int(max_trades_per_token))
    entry_age_limit = None if max_entry_age_seconds is None else int(max_entry_age_seconds)
    hold_time_limit = None if max_hold_seconds is None else int(max_hold_seconds)
    policy_hold_floor = max(0, int(min_policy_hold_seconds or 0))
    entry_delay = max(0, int(entry_delay_seconds or 0))
    exit_delay = max(0, int(exit_delay_seconds or 0))
    open_position_cap = _open_position_cap(max_open_positions)
    entry_max_fill_wait = None if entry_max_fill_wait_seconds is None else max(0, int(entry_max_fill_wait_seconds))
    exit_max_fill_wait = None if exit_max_fill_wait_seconds is None else max(0, int(exit_max_fill_wait_seconds))
    entry_failure_rate = max(0.0, min(1.0, float(entry_execution_failure_rate or 0.0)))
    exit_failure_rate = max(0.0, min(1.0, float(exit_execution_failure_rate or 0.0)))
    pending_entry_cap = None if max_pending_entries is None else max(0, int(max_pending_entries))
    entry_ranking_mode = str(entry_ranking_mode or "chronological").strip().lower()
    if entry_ranking_mode not in {"chronological", "buy_prob", "entry_value"}:
        raise ValueError(f"unsupported entry_ranking_mode: {entry_ranking_mode}")
    entry_score_floor = None if min_entry_score is None else float(min_entry_score)
    entry_volume_30s_floor = None if min_entry_volume_30s is None else max(0.0, float(min_entry_volume_30s))
    entry_price_volatility_floor = None if min_entry_price_volatility is None else max(0.0, float(min_entry_price_volatility))
    near_threshold_prob_floor = None if buy_near_threshold_min_prob is None else max(0.0, min(1.0, float(buy_near_threshold_min_prob)))
    near_threshold_score_floor = None if buy_near_min_pred_return is None else max(0.0, float(buy_near_min_pred_return))
    near_threshold_volume_30s_floor = None if buy_near_min_entry_volume_30s is None else max(0.0, float(buy_near_min_entry_volume_30s))
    near_threshold_price_volatility_floor = (
        None
        if buy_near_min_entry_price_volatility is None
        else max(0.0, float(buy_near_min_entry_price_volatility))
    )
    near_threshold_min_age = None if buy_near_min_age_seconds is None else max(0.0, float(buy_near_min_age_seconds))
    primary_score_rescue_prob_floor = _optional_runtime_probability(
        buy_primary_score_rescue_min_prob,
        "buy_primary_score_rescue_min_prob",
    )
    primary_score_rescue_score_floor = (
        None
        if buy_primary_score_rescue_min_pred_return is None
        else max(0.0, float(buy_primary_score_rescue_min_pred_return))
    )
    primary_score_rescue_volume_30s_floor = (
        None
        if buy_primary_score_rescue_min_entry_volume_30s is None
        else max(0.0, float(buy_primary_score_rescue_min_entry_volume_30s))
    )
    primary_score_rescue_price_volatility_floor = (
        None
        if buy_primary_score_rescue_min_entry_price_volatility is None
        else max(0.0, float(buy_primary_score_rescue_min_entry_price_volatility))
    )
    primary_score_rescue_min_age = (
        None
        if buy_primary_score_rescue_min_age_seconds is None
        else max(0.0, float(buy_primary_score_rescue_min_age_seconds))
    )
    primary_score_rescue_enabled = (
        primary_score_rescue_prob_floor is not None
        and primary_score_rescue_score_floor is not None
    )
    low_volume_rescue_prob_floor = _optional_runtime_probability(
        buy_low_volume_rescue_min_prob,
        "buy_low_volume_rescue_min_prob",
    )
    low_volume_rescue_volume_floor = _optional_nonnegative_finite(
        buy_low_volume_rescue_min_entry_volume_30s,
        "buy_low_volume_rescue_min_entry_volume_30s",
    )
    low_volume_rescue_volume_ceiling = _optional_nonnegative_finite(
        buy_low_volume_rescue_max_entry_volume_30s,
        "buy_low_volume_rescue_max_entry_volume_30s",
    )
    low_volume_rescue_price_volatility_floor = _optional_nonnegative_finite(
        buy_low_volume_rescue_min_entry_price_volatility,
        "buy_low_volume_rescue_min_entry_price_volatility",
    )
    low_volume_rescue_age_ceiling = _optional_nonnegative_finite(
        buy_low_volume_rescue_max_age_seconds,
        "buy_low_volume_rescue_max_age_seconds",
    )
    low_volume_rescue_take_profit = _optional_nonnegative_finite(
        buy_low_volume_rescue_take_profit_pct,
        "buy_low_volume_rescue_take_profit_pct",
    )
    low_volume_rescue_action_score_floor = _optional_nonnegative_finite(
        buy_low_volume_rescue_min_action_score,
        "buy_low_volume_rescue_min_action_score",
    )
    if (
        low_volume_rescue_volume_floor is not None
        and low_volume_rescue_volume_ceiling is not None
        and low_volume_rescue_volume_floor > low_volume_rescue_volume_ceiling
    ):
        raise ValueError(
            "buy_low_volume_rescue_min_entry_volume_30s must be <= "
            "buy_low_volume_rescue_max_entry_volume_30s"
        )
    low_volume_rescue_enabled = low_volume_rescue_prob_floor is not None
    quick_profit_overlay_prob_floor = _optional_runtime_probability(
        buy_quick_profit_overlay_min_prob,
        "buy_quick_profit_overlay_min_prob",
    )
    quick_profit_overlay_score_floor = _optional_finite(
        buy_quick_profit_overlay_min_pred_return,
        "buy_quick_profit_overlay_min_pred_return",
    )
    quick_profit_overlay_score_ceiling = _optional_nonnegative_finite(
        buy_quick_profit_overlay_max_pred_return,
        "buy_quick_profit_overlay_max_pred_return",
    )
    quick_profit_overlay_volume_floor = _optional_nonnegative_finite(
        buy_quick_profit_overlay_min_entry_volume_30s,
        "buy_quick_profit_overlay_min_entry_volume_30s",
    )
    quick_profit_overlay_price_volatility_floor = _optional_nonnegative_finite(
        buy_quick_profit_overlay_min_entry_price_volatility,
        "buy_quick_profit_overlay_min_entry_price_volatility",
    )
    quick_profit_overlay_interval_regularity_floor = _optional_nonnegative_finite(
        buy_quick_profit_overlay_min_interval_regularity,
        "buy_quick_profit_overlay_min_interval_regularity",
    )
    quick_profit_overlay_age_ceiling = _optional_nonnegative_finite(
        buy_quick_profit_overlay_max_age_seconds,
        "buy_quick_profit_overlay_max_age_seconds",
    )
    quick_profit_overlay_take_profit = _optional_nonnegative_finite(
        buy_quick_profit_overlay_take_profit_pct,
        "buy_quick_profit_overlay_take_profit_pct",
    )
    quick_profit_overlay_max_hold = _optional_nonnegative_finite(
        buy_quick_profit_overlay_max_hold_seconds,
        "buy_quick_profit_overlay_max_hold_seconds",
    )
    quick_profit_overlay_total_buys_floor = _optional_nonnegative_finite(
        buy_quick_profit_overlay_min_total_buys,
        "buy_quick_profit_overlay_min_total_buys",
    )
    quick_profit_overlay_flow_event_floor = _optional_nonnegative_finite(
        buy_quick_profit_overlay_min_flow_event_count_30s,
        "buy_quick_profit_overlay_min_flow_event_count_30s",
    )
    quick_profit_overlay_overlap_ceiling = _optional_unit_interval(
        buy_quick_profit_overlay_max_buy_sell_overlap_ratio_60s,
        "buy_quick_profit_overlay_max_buy_sell_overlap_ratio_60s",
    )
    quick_profit_overlay_reentry_ceiling = _optional_unit_interval(
        buy_quick_profit_overlay_max_recent_seller_reentry_ratio_30s,
        "buy_quick_profit_overlay_max_recent_seller_reentry_ratio_30s",
    )
    quick_profit_overlay_confirmation_delay = _optional_nonnegative_finite(
        buy_quick_profit_overlay_confirmation_delay_seconds,
        "buy_quick_profit_overlay_confirmation_delay_seconds",
    )
    quick_profit_overlay_confirmation_delay = (
        0
        if quick_profit_overlay_confirmation_delay is None
        else int(quick_profit_overlay_confirmation_delay)
    )
    quick_profit_overlay_confirmation_drawdown_ceiling = _optional_nonnegative_finite(
        buy_quick_profit_overlay_max_confirmation_drawdown_pct,
        "buy_quick_profit_overlay_max_confirmation_drawdown_pct",
    )
    quick_profit_overlay_confirmation_chase_ceiling = _optional_nonnegative_finite(
        buy_quick_profit_overlay_max_confirmation_chase_pct,
        "buy_quick_profit_overlay_max_confirmation_chase_pct",
    )
    profit_lock_take_profit = _optional_nonnegative_finite(
        profit_lock_take_profit_pct,
        "profit_lock_take_profit_pct",
    )
    profit_lock_max_hold = _optional_nonnegative_finite(
        profit_lock_max_hold_seconds,
        "profit_lock_max_hold_seconds",
    )
    if (profit_lock_take_profit is None) != (profit_lock_max_hold is None):
        raise ValueError(
            "profit_lock_take_profit_pct and profit_lock_max_hold_seconds must be provided together"
        )
    if (
        quick_profit_overlay_score_floor is not None
        and quick_profit_overlay_score_ceiling is not None
        and quick_profit_overlay_score_floor > quick_profit_overlay_score_ceiling
    ):
        raise ValueError(
            "buy_quick_profit_overlay_min_pred_return must be <= "
            "buy_quick_profit_overlay_max_pred_return"
        )
    quick_profit_overlay_enabled = quick_profit_overlay_prob_floor is not None
    shadow_meta_gate_prob_floor = _optional_runtime_probability(
        buy_shadow_meta_gate_min_prob,
        "buy_shadow_meta_gate_min_prob",
    )
    shadow_meta_gate_max_entry_score = _optional_nonnegative_finite(
        buy_shadow_meta_gate_max_entry_score,
        "buy_shadow_meta_gate_max_entry_score",
    )
    shadow_meta_gate_volume_30s_floor = _optional_nonnegative_finite(
        buy_shadow_meta_gate_min_entry_volume_30s,
        "buy_shadow_meta_gate_min_entry_volume_30s",
    )
    shadow_meta_gate_price_volatility_floor = _optional_nonnegative_finite(
        buy_shadow_meta_gate_min_entry_price_volatility,
        "buy_shadow_meta_gate_min_entry_price_volatility",
    )
    shadow_meta_gate_age_ceiling = _optional_nonnegative_finite(
        buy_shadow_meta_gate_max_age_seconds,
        "buy_shadow_meta_gate_max_age_seconds",
    )
    shadow_meta_gate_score_floor = _optional_nonnegative_finite(
        buy_shadow_meta_gate_min_score,
        "buy_shadow_meta_gate_min_score",
    )
    shadow_meta_gate_enabled = (
        shadow_meta_gate_prob_floor is not None
        and shadow_meta_gate_max_entry_score is not None
        and shadow_meta_gate_volume_30s_floor is not None
        and shadow_meta_gate_price_volatility_floor is not None
        and shadow_meta_gate_age_ceiling is not None
        and shadow_meta_gate_score_floor is not None
    )
    path_state_meta_gate_score_floor = _optional_nonnegative_finite(
        buy_path_state_meta_gate_min_score,
        "buy_path_state_meta_gate_min_score",
    )
    path_state_meta_gate_enabled = path_state_meta_gate_score_floor is not None
    action_policy_router_confidence_floor = _optional_runtime_probability(
        buy_action_policy_router_min_confidence,
        "buy_action_policy_router_min_confidence",
    )
    action_policy_router_prob_floor = _optional_runtime_probability(
        buy_action_policy_router_min_prob,
        "buy_action_policy_router_min_prob",
    )
    action_policy_router_score_ceiling = _optional_nonnegative_finite(
        buy_action_policy_router_max_pred_return,
        "buy_action_policy_router_max_pred_return",
    )
    action_policy_router_enabled = action_policy_router_confidence_floor is not None
    action_policy_router_skip_passthrough = bool(
        _optional_runtime_bool(
            buy_action_policy_router_skip_passthrough,
            "buy_action_policy_router_skip_passthrough",
        )
    )
    action_policy_continue_hold_take_profit = _optional_nonnegative_finite(
        buy_action_policy_continue_hold_take_profit_pct,
        "buy_action_policy_continue_hold_take_profit_pct",
    )
    action_policy_continue_hold_release = _optional_nonnegative_finite(
        buy_action_policy_continue_hold_release_pct,
        "buy_action_policy_continue_hold_release_pct",
    )
    action_policy_continue_hold_activation = _optional_nonnegative_finite(
        buy_action_policy_continue_hold_activation_pct,
        "buy_action_policy_continue_hold_activation_pct",
    )
    flow_activation_prob_floor = _optional_runtime_probability(
        buy_flow_activation_min_prob,
        "buy_flow_activation_min_prob",
    )
    flow_activation_score_floor = _optional_nonnegative_finite(
        buy_flow_activation_min_pred_return,
        "buy_flow_activation_min_pred_return",
    )
    flow_activation_age_ceiling = _optional_nonnegative_finite(
        buy_flow_activation_max_age_seconds,
        "buy_flow_activation_max_age_seconds",
    )
    flow_activation_lookback = _optional_nonnegative_finite(
        buy_flow_activation_lookback_seconds,
        "buy_flow_activation_lookback_seconds",
    )
    flow_activation_volume_ramp_ratio_floor = _optional_nonnegative_finite(
        buy_flow_activation_min_volume_ramp_ratio,
        "buy_flow_activation_min_volume_ramp_ratio",
    )
    flow_activation_volume_ramp_delta_floor = _optional_nonnegative_finite(
        buy_flow_activation_min_volume_ramp_delta,
        "buy_flow_activation_min_volume_ramp_delta",
    )
    flow_activation_pred_return_delta_floor = _optional_nonnegative_finite(
        buy_flow_activation_min_pred_return_delta,
        "buy_flow_activation_min_pred_return_delta",
    )
    flow_activation_price_volatility_delta_floor = _optional_nonnegative_finite(
        buy_flow_activation_min_price_volatility_delta,
        "buy_flow_activation_min_price_volatility_delta",
    )
    flow_activation_current_volume_floor = _optional_nonnegative_finite(
        buy_flow_activation_min_current_volume_30s,
        "buy_flow_activation_min_current_volume_30s",
    )
    dead_flow_exit_min_hold = _optional_nonnegative_finite(
        buy_dead_flow_exit_min_hold_seconds,
        "buy_dead_flow_exit_min_hold_seconds",
    )
    dead_flow_exit_max_mfe = _optional_nonnegative_finite(
        buy_dead_flow_exit_max_mfe_pct,
        "buy_dead_flow_exit_max_mfe_pct",
    )
    flow_activation_enabled = flow_activation_prob_floor is not None
    dead_flow_exit_enabled = (
        dead_flow_exit_min_hold is not None
        and dead_flow_exit_max_mfe is not None
    )
    flow_abstention_prob_floor = _optional_runtime_probability(
        buy_flow_abstention_min_prob,
        "buy_flow_abstention_min_prob",
    )
    flow_abstention_age_ceiling = _optional_nonnegative_finite(
        buy_flow_abstention_max_age_seconds,
        "buy_flow_abstention_max_age_seconds",
    )
    flow_abstention_volume_floor = _optional_nonnegative_finite(
        buy_flow_abstention_min_entry_volume_30s,
        "buy_flow_abstention_min_entry_volume_30s",
    )
    flow_abstention_price_volatility_floor = _optional_nonnegative_finite(
        buy_flow_abstention_min_entry_price_volatility,
        "buy_flow_abstention_min_entry_price_volatility",
    )
    flow_abstention_buy_sell_ratio_ceiling = _optional_nonnegative_finite(
        buy_flow_abstention_max_buy_sell_ratio_30s,
        "buy_flow_abstention_max_buy_sell_ratio_30s",
    )
    flow_abstention_sell_pressure_floor = _optional_unit_interval(
        buy_flow_abstention_min_sell_pressure_30s,
        "buy_flow_abstention_min_sell_pressure_30s",
    )
    flow_abstention_signed_imbalance_ceiling = _optional_signed_unit_interval(
        buy_flow_abstention_max_signed_imbalance_30s,
        "buy_flow_abstention_max_signed_imbalance_30s",
    )
    flow_abstention_buy_sell_ratio_60s_ceiling = _optional_nonnegative_finite(
        buy_flow_abstention_max_buy_sell_ratio_60s,
        "buy_flow_abstention_max_buy_sell_ratio_60s",
    )
    flow_abstention_sell_pressure_60s_floor = _optional_unit_interval(
        buy_flow_abstention_min_sell_pressure_60s,
        "buy_flow_abstention_min_sell_pressure_60s",
    )
    flow_abstention_signed_imbalance_60s_ceiling = _optional_signed_unit_interval(
        buy_flow_abstention_max_signed_imbalance_60s,
        "buy_flow_abstention_max_signed_imbalance_60s",
    )
    flow_abstention_overlap_60s_floor = _optional_unit_interval(
        buy_flow_abstention_min_buy_sell_overlap_ratio_60s,
        "buy_flow_abstention_min_buy_sell_overlap_ratio_60s",
    )
    flow_abstention_event_count_10s_floor = _optional_nonnegative_finite(
        buy_flow_abstention_min_event_count_10s,
        "buy_flow_abstention_min_event_count_10s",
    )
    flow_abstention_toxic_volume_floor = _optional_nonnegative_finite(
        buy_flow_abstention_min_toxic_entry_volume_30s,
        "buy_flow_abstention_min_toxic_entry_volume_30s",
    )
    flow_abstention_enabled = (
        flow_abstention_prob_floor is not None
        and (
            flow_abstention_buy_sell_ratio_ceiling is not None
            or flow_abstention_sell_pressure_floor is not None
            or flow_abstention_signed_imbalance_ceiling is not None
            or flow_abstention_buy_sell_ratio_60s_ceiling is not None
            or flow_abstention_sell_pressure_60s_floor is not None
            or flow_abstention_signed_imbalance_60s_ceiling is not None
            or flow_abstention_overlap_60s_floor is not None
            or flow_abstention_event_count_10s_floor is not None
            or flow_abstention_toxic_volume_floor is not None
        )
    )
    late_pump_veto_min_age = _optional_nonnegative_finite(
        buy_late_pump_veto_min_age_seconds,
        "buy_late_pump_veto_min_age_seconds",
    )
    late_pump_veto_extension_window = _optional_nonnegative_finite(
        buy_late_pump_veto_extension_window_seconds,
        "buy_late_pump_veto_extension_window_seconds",
    )
    late_pump_veto_min_extension = _optional_nonnegative_finite(
        buy_late_pump_veto_min_price_extension_pct,
        "buy_late_pump_veto_min_price_extension_pct",
    )
    late_pump_veto_min_peak_drawdown = _optional_nonnegative_finite(
        buy_late_pump_veto_min_drawdown_from_peak_pct,
        "buy_late_pump_veto_min_drawdown_from_peak_pct",
    )
    late_pump_veto_volume_floor = _optional_nonnegative_finite(
        buy_late_pump_veto_min_entry_volume_30s,
        "buy_late_pump_veto_min_entry_volume_30s",
    )
    late_pump_veto_price_volatility_floor = _optional_nonnegative_finite(
        buy_late_pump_veto_min_entry_price_volatility,
        "buy_late_pump_veto_min_entry_price_volatility",
    )
    entry_slippage_risk_veto_min_age = _optional_nonnegative_finite(
        buy_entry_slippage_risk_veto_min_age_seconds,
        "buy_entry_slippage_risk_veto_min_age_seconds",
    )
    entry_slippage_risk_veto_extension_window = _optional_nonnegative_finite(
        buy_entry_slippage_risk_veto_extension_window_seconds,
        "buy_entry_slippage_risk_veto_extension_window_seconds",
    )
    entry_slippage_risk_veto_min_extension = _optional_nonnegative_finite(
        buy_entry_slippage_risk_veto_min_price_extension_pct,
        "buy_entry_slippage_risk_veto_min_price_extension_pct",
    )
    entry_slippage_risk_veto_min_peak_drawdown = _optional_nonnegative_finite(
        buy_entry_slippage_risk_veto_min_drawdown_from_peak_pct,
        "buy_entry_slippage_risk_veto_min_drawdown_from_peak_pct",
    )
    entry_slippage_risk_veto_min_recent_jump = _optional_nonnegative_finite(
        buy_entry_slippage_risk_veto_min_recent_jump_pct,
        "buy_entry_slippage_risk_veto_min_recent_jump_pct",
    )
    entry_slippage_risk_veto_volume_floor = _optional_nonnegative_finite(
        buy_entry_slippage_risk_veto_min_entry_volume_30s,
        "buy_entry_slippage_risk_veto_min_entry_volume_30s",
    )
    entry_slippage_risk_veto_price_volatility_floor = _optional_nonnegative_finite(
        buy_entry_slippage_risk_veto_min_entry_price_volatility,
        "buy_entry_slippage_risk_veto_min_entry_price_volatility",
    )
    dead_bounce_veto_max_age = _optional_nonnegative_finite(
        buy_dead_bounce_veto_max_age_seconds,
        "buy_dead_bounce_veto_max_age_seconds",
    )
    dead_bounce_veto_min_peak_drawdown = _optional_nonnegative_finite(
        buy_dead_bounce_veto_min_peak_drawdown_pct,
        "buy_dead_bounce_veto_min_peak_drawdown_pct",
    )
    dead_bounce_veto_min_creator_sell_volume = _optional_nonnegative_finite(
        buy_dead_bounce_veto_min_creator_sell_volume_bnb,
        "buy_dead_bounce_veto_min_creator_sell_volume_bnb",
    )
    dead_bounce_veto_max_buy_pressure = _optional_nonnegative_finite(
        buy_dead_bounce_veto_max_buy_pressure,
        "buy_dead_bounce_veto_max_buy_pressure",
    )
    dead_bounce_veto_volume_floor = _optional_nonnegative_finite(
        buy_dead_bounce_veto_min_entry_volume_30s,
        "buy_dead_bounce_veto_min_entry_volume_30s",
    )
    dead_bounce_veto_price_volatility_floor = _optional_nonnegative_finite(
        buy_dead_bounce_veto_min_entry_price_volatility,
        "buy_dead_bounce_veto_min_entry_price_volatility",
    )
    if dead_bounce_veto_min_peak_drawdown is not None and dead_bounce_veto_min_peak_drawdown > 1.0:
        raise ValueError("buy_dead_bounce_veto_min_peak_drawdown_pct must be <= 1.0")
    if dead_bounce_veto_max_buy_pressure is not None and dead_bounce_veto_max_buy_pressure > 1.0:
        raise ValueError("buy_dead_bounce_veto_max_buy_pressure must be <= 1.0")
    if entry_slippage_risk_veto_min_peak_drawdown is not None and entry_slippage_risk_veto_min_peak_drawdown > 1.0:
        raise ValueError("buy_entry_slippage_risk_veto_min_drawdown_from_peak_pct must be <= 1.0")
    late_pump_veto_enabled = (
        late_pump_veto_min_age is not None
        and late_pump_veto_extension_window is not None
        and late_pump_veto_min_extension is not None
        and late_pump_veto_volume_floor is not None
        and late_pump_veto_price_volatility_floor is not None
    )
    entry_slippage_risk_veto_enabled = (
        entry_slippage_risk_veto_min_age is not None
        and entry_slippage_risk_veto_extension_window is not None
        and entry_slippage_risk_veto_min_extension is not None
        and entry_slippage_risk_veto_min_peak_drawdown is not None
        and entry_slippage_risk_veto_min_recent_jump is not None
        and entry_slippage_risk_veto_volume_floor is not None
        and entry_slippage_risk_veto_price_volatility_floor is not None
    )
    dead_bounce_veto_enabled = (
        dead_bounce_veto_max_age is not None
        and dead_bounce_veto_min_peak_drawdown is not None
        and dead_bounce_veto_min_creator_sell_volume is not None
        and dead_bounce_veto_max_buy_pressure is not None
        and dead_bounce_veto_volume_floor is not None
        and dead_bounce_veto_price_volatility_floor is not None
    )
    entry_price_protection = (
        None
        if entry_price_protection_pct is None
        else max(0.0, float(entry_price_protection_pct))
    )
    entry_counts_by_token = {}
    entry_count = 0
    pending_entries = {}
    partial_exits_enabled = bool(allow_partial_exits)
    entry_wait_seconds = []
    entry_fill_lag_seconds = []
    exit_wait_seconds = []
    entry_signal_count = 0
    entry_attempt_count = 0
    entry_blocked_count = 0
    entry_timeout_count = 0
    entry_price_protection_skip_count = 0
    entry_execution_failure_count = 0
    entry_score_reject_count = 0
    entry_quality_reject_count = 0
    near_threshold_signal_count = 0
    near_threshold_entry_count = 0
    near_threshold_reject_count = 0
    primary_score_rescue_signal_count = 0
    primary_score_rescue_entry_count = 0
    primary_score_rescue_reject_count = 0
    low_volume_rescue_signal_count = 0
    low_volume_rescue_entry_count = 0
    low_volume_rescue_reject_count = 0
    quick_profit_overlay_signal_count = 0
    quick_profit_overlay_entry_count = 0
    quick_profit_overlay_reject_count = 0
    quick_profit_overlay_confirmation_reject_count = 0
    quick_profit_overlay_take_profit_count = 0
    quick_profit_overlay_timeout_count = 0
    action_policy_router_signal_count = 0
    action_policy_router_entry_count = 0
    action_policy_router_reject_count = 0
    action_policy_router_passthrough_count = 0
    action_policy_router_quick_take_profit_entry_count = 0
    action_policy_router_continue_hold_entry_count = 0
    action_policy_continue_hold_take_profit_count = 0
    action_policy_continue_hold_forced_hold_count = 0
    shadow_meta_gate_signal_count = 0
    shadow_meta_gate_entry_count = 0
    shadow_meta_gate_reject_count = 0
    path_state_meta_gate_signal_count = 0
    path_state_meta_gate_entry_count = 0
    path_state_meta_gate_reject_count = 0
    flow_activation_signal_count = 0
    flow_activation_entry_count = 0
    flow_activation_reject_count = 0
    dead_flow_exit_count = 0
    flow_abstention_veto_signal_count = 0
    flow_abstention_veto_reject_count = 0
    profit_lock_take_profit_count = 0
    late_pump_veto_signal_count = 0
    late_pump_veto_reject_count = 0
    entry_slippage_risk_veto_signal_count = 0
    entry_slippage_risk_veto_reject_count = 0
    dead_bounce_veto_signal_count = 0
    dead_bounce_veto_reject_count = 0
    exit_attempt_count = 0
    exit_execution_failure_count = 0
    exit_timeout_count = 0
    if buy_probabilities_by_episode is None:
        buy_probabilities_by_episode = _episode_buy_probabilities(
            episodes,
            buy_model,
            feature_names=feature_names,
            ignored_feature_names=ignored_feature_names,
            max_entry_age_seconds=max_entry_age_seconds,
        )
    if entry_scores_by_episode is None:
        entry_scores_by_episode = [{} for _episode in episodes]
    if shadow_scores_by_episode is None:
        shadow_scores_by_episode = [{} for _episode in episodes]
    if low_volume_rescue_scores_by_episode is None:
        low_volume_rescue_scores_by_episode = [{} for _episode in episodes]
    if path_state_scores_by_episode is None:
        path_state_scores_by_episode = [{} for _episode in episodes]
    if action_policy_routes_by_episode is None:
        action_policy_routes_by_episode = [{} for _episode in episodes]

    def _entry_allowed(token):
        token_key = str(token or "").strip().lower()
        count = int(entry_counts_by_token.get(token_key, 0))
        if bool(one_entry_per_token) and count > 0:
            return False
        if max_entries_per_token is not None and count >= max_entries_per_token:
            return False
        return True

    def _available_cash_for_new_entry():
        if fixed_stake is not None:
            reserved = len(pending_entries) * (fixed_stake + entry_fixed_cost)
            return max(0.0, cash - reserved)
        return cash

    def _can_open_position(token):
        available_cash = _available_cash_for_new_entry()
        if available_cash <= 0.0 or (fixed_stake is None and stake_fraction <= 0.0):
            return False
        if fixed_stake is not None and available_cash + 1e-12 < fixed_stake + entry_fixed_cost:
            return False
        if pending_entry_cap is not None and len(pending_entries) >= pending_entry_cap:
            return False
        if open_position_cap is not None and (len(positions) + len(pending_entries)) >= open_position_cap:
            return False
        return _entry_allowed(token)

    def _passes_entry_score_filter(entry_score):
        if entry_score_floor is None:
            return True
        if entry_score is None:
            return False
        try:
            score = float(entry_score)
        except (TypeError, ValueError):
            return False
        return math.isfinite(score) and score >= entry_score_floor

    def _passes_entry_quality_filter(sample):
        features = sample.get("features", {}) if isinstance(sample, dict) else {}
        if entry_volume_30s_floor is not None and entry_volume_30s_floor > 0.0:
            try:
                volume_30s = float(features.get("volume_30s", 0.0) or 0.0)
            except (TypeError, ValueError):
                return False
            if not math.isfinite(volume_30s) or volume_30s < entry_volume_30s_floor:
                return False
        if entry_price_volatility_floor is not None and entry_price_volatility_floor > 0.0:
            try:
                price_volatility = float(features.get("price_volatility", 0.0) or 0.0)
            except (TypeError, ValueError):
                return False
            if not math.isfinite(price_volatility) or price_volatility < entry_price_volatility_floor:
                return False
        return True

    def _near_threshold_prob_candidate(buy_prob):
        if near_threshold_prob_floor is None:
            return False
        try:
            probability = float(buy_prob)
        except (TypeError, ValueError):
            return False
        return math.isfinite(probability) and probability >= near_threshold_prob_floor and probability < float(threshold)

    def _near_threshold_reject_kind(sample, entry_score):
        if near_threshold_score_floor is None or entry_score is None:
            return "score"
        try:
            score = float(entry_score)
        except (TypeError, ValueError):
            return "score"
        if not math.isfinite(score) or score < near_threshold_score_floor:
            return "score"

        features = sample.get("features", {}) if isinstance(sample, dict) else {}
        if near_threshold_volume_30s_floor is not None and near_threshold_volume_30s_floor > 0.0:
            try:
                volume_30s = float(features.get("volume_30s", 0.0) or 0.0)
            except (TypeError, ValueError):
                return "quality"
            if not math.isfinite(volume_30s) or volume_30s < near_threshold_volume_30s_floor:
                return "quality"
        if (
            near_threshold_price_volatility_floor is not None
            and near_threshold_price_volatility_floor > 0.0
        ):
            try:
                price_volatility = float(features.get("price_volatility", 0.0) or 0.0)
            except (TypeError, ValueError):
                return "quality"
            if (
                not math.isfinite(price_volatility)
                or price_volatility < near_threshold_price_volatility_floor
            ):
                return "quality"

        age_seconds = float(_sample_age_seconds(sample))
        if near_threshold_min_age is not None and age_seconds < near_threshold_min_age:
            return "quality"
        if entry_age_limit is not None and age_seconds > float(entry_age_limit):
            return "quality"
        return None

    def _primary_score_rescue_prob_candidate(buy_prob):
        if not primary_score_rescue_enabled:
            return False
        try:
            probability = float(buy_prob)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(probability):
            return False
        return probability >= max(float(threshold), float(primary_score_rescue_prob_floor))

    def _primary_score_rescue_reject_kind(sample, entry_score):
        if not primary_score_rescue_enabled or entry_score is None:
            return "score"
        try:
            score = float(entry_score)
        except (TypeError, ValueError):
            return "score"
        if not math.isfinite(score) or score < float(primary_score_rescue_score_floor):
            return "score"

        features = sample.get("features", {}) if isinstance(sample, dict) else {}
        volume_floor = primary_score_rescue_volume_30s_floor
        if volume_floor is None and entry_volume_30s_floor is not None and entry_volume_30s_floor > 0.0:
            volume_floor = entry_volume_30s_floor
        if volume_floor is not None and volume_floor > 0.0:
            try:
                volume_30s = float(features.get("volume_30s", 0.0) or 0.0)
            except (TypeError, ValueError):
                return "quality"
            if not math.isfinite(volume_30s) or volume_30s < volume_floor:
                return "quality"

        price_volatility_floor = primary_score_rescue_price_volatility_floor
        if (
            price_volatility_floor is None
            and entry_price_volatility_floor is not None
            and entry_price_volatility_floor > 0.0
        ):
            price_volatility_floor = entry_price_volatility_floor
        if price_volatility_floor is not None and price_volatility_floor > 0.0:
            try:
                price_volatility = float(features.get("price_volatility", 0.0) or 0.0)
            except (TypeError, ValueError):
                return "quality"
            if not math.isfinite(price_volatility) or price_volatility < price_volatility_floor:
                return "quality"

        age_seconds = float(_sample_age_seconds(sample))
        if primary_score_rescue_min_age is not None and age_seconds < primary_score_rescue_min_age:
            return "quality"
        if entry_age_limit is not None and age_seconds > float(entry_age_limit):
            return "quality"
        return None

    def _quick_profit_overlay_prob_candidate(buy_prob):
        if not quick_profit_overlay_enabled:
            return False
        try:
            probability = float(buy_prob)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(probability):
            return False
        return probability >= max(float(threshold), float(quick_profit_overlay_prob_floor))

    def _quick_profit_overlay_age_seconds(sample):
        meta = sample.get("meta", {}) if isinstance(sample, dict) else {}
        if "sample_interval" in meta:
            try:
                age = float(meta.get("sample_interval"))
            except (TypeError, ValueError):
                return None
            return age if math.isfinite(age) and age >= 0.0 else None
        if "create_timestamp" not in meta or "sample_time" not in meta:
            return None
        try:
            sample_time_value = float(meta.get("sample_time"))
            create_time_value = float(meta.get("create_timestamp"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(sample_time_value) or not math.isfinite(create_time_value):
            return None
        age = sample_time_value - create_time_value
        return age if age >= 0.0 else None

    def _quick_profit_overlay_reject_kind(sample, buy_prob, entry_score):
        if not quick_profit_overlay_enabled:
            return "disabled"
        if (
            quick_profit_overlay_score_floor is None
            or quick_profit_overlay_score_ceiling is None
            or quick_profit_overlay_volume_floor is None
            or quick_profit_overlay_price_volatility_floor is None
            or quick_profit_overlay_age_ceiling is None
            or quick_profit_overlay_take_profit is None
            or quick_profit_overlay_max_hold is None
        ):
            return "quality"
        try:
            probability = float(buy_prob)
        except (TypeError, ValueError):
            return "probability"
        if (
            not math.isfinite(probability)
            or probability < float(threshold)
            or probability < float(quick_profit_overlay_prob_floor)
        ):
            return "probability"

        if entry_score is None:
            return "score"
        try:
            score = float(entry_score)
        except (TypeError, ValueError):
            return "score"
        if (
            not math.isfinite(score)
            or score < float(quick_profit_overlay_score_floor)
            or score > float(quick_profit_overlay_score_ceiling)
        ):
            return "score"

        features = sample.get("features", {}) if isinstance(sample, dict) else {}
        try:
            volume_30s = float(features.get("volume_30s", 0.0) or 0.0)
        except (TypeError, ValueError):
            return "quality"
        if not math.isfinite(volume_30s) or volume_30s < float(quick_profit_overlay_volume_floor):
            return "quality"

        try:
            price_volatility = float(features.get("price_volatility", 0.0) or 0.0)
        except (TypeError, ValueError):
            return "quality"
        if (
            not math.isfinite(price_volatility)
            or price_volatility < float(quick_profit_overlay_price_volatility_floor)
        ):
            return "quality"

        if quick_profit_overlay_interval_regularity_floor is not None:
            try:
                interval_regularity = float(features.get("interval_regularity"))
            except (TypeError, ValueError):
                return "quality"
            if (
                not math.isfinite(interval_regularity)
                or interval_regularity < float(quick_profit_overlay_interval_regularity_floor)
            ):
                return "quality"

        if quick_profit_overlay_total_buys_floor is not None:
            if "total_buys" not in features:
                return "quality"
            try:
                total_buys = float(features.get("total_buys"))
            except (TypeError, ValueError):
                return "quality"
            if (
                not math.isfinite(total_buys)
                or total_buys < float(quick_profit_overlay_total_buys_floor)
            ):
                return "quality"

        if quick_profit_overlay_flow_event_floor is not None:
            meta = sample.get("meta", {}) if isinstance(sample, dict) else {}
            try:
                flow_event_count_30s = float(meta.get("flow_event_count_30s"))
            except (TypeError, ValueError):
                return "quality"
            if (
                not math.isfinite(flow_event_count_30s)
                or flow_event_count_30s < float(quick_profit_overlay_flow_event_floor)
            ):
                return "quality"

        if quick_profit_overlay_overlap_ceiling is not None:
            try:
                buy_sell_overlap_ratio_60s = float(features.get("buy_sell_overlap_ratio_60s"))
            except (TypeError, ValueError):
                return "quality"
            if (
                not math.isfinite(buy_sell_overlap_ratio_60s)
                or buy_sell_overlap_ratio_60s > float(quick_profit_overlay_overlap_ceiling)
            ):
                return "quality"

        if quick_profit_overlay_reentry_ceiling is not None:
            try:
                recent_seller_reentry_ratio_30s = float(features.get("recent_seller_reentry_ratio_30s"))
            except (TypeError, ValueError):
                return "quality"
            if (
                not math.isfinite(recent_seller_reentry_ratio_30s)
                or recent_seller_reentry_ratio_30s > float(quick_profit_overlay_reentry_ceiling)
            ):
                return "quality"

        age_seconds = _quick_profit_overlay_age_seconds(sample)
        if age_seconds is None or age_seconds > float(quick_profit_overlay_age_ceiling):
            return "quality"
        if entry_age_limit is not None and age_seconds > float(entry_age_limit):
            return "quality"
        return None

    def _quick_profit_overlay_confirmation_reject_kind(pending_entry, fill_price):
        if not bool((pending_entry or {}).get("quick_profit_overlay_used", False)):
            return None
        if (
            quick_profit_overlay_confirmation_drawdown_ceiling is None
            and quick_profit_overlay_confirmation_chase_ceiling is None
        ):
            return None
        try:
            signal_price = float((pending_entry or {}).get("signal_price", 0.0) or 0.0)
            fill_price_value = float(fill_price)
        except (TypeError, ValueError):
            return "price"
        if (
            not math.isfinite(signal_price)
            or not math.isfinite(fill_price_value)
            or signal_price <= 0.0
            or fill_price_value <= 0.0
        ):
            return "price"
        confirmation_return = (fill_price_value / signal_price) - 1.0
        if (
            quick_profit_overlay_confirmation_drawdown_ceiling is not None
            and confirmation_return < -float(quick_profit_overlay_confirmation_drawdown_ceiling)
        ):
            return "drawdown"
        if (
            quick_profit_overlay_confirmation_chase_ceiling is not None
            and confirmation_return > float(quick_profit_overlay_confirmation_chase_ceiling)
        ):
            return "chase"
        return None

    def _shadow_meta_gate_reject_kind(sample, buy_prob, entry_score, shadow_score):
        if not shadow_meta_gate_enabled:
            return "disabled"
        try:
            probability = float(buy_prob)
        except (TypeError, ValueError):
            return "probability"
        if (
            not math.isfinite(probability)
            or probability < float(threshold)
            or probability < float(shadow_meta_gate_prob_floor)
        ):
            return "probability"

        if entry_score is None:
            return "score"
        try:
            score = float(entry_score)
        except (TypeError, ValueError):
            return "score"
        if not math.isfinite(score):
            return "score"
        if entry_score_floor is None or score >= float(entry_score_floor):
            return "score"
        if score > float(shadow_meta_gate_max_entry_score):
            return "score"

        features = sample.get("features", {}) if isinstance(sample, dict) else {}
        try:
            volume_30s = float(features.get("volume_30s", 0.0) or 0.0)
        except (TypeError, ValueError):
            return "quality"
        if not math.isfinite(volume_30s) or volume_30s < float(shadow_meta_gate_volume_30s_floor):
            return "quality"

        try:
            price_volatility = float(features.get("price_volatility", 0.0) or 0.0)
        except (TypeError, ValueError):
            return "quality"
        if (
            not math.isfinite(price_volatility)
            or price_volatility < float(shadow_meta_gate_price_volatility_floor)
        ):
            return "quality"

        age_seconds = float(_sample_age_seconds(sample))
        if age_seconds > float(shadow_meta_gate_age_ceiling):
            return "quality"
        if entry_age_limit is not None and age_seconds > float(entry_age_limit):
            return "quality"

        if shadow_score is None:
            return "meta_score"
        try:
            meta_score = float(shadow_score)
        except (TypeError, ValueError):
            return "meta_score"
        if not math.isfinite(meta_score) or meta_score < float(shadow_meta_gate_score_floor):
            return "meta_score"
        return None

    def _flow_activation_prob_candidate(buy_prob):
        if not flow_activation_enabled:
            return False
        try:
            probability = float(buy_prob)
        except (TypeError, ValueError):
            return False
        return math.isfinite(probability) and probability >= float(flow_activation_prob_floor)

    def _flow_activation_metrics(episode, current_index, current_score, entry_score_by_index):
        sample = episode[current_index]
        meta = sample.get("meta", {}) if isinstance(sample, dict) else {}
        try:
            sample_time = float(meta.get("sample_time", current_index) or current_index)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(sample_time):
            return None
        lookback = float(flow_activation_lookback or 0.0)
        lookback_start = sample_time - lookback
        history = []
        for prior_index, prior_sample in enumerate(episode[:current_index]):
            prior_meta = prior_sample.get("meta", {}) if isinstance(prior_sample, dict) else {}
            try:
                prior_time = float(prior_meta.get("sample_time", prior_index) or prior_index)
            except (TypeError, ValueError):
                continue
            if math.isfinite(prior_time) and prior_time >= lookback_start and prior_time < sample_time:
                history.append((prior_index, prior_time, prior_sample))

        if history:
            baseline_index, _baseline_time, baseline_sample = history[0]
        else:
            baseline_index, baseline_sample = current_index, sample

        current_features = sample.get("features", {}) if isinstance(sample, dict) else {}
        baseline_features = baseline_sample.get("features", {}) if isinstance(baseline_sample, dict) else {}
        try:
            current_volume = float(current_features.get("volume_30s", 0.0) or 0.0)
            baseline_volume = float(baseline_features.get("volume_30s", 0.0) or 0.0)
            current_volatility = float(current_features.get("price_volatility", 0.0) or 0.0)
            baseline_volatility = float(baseline_features.get("price_volatility", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(value) for value in (
            current_volume,
            baseline_volume,
            current_volatility,
            baseline_volatility,
        )):
            return None

        baseline_score = entry_score_by_index.get(baseline_index)
        pred_return_delta = None
        if baseline_score is not None and current_score is not None:
            try:
                pred_return_delta = float(current_score) - float(baseline_score)
            except (TypeError, ValueError):
                pred_return_delta = None
            if pred_return_delta is not None and not math.isfinite(pred_return_delta):
                pred_return_delta = None

        return {
            "current_volume_30s": current_volume,
            "volume_ramp_ratio": None if baseline_volume <= 0.0 else current_volume / baseline_volume,
            "volume_ramp_delta": current_volume - baseline_volume,
            "price_volatility_delta": current_volatility - baseline_volatility,
            "pred_return_delta": pred_return_delta,
            "history_count": len(history),
        }

    def _flow_activation_reject_kind(episode, current_index, sample, buy_prob, entry_score, entry_score_by_index):
        if not flow_activation_enabled:
            return "disabled"
        try:
            probability = float(buy_prob)
        except (TypeError, ValueError):
            return "probability"
        if not math.isfinite(probability) or probability < float(flow_activation_prob_floor):
            return "probability"

        if flow_activation_score_floor is not None:
            if entry_score is None:
                return "score"
            try:
                score = float(entry_score)
            except (TypeError, ValueError):
                return "score"
            if not math.isfinite(score) or score < float(flow_activation_score_floor):
                return "score"

        if flow_activation_age_ceiling is not None:
            age_seconds = _quick_profit_overlay_age_seconds(sample)
            if age_seconds is None or age_seconds > float(flow_activation_age_ceiling):
                return "quality"
            if entry_age_limit is not None and age_seconds > float(entry_age_limit):
                return "quality"

        metrics = _flow_activation_metrics(episode, current_index, entry_score, entry_score_by_index)
        if metrics is None:
            return "quality"

        if flow_activation_current_volume_floor is not None:
            current_volume = metrics.get("current_volume_30s")
            if current_volume is None or current_volume < float(flow_activation_current_volume_floor):
                return "quality"
        if flow_activation_volume_ramp_ratio_floor is not None:
            ramp_ratio = metrics.get("volume_ramp_ratio")
            if ramp_ratio is None or ramp_ratio < float(flow_activation_volume_ramp_ratio_floor):
                return "quality"
        if flow_activation_volume_ramp_delta_floor is not None:
            ramp_delta = metrics.get("volume_ramp_delta")
            if ramp_delta is None or ramp_delta < float(flow_activation_volume_ramp_delta_floor):
                return "quality"
        if flow_activation_pred_return_delta_floor is not None:
            pred_return_delta = metrics.get("pred_return_delta")
            if pred_return_delta is None or pred_return_delta < float(flow_activation_pred_return_delta_floor):
                return "score"
        if flow_activation_price_volatility_delta_floor is not None:
            volatility_delta = metrics.get("price_volatility_delta")
            if volatility_delta is None or volatility_delta < float(flow_activation_price_volatility_delta_floor):
                return "quality"
        return None

    def _action_policy_router_decision(route_item):
        if not action_policy_router_enabled:
            return "disabled", None
        if not isinstance(route_item, dict):
            if action_policy_router_skip_passthrough:
                return "passthrough", None
            return "missing_route", None
        route = str(route_item.get("route") or "").strip()
        confidence = route_item.get("confidence")
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            if action_policy_router_skip_passthrough:
                return "passthrough", route
            return "confidence", route
        if (
            not math.isfinite(confidence_value)
            or confidence_value < float(action_policy_router_confidence_floor)
        ):
            if action_policy_router_skip_passthrough:
                return "passthrough", route
            return "confidence", route
        if action_policy_router_prob_floor is not None:
            route_prob = _finite_float(route_item.get("prob"))
            if route_prob is None or route_prob < float(action_policy_router_prob_floor):
                if action_policy_router_skip_passthrough:
                    return "passthrough", route
                return "prob", route
        if action_policy_router_score_ceiling is not None:
            route_score = _finite_float(route_item.get("pred_return"))
            if route_score is None or route_score > float(action_policy_router_score_ceiling):
                if action_policy_router_skip_passthrough:
                    return "passthrough", route
                return "pred_return", route
        if route == "skip":
            if action_policy_router_skip_passthrough:
                return "passthrough", route
            return "skip", route
        if route in {"conditional_slow_hold", "continue_hold", "lock_profit", "quick_take_profit"}:
            return None, route
        if action_policy_router_skip_passthrough:
            return "passthrough", route
        return "unsupported_route", route

    def _low_volume_rescue_age_seconds(sample):
        meta = sample.get("meta", {}) if isinstance(sample, dict) else {}
        try:
            if "sample_interval" in meta:
                age = float(meta.get("sample_interval"))
            else:
                if "create_timestamp" not in meta or "sample_time" not in meta:
                    return None
                age = float(meta.get("sample_time")) - float(meta.get("create_timestamp"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(age) or age < 0.0:
            return None
        return age

    def _low_volume_rescue_candidate(sample):
        if not low_volume_rescue_enabled:
            return False
        if (
            entry_volume_30s_floor is None
            or entry_volume_30s_floor <= 0.0
            or low_volume_rescue_volume_floor is None
            or low_volume_rescue_volume_ceiling is None
        ):
            return False
        features = sample.get("features", {}) if isinstance(sample, dict) else {}
        try:
            volume_30s = float(features.get("volume_30s"))
        except (TypeError, ValueError):
            return False
        if not math.isfinite(volume_30s):
            return False
        return (
            volume_30s < entry_volume_30s_floor
            and volume_30s >= low_volume_rescue_volume_floor
            and volume_30s <= low_volume_rescue_volume_ceiling
        )

    def _low_volume_rescue_reject_kind(sample, buy_prob):
        if not low_volume_rescue_enabled:
            return "disabled"
        if (
            low_volume_rescue_volume_floor is None
            or low_volume_rescue_volume_ceiling is None
            or low_volume_rescue_price_volatility_floor is None
            or low_volume_rescue_age_ceiling is None
        ):
            return "quality"
        try:
            probability = float(buy_prob)
        except (TypeError, ValueError):
            return "probability"
        if (
            not math.isfinite(probability)
            or probability < float(threshold)
            or probability < float(low_volume_rescue_prob_floor)
        ):
            return "probability"

        features = sample.get("features", {}) if isinstance(sample, dict) else {}
        try:
            volume_30s = float(features.get("volume_30s"))
        except (TypeError, ValueError):
            return "quality"
        if (
            not math.isfinite(volume_30s)
            or volume_30s < low_volume_rescue_volume_floor
            or volume_30s > low_volume_rescue_volume_ceiling
        ):
            return "quality"

        try:
            price_volatility = float(features.get("price_volatility"))
        except (TypeError, ValueError):
            return "quality"
        if (
            not math.isfinite(price_volatility)
            or price_volatility < low_volume_rescue_price_volatility_floor
        ):
            return "quality"

        age_seconds = _low_volume_rescue_age_seconds(sample)
        if age_seconds is None or age_seconds > low_volume_rescue_age_ceiling:
            return "quality"
        return None

    def _late_pump_veto_candidate(sample, price_extension):
        if not late_pump_veto_enabled or not price_extension:
            return False
        age_seconds = float(_sample_age_seconds(sample))
        if age_seconds < float(late_pump_veto_min_age):
            return False
        current_extension = float(price_extension.get("current_extension", 0.0))
        peak_extension = float(price_extension.get("chronological_peak_extension", 0.0))
        drawdown_from_peak = float(price_extension.get("drawdown_from_peak", 0.0))
        current_extension_match = current_extension >= float(late_pump_veto_min_extension)
        peak_fade_match = (
            late_pump_veto_min_peak_drawdown is not None
            and peak_extension >= float(late_pump_veto_min_extension)
            and drawdown_from_peak >= float(late_pump_veto_min_peak_drawdown)
        )
        if not (current_extension_match or peak_fade_match):
            return False
        features = sample.get("features", {}) if isinstance(sample, dict) else {}
        try:
            volume_30s = float(features.get("volume_30s"))
            price_volatility = float(features.get("price_volatility"))
        except (TypeError, ValueError):
            return False
        return (
            math.isfinite(volume_30s)
            and math.isfinite(price_volatility)
            and volume_30s >= float(late_pump_veto_volume_floor)
            and price_volatility >= float(late_pump_veto_price_volatility_floor)
        )

    def _entry_slippage_risk_veto_candidate(sample, price_extension):
        if not entry_slippage_risk_veto_enabled or not price_extension:
            return False
        age_seconds = float(_sample_age_seconds(sample))
        if age_seconds < float(entry_slippage_risk_veto_min_age):
            return False
        current_extension = float(price_extension.get("current_extension", 0.0))
        drawdown_from_peak = float(price_extension.get("drawdown_from_peak", 0.0))
        recent_jump = float(price_extension.get("recent_jump", 0.0))
        if current_extension < float(entry_slippage_risk_veto_min_extension):
            return False
        if drawdown_from_peak < float(entry_slippage_risk_veto_min_peak_drawdown):
            return False
        if recent_jump < float(entry_slippage_risk_veto_min_recent_jump):
            return False
        features = sample.get("features", {}) if isinstance(sample, dict) else {}
        try:
            volume_30s = float(features.get("volume_30s"))
            price_volatility = float(features.get("price_volatility"))
        except (TypeError, ValueError):
            return False
        return (
            math.isfinite(volume_30s)
            and math.isfinite(price_volatility)
            and volume_30s >= float(entry_slippage_risk_veto_volume_floor)
            and price_volatility >= float(entry_slippage_risk_veto_price_volatility_floor)
        )

    def _finite_feature_float(features, key, default):
        try:
            value = float(features.get(key, default))
        except (TypeError, ValueError):
            return float(default)
        return value if math.isfinite(value) else float(default)

    def _dead_bounce_veto_candidate(sample, buy_prob, entry_score):
        if not dead_bounce_veto_enabled:
            return False
        _ = buy_prob, entry_score
        features = sample.get("features", {}) if isinstance(sample, dict) else {}
        age_seconds = _quick_profit_overlay_age_seconds(sample)
        if age_seconds is None or age_seconds > float(dead_bounce_veto_max_age):
            return False

        current_price = _finite_feature_float(features, "current_price", 0.0)
        max_price = _finite_feature_float(features, "max_price", 0.0)
        if current_price <= 0.0 or max_price <= 0.0:
            return False
        peak_drawdown = max(0.0, 1.0 - (current_price / max_price))
        if peak_drawdown < float(dead_bounce_veto_min_peak_drawdown):
            return False

        creator_sell_volume = _finite_feature_float(features, "creator_sell_volume", 0.0)
        buy_pressure = _finite_feature_float(features, "buy_pressure", 1.0)
        has_creator_pressure = creator_sell_volume >= float(dead_bounce_veto_min_creator_sell_volume)
        has_low_buy_pressure = buy_pressure <= float(dead_bounce_veto_max_buy_pressure)
        if not (has_creator_pressure or has_low_buy_pressure):
            return False

        volume_30s = _finite_feature_float(features, "volume_30s", 0.0)
        price_volatility = _finite_feature_float(features, "price_volatility", 0.0)
        return (
            volume_30s >= float(dead_bounce_veto_volume_floor)
            and price_volatility >= float(dead_bounce_veto_price_volatility_floor)
        )

    def _flow_abstention_veto_candidate(sample, buy_prob):
        if not flow_abstention_enabled:
            return False
        try:
            probability = float(buy_prob)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(probability) or probability < float(flow_abstention_prob_floor):
            return False

        if flow_abstention_age_ceiling is not None:
            age_seconds = _quick_profit_overlay_age_seconds(sample)
            if age_seconds is None or age_seconds > float(flow_abstention_age_ceiling):
                return False
            if entry_age_limit is not None and age_seconds > float(entry_age_limit):
                return False

        features = sample.get("features", {}) if isinstance(sample, dict) else {}
        if flow_abstention_volume_floor is not None:
            volume_30s = _finite_feature_float(features, "volume_30s", -1.0)
            if volume_30s < float(flow_abstention_volume_floor):
                return False
        if flow_abstention_price_volatility_floor is not None:
            price_volatility = _finite_feature_float(features, "price_volatility", -1.0)
            if price_volatility < float(flow_abstention_price_volatility_floor):
                return False

        toxic_flow = False
        if flow_abstention_buy_sell_ratio_ceiling is not None:
            buy_sell_ratio = _finite_feature_float(features, "flow_buy_sell_ratio_30s", math.nan)
            toxic_flow = toxic_flow or (
                math.isfinite(buy_sell_ratio)
                and buy_sell_ratio <= float(flow_abstention_buy_sell_ratio_ceiling)
            )
        if flow_abstention_sell_pressure_floor is not None:
            sell_pressure = _finite_feature_float(features, "flow_sell_pressure_30s", math.nan)
            toxic_flow = toxic_flow or (
                math.isfinite(sell_pressure)
                and sell_pressure >= float(flow_abstention_sell_pressure_floor)
            )
        if flow_abstention_signed_imbalance_ceiling is not None:
            signed_imbalance = _finite_feature_float(features, "flow_signed_imbalance_30s", math.nan)
            toxic_flow = toxic_flow or (
                math.isfinite(signed_imbalance)
                and signed_imbalance <= float(flow_abstention_signed_imbalance_ceiling)
            )
        if flow_abstention_buy_sell_ratio_60s_ceiling is not None:
            buy_sell_ratio = _finite_feature_float(features, "flow_buy_sell_ratio_60s", math.nan)
            toxic_flow = toxic_flow or (
                math.isfinite(buy_sell_ratio)
                and buy_sell_ratio <= float(flow_abstention_buy_sell_ratio_60s_ceiling)
            )
        if flow_abstention_sell_pressure_60s_floor is not None:
            sell_pressure = _finite_feature_float(features, "flow_sell_pressure_60s", math.nan)
            toxic_flow = toxic_flow or (
                math.isfinite(sell_pressure)
                and sell_pressure >= float(flow_abstention_sell_pressure_60s_floor)
            )
        if flow_abstention_signed_imbalance_60s_ceiling is not None:
            signed_imbalance = _finite_feature_float(features, "flow_signed_imbalance_60s", math.nan)
            toxic_flow = toxic_flow or (
                math.isfinite(signed_imbalance)
                and signed_imbalance <= float(flow_abstention_signed_imbalance_60s_ceiling)
            )
        if flow_abstention_overlap_60s_floor is not None:
            overlap_ratio = _finite_feature_float(features, "flow_buy_sell_overlap_ratio_60s", math.nan)
            toxic_flow = toxic_flow or (
                math.isfinite(overlap_ratio)
                and overlap_ratio >= float(flow_abstention_overlap_60s_floor)
            )
        if flow_abstention_event_count_10s_floor is not None:
            event_count = _finite_feature_float(features, "flow_event_count_10s", math.nan)
            toxic_flow = toxic_flow or (
                math.isfinite(event_count)
                and event_count >= float(flow_abstention_event_count_10s_floor)
            )
        if flow_abstention_toxic_volume_floor is not None:
            volume_30s = _finite_feature_float(features, "volume_30s", math.nan)
            toxic_flow = toxic_flow or (
                math.isfinite(volume_30s)
                and volume_30s >= float(flow_abstention_toxic_volume_floor)
            )
        return bool(toxic_flow)

    def _execution_succeeds(kind, token, sample_time, idx, failure_rate):
        if failure_rate <= 0.0:
            return True
        if failure_rate >= 1.0:
            return False
        value = _stable_unit_interval(kind, str(token or "").lower(), int(sample_time), int(idx))
        return value >= failure_rate

    def _mark_entry(token):
        nonlocal entry_count
        token_key = str(token or "").strip().lower()
        entry_counts_by_token[token_key] = int(entry_counts_by_token.get(token_key, 0)) + 1
        entry_count += 1

    def _stake_amount():
        if fixed_stake is not None:
            return fixed_stake if cash + 1e-12 >= fixed_stake + entry_fixed_cost else 0.0
        stake = cash * stake_fraction
        if max_stake_fraction is not None:
            stake = min(stake, initial_equity * max_stake_fraction)
        max_affordable_stake = max(0.0, cash - entry_fixed_cost)
        return min(max_affordable_stake, max(0.0, stake))

    def _entry_fill(stake, price):
        execution_price = float(price) * (1.0 + slippage_rate)
        if execution_price <= 0.0:
            return 0.0, 0.0
        size = float(stake) * (1.0 - fee_rate) / execution_price
        effective_price = float(stake) / max(size, 1e-12)
        return size, effective_price

    def _entry_context_float(features, key):
        try:
            value = float(features.get(key))
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    def _entry_signal_context(sample):
        raw_features = sample.get("features", {}) if isinstance(sample, dict) else {}
        features = raw_features if isinstance(raw_features, dict) else {}
        context = {
            "entry_volume_30s": _entry_context_float(features, "volume_30s"),
            "entry_price_volatility": _entry_context_float(features, "price_volatility"),
            "entry_token_age_seconds": float(_sample_age_seconds(sample)) if isinstance(sample, dict) else 0.0,
        }
        for name in REPLAY_CONTEXT_FEATURE_NAMES:
            value = _entry_context_float(features, name)
            if value is not None:
                context[name] = value
        return context

    def _exit_proceeds(size, price):
        execution_price = float(price) * max(0.0, 1.0 - slippage_rate)
        gross = float(size) * execution_price
        proceeds = gross * (1.0 - fee_rate)
        return proceeds, execution_price

    def _position_liquidation_value(position):
        price = latest_prices.get(position["token"])
        if position["size"] <= 0.0 or price is None or float(price) <= 0.0:
            return 0.0
        proceeds, _execution_price = _exit_proceeds(position["size"], price)
        return max(0.0, proceeds - exit_fixed_cost)

    def _portfolio_equity():
        return cash + sum(_position_liquidation_value(position) for position in positions.values())

    def _append_equity_point():
        equity = _portfolio_equity()
        prev_equity = equity_curve[-1]
        if prev_equity > 0:
            step_returns.append((equity / prev_equity) - 1.0)
        equity_curve.append(equity)

    def _record_exit(
        *,
        position,
        exit_time,
        exit_index,
        exit_price,
        exit_reason,
        proceeds,
        realized_cost_basis,
        size_fraction,
        requested_size_fraction=None,
    ):
        trade_return = (proceeds - realized_cost_basis) / max(realized_cost_basis, 1e-9)
        trade_returns.append(trade_return)
        if include_trade_log:
            requested_fraction = size_fraction if requested_size_fraction is None else requested_size_fraction
            row = {
                "token": str(position["token"]),
                "entry_time": int(position["entry_time"]),
                "entry_index": int(position["entry_index"]),
                "entry_price": float(position["entry_price"]),
                "exit_time": int(exit_time),
                "exit_index": int(exit_index),
                "exit_price": float(exit_price),
                "exit_reason": str(exit_reason),
                "return_pct": float(trade_return * 100.0),
                "buy_prob": float(position["buy_prob"]),
                "entry_score": None if position.get("entry_score") is None else float(position.get("entry_score")),
                "near_threshold_rescue_used": bool(position.get("near_threshold_rescue_used", False)),
                "primary_score_rescue_used": bool(position.get("primary_score_rescue_used", False)),
                "low_volume_rescue_used": bool(position.get("low_volume_rescue_used", False)),
                "quick_profit_overlay_used": bool(position.get("quick_profit_overlay_used", False)),
                "action_policy_router_used": bool(position.get("action_policy_router_used", False)),
                "action_policy_router_route": position.get("action_policy_router_route"),
                "shadow_meta_gate_used": bool(position.get("shadow_meta_gate_used", False)),
                "path_state_meta_gate_used": bool(position.get("path_state_meta_gate_used", False)),
                "flow_activation_used": bool(position.get("flow_activation_used", False)),
                "position_fraction": float(stake_fraction),
                "max_position_fraction": None if max_stake_fraction is None else float(max_stake_fraction),
                "stake_bnb": float(position.get("stake_bnb", position.get("cost_basis", 0.0))),
                "fixed_stake_bnb": None if fixed_stake is None else float(fixed_stake),
                "entry_fixed_cost_bnb": float(position.get("realized_entry_fixed_cost_bnb", 0.0)),
                "exit_fixed_cost_bnb": float(position.get("realized_exit_fixed_cost_bnb", 0.0)),
                "stake_mode": stake_mode,
                "size_fraction": float(size_fraction),
                "requested_size_fraction": float(requested_fraction),
                "entry_signal_time": int(position.get("entry_signal_time", position["entry_time"])),
                "entry_due_time": int(position.get("entry_due_time", position["entry_time"])),
                "entry_wait_seconds": float(position.get("entry_wait_seconds", 0.0)),
                "entry_fill_lag_seconds": float(position.get("entry_fill_lag_seconds", 0.0)),
                "entry_volume_30s": position.get("entry_volume_30s"),
                "entry_price_volatility": position.get("entry_price_volatility"),
                "entry_token_age_seconds": position.get("entry_token_age_seconds"),
                "fee_bps": float(fee_rate * 10000.0),
                "slippage_bps": float(slippage_rate * 10000.0),
                "allow_partial_exits": bool(partial_exits_enabled),
                "max_adverse_excursion_pct": float(((position["min_price"] / position["entry_price"]) - 1.0) * 100.0) if position["entry_price"] > 0 else 0.0,
                "max_favorable_excursion_pct": float(((position["max_price"] / position["entry_price"]) - 1.0) * 100.0) if position["entry_price"] > 0 else 0.0,
            }
            for name in REPLAY_CONTEXT_FEATURE_NAMES:
                if position.get(name) is not None:
                    row[name] = position.get(name)
            trade_log.append(row)

    def _open_position(
        token,
        sample_time,
        idx,
        price,
        buy_prob,
        episode_start_time,
        *,
        signal_time=None,
        due_time=None,
        entry_score=None,
        near_threshold_rescue_used=False,
        primary_score_rescue_used=False,
        low_volume_rescue_used=False,
        quick_profit_overlay_used=False,
        action_policy_router_used=False,
        action_policy_router_route=None,
        shadow_meta_gate_used=False,
        path_state_meta_gate_used=False,
        flow_activation_used=False,
        entry_context=None,
    ):
        nonlocal cash
        nonlocal near_threshold_entry_count, primary_score_rescue_entry_count
        nonlocal low_volume_rescue_entry_count, quick_profit_overlay_entry_count
        nonlocal action_policy_router_entry_count, action_policy_router_quick_take_profit_entry_count
        nonlocal action_policy_router_continue_hold_entry_count
        nonlocal shadow_meta_gate_entry_count, path_state_meta_gate_entry_count
        nonlocal flow_activation_entry_count
        if not _can_open_position(token):
            return False
        stake = _stake_amount()
        position_size, effective_entry_price = _entry_fill(stake, price)
        if position_size <= 0.0:
            return False
        signal_time = int(sample_time if signal_time is None else signal_time)
        due_time = int(sample_time if due_time is None else due_time)
        wait_seconds = max(0, int(sample_time) - signal_time)
        fill_lag_seconds = max(0, int(sample_time) - due_time)
        cash -= stake + entry_fixed_cost
        positions[token] = {
            "token": token,
            "size": position_size,
            "entry_size": position_size,
            "cost_basis": stake,
            "entry_cost_basis": entry_fixed_cost,
            "stake_bnb": float(stake),
            "entry_price": effective_entry_price,
            "entry_time": sample_time,
            "entry_index": idx,
            "entry_signal_time": signal_time,
            "entry_due_time": due_time,
            "entry_wait_seconds": float(wait_seconds),
            "entry_fill_lag_seconds": float(fill_lag_seconds),
            "buy_prob": float(buy_prob),
            "entry_score": None if entry_score is None else float(entry_score),
            "near_threshold_rescue_used": bool(near_threshold_rescue_used),
            "primary_score_rescue_used": bool(primary_score_rescue_used),
            "low_volume_rescue_used": bool(low_volume_rescue_used),
            "quick_profit_overlay_used": bool(quick_profit_overlay_used),
            "action_policy_router_used": bool(action_policy_router_used),
            "action_policy_router_route": action_policy_router_route,
            "shadow_meta_gate_used": bool(shadow_meta_gate_used),
            "path_state_meta_gate_used": bool(path_state_meta_gate_used),
            "flow_activation_used": bool(flow_activation_used),
            "min_price": effective_entry_price,
            "max_price": effective_entry_price,
            "episode_start_time": episode_start_time,
        }
        positions[token].update(dict(entry_context or {}))
        entry_wait_seconds.append(float(wait_seconds))
        entry_fill_lag_seconds.append(float(fill_lag_seconds))
        _mark_entry(token)
        if near_threshold_rescue_used:
            near_threshold_entry_count += 1
        if primary_score_rescue_used:
            primary_score_rescue_entry_count += 1
        if low_volume_rescue_used:
            low_volume_rescue_entry_count += 1
        if quick_profit_overlay_used:
            quick_profit_overlay_entry_count += 1
        if action_policy_router_used:
            action_policy_router_entry_count += 1
            if action_policy_router_route == "quick_take_profit":
                action_policy_router_quick_take_profit_entry_count += 1
            elif action_policy_router_route == "continue_hold":
                action_policy_router_continue_hold_entry_count += 1
        if shadow_meta_gate_used:
            shadow_meta_gate_entry_count += 1
        if path_state_meta_gate_used:
            path_state_meta_gate_entry_count += 1
        if flow_activation_used:
            flow_activation_entry_count += 1
        return True

    def _sample_live_entry_fill(sample, due_time):
        label = sample.get("label", {}) if isinstance(sample, dict) else {}
        if int(label.get("live_entry_available", 0) or 0) != 1:
            return None
        fill_time = int(label.get("live_entry_time", 0) or 0)
        fill_price = float(label.get("live_entry_price", 0.0) or 0.0)
        if fill_time < int(due_time) or fill_price <= 0.0:
            return None
        return {"fill_time": fill_time, "fill_price": fill_price}

    def _execute_exit(position, token, sample_time, idx, price, fraction, requested_fraction, exit_reason, *, exit_due_time=None):
        nonlocal cash, exit_attempt_count, exit_execution_failure_count
        if fraction <= 0.0:
            return False
        exit_attempt_count += 1
        if not _execution_succeeds("exit", token, sample_time, idx, exit_failure_rate):
            exit_execution_failure_count += 1
            return False
        if exit_due_time is not None:
            exit_wait_seconds.append(float(max(0, int(sample_time) - int(exit_due_time))))
        sell_size = position["size"] * min(1.0, float(fraction))
        proceeds, exit_execution_price = _exit_proceeds(sell_size, price)
        exit_cost = min(exit_fixed_cost, proceeds)
        net_proceeds = max(0.0, proceeds - exit_cost)
        basis_fraction = sell_size / max(position["size"], 1e-9)
        realized_cost_basis = position["cost_basis"] * basis_fraction
        realized_entry_cost_basis = position.get("entry_cost_basis", 0.0) * basis_fraction
        total_realized_cost_basis = realized_cost_basis + realized_entry_cost_basis
        position["realized_entry_fixed_cost_bnb"] = realized_entry_cost_basis
        position["realized_exit_fixed_cost_bnb"] = exit_cost
        _record_exit(
            position=position,
            exit_time=sample_time,
            exit_index=idx,
            exit_price=exit_execution_price,
            exit_reason=exit_reason,
            proceeds=net_proceeds,
            realized_cost_basis=total_realized_cost_basis,
            size_fraction=fraction,
            requested_size_fraction=requested_fraction,
        )
        cash += net_proceeds
        position["size"] -= sell_size
        position["cost_basis"] -= realized_cost_basis
        position["entry_cost_basis"] = max(
            0.0,
            float(position.get("entry_cost_basis", 0.0)) - realized_entry_cost_basis,
        )
        position.pop("pending_exit", None)
        if position["size"] <= 1e-12:
            positions.pop(token, None)
        return True

    def _price_extensions_for_window(episode, extension_window):
        price_extension_by_index = {}
        for idx, sample in enumerate(episode):
            sample_time = int(sample.get("meta", {}).get("sample_time", 0) or 0)
            try:
                current_price = float(sample.get("features", {}).get("current_price", 0.0) or 0.0)
            except (TypeError, ValueError):
                current_price = 0.0
            if current_price <= 0.0:
                continue
            earlier_points = []
            for earlier in episode[:idx]:
                earlier_time = int(earlier.get("meta", {}).get("sample_time", 0) or 0)
                if earlier_time >= sample_time:
                    continue
                if float(sample_time - earlier_time) > float(extension_window):
                    continue
                try:
                    earlier_price = float(earlier.get("features", {}).get("current_price", 0.0) or 0.0)
                except (TypeError, ValueError):
                    continue
                if earlier_price > 0.0 and math.isfinite(earlier_price):
                    earlier_points.append((earlier_time, earlier_price))
            if earlier_points:
                reference_price = min(price for _time, price in earlier_points)
                peak_price = max(price for _time, price in earlier_points)
                _previous_time, previous_price = max(earlier_points, key=lambda item: item[0])
                chronological_peak_extension = 0.0
                chronological_drawdown_from_peak = 0.0
                for reference_index, (_reference_time, low_price) in enumerate(earlier_points):
                    if low_price <= 0.0:
                        continue
                    for _peak_time, candidate_peak in earlier_points[reference_index + 1:]:
                        if candidate_peak <= low_price:
                            continue
                        candidate_extension = (candidate_peak / low_price) - 1.0
                        candidate_drawdown = max(0.0, 1.0 - (current_price / candidate_peak))
                        if (
                            candidate_extension > chronological_peak_extension
                            or (
                                candidate_extension == chronological_peak_extension
                                and candidate_drawdown > chronological_drawdown_from_peak
                            )
                        ):
                            chronological_peak_extension = candidate_extension
                            chronological_drawdown_from_peak = candidate_drawdown
                price_extension_by_index[idx] = {
                    "current_extension": (current_price / reference_price) - 1.0,
                    "peak_extension": (peak_price / reference_price) - 1.0,
                    "chronological_peak_extension": chronological_peak_extension,
                    "drawdown_from_peak": chronological_drawdown_from_peak,
                    "recent_jump": max(0.0, (current_price / previous_price) - 1.0) if previous_price > 0.0 else 0.0,
                }
        return price_extension_by_index

    timeline = []
    price_extensions_by_episode = []
    for episode_index, episode in enumerate(episodes):
        episode_start_time = int(episode[0].get("meta", {}).get("sample_time", 0) or 0) if episode else 0
        price_extensions_by_episode.append(
            {
                "late_pump": (
                    _price_extensions_for_window(episode, late_pump_veto_extension_window)
                    if late_pump_veto_enabled
                    else {}
                ),
                "entry_slippage_risk": (
                    _price_extensions_for_window(episode, entry_slippage_risk_veto_extension_window)
                    if entry_slippage_risk_veto_enabled
                    else {}
                ),
            }
        )
        if episode_index < len(buy_probabilities_by_episode):
            buy_prob_by_index = dict(buy_probabilities_by_episode[episode_index] or {})
        else:
            buy_prob_by_index = {}
        if episode_index < len(entry_scores_by_episode):
            entry_score_by_index = dict(entry_scores_by_episode[episode_index] or {})
        else:
            entry_score_by_index = {}
        if episode_index < len(shadow_scores_by_episode):
            shadow_score_by_index = dict(shadow_scores_by_episode[episode_index] or {})
        else:
            shadow_score_by_index = {}
        if episode_index < len(low_volume_rescue_scores_by_episode):
            low_volume_rescue_score_by_index = _normalized_path_state_score_map(
                low_volume_rescue_scores_by_episode[episode_index]
            )
        else:
            low_volume_rescue_score_by_index = {}
        if episode_index < len(path_state_scores_by_episode):
            path_state_score_by_index = _normalized_path_state_score_map(path_state_scores_by_episode[episode_index])
        else:
            path_state_score_by_index = {}
        if episode_index < len(action_policy_routes_by_episode):
            action_policy_route_by_index = _normalized_action_policy_route_map(
                action_policy_routes_by_episode[episode_index]
            )
        else:
            action_policy_route_by_index = {}
        for idx, sample in enumerate(episode):
            sample_time = int(sample.get("meta", {}).get("sample_time", 0) or 0)
            timeline.append((
                sample_time,
                episode_index,
                idx,
                sample,
                episode_start_time,
                buy_prob_by_index,
                entry_score_by_index,
                shadow_score_by_index,
                low_volume_rescue_score_by_index,
                path_state_score_by_index,
                action_policy_route_by_index,
                idx >= len(episode) - 1,
            ))

    def _timeline_sort_key(item):
        (
            sample_time,
            episode_index,
            idx,
            _sample,
            _episode_start_time,
            buy_prob_by_index,
            entry_score_by_index,
            _shadow_score_by_index,
            _low_volume_rescue_score_by_index,
            _path_state_score_by_index,
            _action_policy_route_by_index,
            _is_last_sample,
        ) = item
        if entry_ranking_mode in {"buy_prob", "entry_value"}:
            buy_prob = buy_prob_by_index.get(idx)
            is_primary_signal = buy_prob is not None and buy_prob >= threshold
            is_near_signal = not is_primary_signal and _near_threshold_prob_candidate(buy_prob)
            if not is_primary_signal and not is_near_signal:
                signal_score = -1.0
            elif entry_ranking_mode == "entry_value":
                entry_score = entry_score_by_index.get(idx)
                signal_score = float(entry_score) if entry_score is not None else -1.0
            else:
                signal_score = float(buy_prob)
            return (sample_time, -signal_score, episode_index, idx)
        return (sample_time, episode_index, idx)

    timeline.sort(key=_timeline_sort_key)

    for (
        sample_time,
        episode_index,
        idx,
        sample,
        episode_start_time,
        buy_prob_by_index,
        entry_score_by_index,
        shadow_score_by_index,
        low_volume_rescue_score_by_index,
        path_state_score_by_index,
        action_policy_route_by_index,
        is_last_sample,
    ) in timeline:
        event = _sample_to_event(sample)
        price = float(event.get("mid_price", 0.0))
        token = _sample_token(sample)
        if price <= 0.0:
            _append_equity_point()
            continue

        latest_prices[token] = price
        latest_sample_times[token] = sample_time
        position = positions.get(token)

        if position is None:
            pending_entry = pending_entries.get(token)
            if pending_entry is not None:
                due_time = int(pending_entry["due_time"])
                if "fill_time" in pending_entry:
                    fill_time = int(pending_entry.get("fill_time", sample_time) or sample_time)
                    fill_price = float(pending_entry.get("fill_price", price) or price)
                else:
                    if sample_time < due_time:
                        _append_equity_point()
                        continue
                    fill_time = int(sample_time)
                    fill_price = float(price)
                if sample_time < fill_time:
                    _append_equity_point()
                    continue
                pending_entries.pop(token, None)
                quick_profit_confirmation_reject_kind = _quick_profit_overlay_confirmation_reject_kind(
                    pending_entry,
                    fill_price,
                )
                if quick_profit_confirmation_reject_kind is not None:
                    quick_profit_overlay_confirmation_reject_count += 1
                    _append_equity_point()
                    continue
                entry_attempt_count += 1
                fill_lag_seconds = max(0, int(fill_time) - due_time)
                if entry_max_fill_wait is not None and fill_lag_seconds > entry_max_fill_wait:
                    entry_timeout_count += 1
                    _append_equity_point()
                    continue
                signal_price = float(pending_entry.get("signal_price", 0.0) or 0.0)
                if (
                    entry_price_protection is not None
                    and signal_price > 0.0
                    and float(fill_price) > signal_price * (1.0 + entry_price_protection)
                ):
                    entry_price_protection_skip_count += 1
                    _append_equity_point()
                    continue
                if not _execution_succeeds("entry", token, fill_time, idx, entry_failure_rate):
                    entry_execution_failure_count += 1
                    _append_equity_point()
                    continue
                if _open_position(
                    token,
                    fill_time,
                    idx,
                    fill_price,
                    pending_entry["buy_prob"],
                    pending_entry["episode_start_time"],
                    signal_time=pending_entry.get("signal_time"),
                    due_time=due_time,
                    entry_score=pending_entry.get("entry_score"),
                    near_threshold_rescue_used=bool(pending_entry.get("near_threshold_rescue_used", False)),
                    primary_score_rescue_used=bool(pending_entry.get("primary_score_rescue_used", False)),
                    low_volume_rescue_used=bool(pending_entry.get("low_volume_rescue_used", False)),
                    quick_profit_overlay_used=bool(pending_entry.get("quick_profit_overlay_used", False)),
                    action_policy_router_used=bool(pending_entry.get("action_policy_router_used", False)),
                    action_policy_router_route=pending_entry.get("action_policy_router_route"),
                    shadow_meta_gate_used=bool(pending_entry.get("shadow_meta_gate_used", False)),
                    path_state_meta_gate_used=bool(pending_entry.get("path_state_meta_gate_used", False)),
                    flow_activation_used=bool(pending_entry.get("flow_activation_used", False)),
                    entry_context=pending_entry.get("entry_context"),
                ):
                    position = positions.get(token)
                    if int(fill_time) >= int(sample_time):
                        _append_equity_point()
                        continue
                else:
                    _append_equity_point()
                    continue

            if position is None:
                buy_prob = buy_prob_by_index.get(idx)
                entry_score = entry_score_by_index.get(idx)
                shadow_score = shadow_score_by_index.get(idx)
                if (
                    buy_prob is not None
                    and (buy_prob >= threshold or _near_threshold_prob_candidate(buy_prob))
                ):
                    is_near_signal = buy_prob < threshold
                    primary_score_rescue_used = False
                    low_volume_rescue_used = False
                    quick_profit_overlay_used = False
                    action_policy_router_used = False
                    action_policy_router_route = None
                    shadow_meta_gate_used = False
                    path_state_meta_gate_used = False
                    flow_activation_used = False
                    filter_rejected = False
                    entry_signal_count += 1
                    near_reject_kind = None
                    if is_near_signal:
                        near_threshold_signal_count += 1
                        near_reject_kind = _near_threshold_reject_kind(sample, entry_score)
                    if is_near_signal and near_reject_kind == "score":
                        near_threshold_reject_count += 1
                        entry_score_reject_count += 1
                        filter_rejected = True
                    elif is_near_signal and near_reject_kind == "quality":
                        near_threshold_reject_count += 1
                        entry_quality_reject_count += 1
                        filter_rejected = True
                    elif not is_near_signal:
                        score_passed = _passes_entry_score_filter(entry_score)
                        quality_passed = _passes_entry_quality_filter(sample)
                        primary_rescue_candidate = (
                            (not score_passed)
                            and quality_passed
                            and _primary_score_rescue_prob_candidate(buy_prob)
                        )
                        quick_profit_overlay_candidate = (
                            (not primary_rescue_candidate)
                            and (not score_passed)
                            and quality_passed
                            and _quick_profit_overlay_prob_candidate(buy_prob)
                        )
                        shadow_meta_gate_candidate = (
                            (not primary_rescue_candidate)
                            and (not quick_profit_overlay_candidate)
                            and (not score_passed)
                            and quality_passed
                            and shadow_meta_gate_enabled
                        )
                        if primary_rescue_candidate:
                            primary_score_rescue_signal_count += 1
                            primary_rescue_reject_kind = _primary_score_rescue_reject_kind(sample, entry_score)
                            if primary_rescue_reject_kind is None:
                                primary_score_rescue_used = True
                            else:
                                primary_score_rescue_reject_count += 1
                                entry_score_reject_count += 1
                                if primary_rescue_reject_kind == "quality":
                                    entry_quality_reject_count += 1
                                filter_rejected = True
                        elif quick_profit_overlay_candidate:
                            quick_profit_overlay_signal_count += 1
                            quick_profit_overlay_reject_kind = _quick_profit_overlay_reject_kind(
                                sample,
                                buy_prob,
                                entry_score,
                            )
                            if quick_profit_overlay_reject_kind is None:
                                quick_profit_overlay_used = True
                            else:
                                quick_profit_overlay_reject_count += 1
                                entry_score_reject_count += 1
                                if quick_profit_overlay_reject_kind == "quality":
                                    entry_quality_reject_count += 1
                                filter_rejected = True
                        elif shadow_meta_gate_candidate:
                            shadow_meta_gate_signal_count += 1
                            shadow_meta_gate_reject_kind = _shadow_meta_gate_reject_kind(
                                sample,
                                buy_prob,
                                entry_score,
                                shadow_score,
                            )
                            if shadow_meta_gate_reject_kind is None:
                                shadow_meta_gate_used = True
                            else:
                                shadow_meta_gate_reject_count += 1
                                entry_score_reject_count += 1
                                if shadow_meta_gate_reject_kind == "quality":
                                    entry_quality_reject_count += 1
                                filter_rejected = True
                        elif not score_passed:
                            entry_score_reject_count += 1
                            filter_rejected = True
                        elif not quality_passed:
                            low_volume_rescue_candidate = _low_volume_rescue_candidate(sample)
                            if low_volume_rescue_candidate:
                                low_volume_rescue_signal_count += 1
                                low_volume_rescue_reject_kind = _low_volume_rescue_reject_kind(sample, buy_prob)
                                if (
                                    low_volume_rescue_reject_kind is None
                                    and low_volume_rescue_action_score_floor is not None
                                ):
                                    low_volume_rescue_score = low_volume_rescue_score_by_index.get(idx)
                                    try:
                                        low_volume_rescue_score = (
                                            None
                                            if low_volume_rescue_score is None
                                            else float(low_volume_rescue_score)
                                        )
                                    except (TypeError, ValueError):
                                        low_volume_rescue_score = None
                                    if (
                                        low_volume_rescue_score is None
                                        or not math.isfinite(low_volume_rescue_score)
                                        or low_volume_rescue_score < float(low_volume_rescue_action_score_floor)
                                    ):
                                        low_volume_rescue_reject_kind = "action_score"
                                if low_volume_rescue_reject_kind is None:
                                    low_volume_rescue_used = True
                                else:
                                    low_volume_rescue_reject_count += 1
                                    entry_quality_reject_count += 1
                                    filter_rejected = True
                            else:
                                entry_quality_reject_count += 1
                                filter_rejected = True
                    if (
                        not filter_rejected
                        and _flow_activation_prob_candidate(buy_prob)
                    ):
                        flow_activation_signal_count += 1
                        flow_activation_reject_kind = _flow_activation_reject_kind(
                            episodes[episode_index],
                            idx,
                            sample,
                            buy_prob,
                            entry_score,
                            entry_score_by_index,
                        )
                        if flow_activation_reject_kind is None:
                            flow_activation_used = True
                        else:
                            flow_activation_reject_count += 1
                            if flow_activation_reject_kind == "score":
                                entry_score_reject_count += 1
                            elif flow_activation_reject_kind == "quality":
                                entry_quality_reject_count += 1
                            filter_rejected = True
                    if (
                        not filter_rejected
                        and path_state_meta_gate_enabled
                        and not primary_score_rescue_used
                        and not low_volume_rescue_used
                        and not quick_profit_overlay_used
                        and not shadow_meta_gate_used
                    ):
                        path_state_meta_gate_signal_count += 1
                        path_state_score = path_state_score_by_index.get(idx)
                        try:
                            path_state_score = None if path_state_score is None else float(path_state_score)
                        except (TypeError, ValueError):
                            path_state_score = None
                        if (
                            path_state_score is None
                            or not math.isfinite(path_state_score)
                            or path_state_score < float(path_state_meta_gate_score_floor)
                        ):
                            path_state_meta_gate_reject_count += 1
                            filter_rejected = True
                        else:
                            path_state_meta_gate_used = True
                    if (
                        not filter_rejected
                        and action_policy_router_enabled
                        and not primary_score_rescue_used
                        and not low_volume_rescue_used
                        and not shadow_meta_gate_used
                    ):
                        action_policy_router_signal_count += 1
                        route_reject_kind, routed_policy = _action_policy_router_decision(
                            action_policy_route_by_index.get(idx)
                        )
                        if route_reject_kind is None:
                            action_policy_router_used = True
                            action_policy_router_route = routed_policy
                            if routed_policy == "quick_take_profit":
                                quick_profit_overlay_used = True
                        elif route_reject_kind == "passthrough":
                            action_policy_router_passthrough_count += 1
                        else:
                            action_policy_router_reject_count += 1
                            filter_rejected = True
                    if filter_rejected:
                        pass
                    elif (
                        not primary_score_rescue_used
                        and not low_volume_rescue_used
                        and not quick_profit_overlay_used
                        and not shadow_meta_gate_used
                        and not path_state_meta_gate_used
                        and not flow_activation_used
                        and _flow_abstention_veto_candidate(sample, buy_prob)
                    ):
                        flow_abstention_veto_signal_count += 1
                        flow_abstention_veto_reject_count += 1
                    elif (
                        not is_near_signal
                        and not primary_score_rescue_used
                        and not low_volume_rescue_used
                        and not quick_profit_overlay_used
                        and not shadow_meta_gate_used
                        and not path_state_meta_gate_used
                        and not flow_activation_used
                        and _dead_bounce_veto_candidate(sample, buy_prob, entry_score)
                    ):
                        dead_bounce_veto_signal_count += 1
                        dead_bounce_veto_reject_count += 1
                    elif _entry_slippage_risk_veto_candidate(
                        sample,
                        price_extensions_by_episode[episode_index].get("entry_slippage_risk", {}).get(idx),
                    ):
                        entry_slippage_risk_veto_signal_count += 1
                        entry_slippage_risk_veto_reject_count += 1
                    elif _late_pump_veto_candidate(
                        sample,
                        price_extensions_by_episode[episode_index].get("late_pump", {}).get(idx),
                    ):
                        late_pump_veto_signal_count += 1
                        late_pump_veto_reject_count += 1
                    elif not _can_open_position(token):
                        entry_blocked_count += 1
                    else:
                        effective_entry_delay = entry_delay
                        if quick_profit_overlay_used and quick_profit_overlay_confirmation_delay > 0:
                            effective_entry_delay += quick_profit_overlay_confirmation_delay
                        if effective_entry_delay > 0:
                            due_time = sample_time + effective_entry_delay
                            pending_entry = {
                                "due_time": due_time,
                                "signal_time": sample_time,
                                "signal_price": float(price),
                                "buy_prob": float(buy_prob),
                                "entry_score": None if entry_score is None else float(entry_score),
                                "near_threshold_rescue_used": bool(is_near_signal),
                                "primary_score_rescue_used": bool(primary_score_rescue_used),
                                "low_volume_rescue_used": bool(low_volume_rescue_used),
                                "quick_profit_overlay_used": bool(quick_profit_overlay_used),
                                "action_policy_router_used": bool(action_policy_router_used),
                                "action_policy_router_route": action_policy_router_route,
                                "shadow_meta_gate_used": bool(shadow_meta_gate_used),
                                "path_state_meta_gate_used": bool(path_state_meta_gate_used),
                                "flow_activation_used": bool(flow_activation_used),
                                "episode_start_time": episode_start_time,
                                "entry_context": _entry_signal_context(sample),
                            }
                            live_fill = _sample_live_entry_fill(sample, due_time)
                            if live_fill is not None:
                                pending_entry.update(live_fill)
                            pending_entries[token] = pending_entry
                        else:
                            entry_attempt_count += 1
                            if not _execution_succeeds("entry", token, sample_time, idx, entry_failure_rate):
                                entry_execution_failure_count += 1
                            else:
                                _open_position(
                                    token,
                                    sample_time,
                                    idx,
                                    price,
                                    buy_prob,
                                    episode_start_time,
                                    signal_time=sample_time,
                                    due_time=sample_time,
                                    entry_score=entry_score,
                                    near_threshold_rescue_used=is_near_signal,
                                    primary_score_rescue_used=primary_score_rescue_used,
                                    low_volume_rescue_used=low_volume_rescue_used,
                                    quick_profit_overlay_used=quick_profit_overlay_used,
                                    action_policy_router_used=action_policy_router_used,
                                    action_policy_router_route=action_policy_router_route,
                                    shadow_meta_gate_used=shadow_meta_gate_used,
                                    path_state_meta_gate_used=path_state_meta_gate_used,
                                    flow_activation_used=flow_activation_used,
                                    entry_context=_entry_signal_context(sample),
                                )
                _append_equity_point()
                continue

        position["min_price"] = min(position["min_price"], price)
        position["max_price"] = max(position["max_price"], price)
        pending_exit = position.get("pending_exit")
        if pending_exit is not None:
            if sample_time >= int(pending_exit["due_time"]) or is_last_sample:
                exit_due_time = int(pending_exit["due_time"])
                exit_wait = max(0, int(sample_time) - exit_due_time)
                if exit_max_fill_wait is not None and exit_wait > exit_max_fill_wait:
                    exit_timeout_count += 1
                exit_executed = _execute_exit(
                    position,
                    token,
                    sample_time,
                    idx,
                    price,
                    pending_exit["fraction"],
                    pending_exit["requested_fraction"],
                    pending_exit["reason"],
                    exit_due_time=exit_due_time,
                )
                if exit_executed and pending_exit["reason"] == "DEAD_FLOW_TIME_EXIT":
                    dead_flow_exit_count += 1
                if exit_executed and pending_exit["reason"] == "PROFIT_LOCK_TAKE_PROFIT":
                    profit_lock_take_profit_count += 1
                if exit_executed and pending_exit["reason"] == "ACTION_POLICY_CONTINUE_HOLD_TAKE_PROFIT":
                    action_policy_continue_hold_take_profit_count += 1
            _append_equity_point()
            continue

        basis_entry_price = position["cost_basis"] / max(position["size"], 1e-9)
        pnl_pct = (price - basis_entry_price) / basis_entry_price if basis_entry_price > 0.0 else 0.0
        peak_pnl_pct = (position["max_price"] / position["entry_price"]) - 1.0 if position["entry_price"] > 0.0 else 0.0
        drawdown_from_peak_pct = (price / position["max_price"]) - 1.0 if position["max_price"] > 0.0 else 0.0
        continue_hold_threshold = (
            action_policy_continue_hold_release
            if action_policy_continue_hold_release is not None
            else action_policy_continue_hold_take_profit
        )
        continue_hold_route_name = str(position.get("action_policy_router_route") or "") == "continue_hold"
        continue_hold_activation_reached = (
            action_policy_continue_hold_activation is None
            or peak_pnl_pct >= float(action_policy_continue_hold_activation)
        )
        continue_hold_route = (
            continue_hold_threshold is not None
            and continue_hold_route_name
            and continue_hold_activation_reached
        )
        continue_hold_take_profit_route = (
            action_policy_continue_hold_take_profit is not None
            and continue_hold_route
        )
        risk_exit_reason = None
        if (
            profit_lock_take_profit is not None
            and sample_time - position["entry_time"] <= float(profit_lock_max_hold)
            and pnl_pct >= float(profit_lock_take_profit)
        ):
            risk_exit_reason = "PROFIT_LOCK_TAKE_PROFIT"
        elif (
            quick_profit_overlay_take_profit is not None
            and bool(position.get("quick_profit_overlay_used", False))
            and pnl_pct >= float(quick_profit_overlay_take_profit)
        ):
            quick_profit_overlay_take_profit_count += 1
            risk_exit_reason = "QUICK_PROFIT_OVERLAY_TAKE_PROFIT"
        elif (
            low_volume_rescue_take_profit is not None
            and bool(position.get("low_volume_rescue_used", False))
            and pnl_pct >= float(low_volume_rescue_take_profit)
        ):
            risk_exit_reason = "LOW_VOLUME_TAKE_PROFIT"
        elif stop_loss is not None and pnl_pct <= float(stop_loss):
            risk_exit_reason = "STOP_LOSS"
        elif continue_hold_take_profit_route and pnl_pct >= float(action_policy_continue_hold_take_profit):
            risk_exit_reason = "ACTION_POLICY_CONTINUE_HOLD_TAKE_PROFIT"
        elif (
            quick_profit_overlay_max_hold is not None
            and bool(position.get("quick_profit_overlay_used", False))
            and sample_time - position["entry_time"] >= float(quick_profit_overlay_max_hold)
        ):
            quick_profit_overlay_timeout_count += 1
            risk_exit_reason = "QUICK_PROFIT_OVERLAY_TIME_EXIT"
        elif (
            dead_flow_exit_enabled
            and sample_time - position["entry_time"] >= float(dead_flow_exit_min_hold)
            and peak_pnl_pct <= float(dead_flow_exit_max_mfe)
        ):
            risk_exit_reason = "DEAD_FLOW_TIME_EXIT"
        elif hold_time_limit is not None and sample_time - position["entry_time"] >= hold_time_limit:
            risk_exit_reason = "TIME_EXIT"
        elif rug_sell_pressure is not None and float(event.get("sell_pressure", 0.0)) >= float(rug_sell_pressure):
            risk_exit_reason = "RUG_EXIT"
        elif (
            trailing_start_pct is not None
            and trailing_stop_pct is not None
            and peak_pnl_pct >= float(trailing_start_pct)
            and drawdown_from_peak_pct <= -float(trailing_stop_pct)
        ):
            risk_exit_reason = "TRAILING_STOP"

        if risk_exit_reason is not None:
            fraction = 1.0
            requested_fraction = 1.0
            exit_reason = risk_exit_reason
        else:
            if continue_hold_route and pnl_pct < float(continue_hold_threshold):
                action_policy_continue_hold_forced_hold_count += 1
                action = 0
            elif sample_time - position["entry_time"] < policy_hold_floor:
                action = 0
            else:
                action = _choose_exit_action(
                    event,
                    sell_policy,
                    entry_price=position["entry_price"],
                    peak_price=position["max_price"],
                    position_remaining=position["size"] / max(position["entry_size"], 1e-9),
                    entry_ts=position["entry_time"],
                    episode_start_ts=position["episode_start_time"],
                )
            action = canonical_sell_action(action, allow_partial_exits=partial_exits_enabled)
            requested_fraction = sell_fraction_for_action(action, allow_partial_exits=True)
            fraction = sell_fraction_for_action(action, allow_partial_exits=partial_exits_enabled)
            exit_reason = _sell_exit_reason(action, allow_partial_exits=partial_exits_enabled)
            if fraction <= 0.0 and is_last_sample:
                fraction = 1.0
                requested_fraction = 1.0
                exit_reason = "EPISODE_END"

        if fraction > 0.0:
            if exit_delay > 0:
                if is_last_sample:
                    exit_executed = _execute_exit(
                        position,
                        token,
                        sample_time,
                        idx,
                        price,
                        fraction,
                        requested_fraction,
                        exit_reason,
                        exit_due_time=sample_time,
                    )
                    if exit_executed and exit_reason == "DEAD_FLOW_TIME_EXIT":
                        dead_flow_exit_count += 1
                    if exit_executed and exit_reason == "PROFIT_LOCK_TAKE_PROFIT":
                        profit_lock_take_profit_count += 1
                    if exit_executed and exit_reason == "ACTION_POLICY_CONTINUE_HOLD_TAKE_PROFIT":
                        action_policy_continue_hold_take_profit_count += 1
                else:
                    position["pending_exit"] = {
                        "due_time": sample_time + exit_delay,
                        "signal_time": sample_time,
                        "fraction": float(fraction),
                        "requested_fraction": float(requested_fraction),
                        "reason": str(exit_reason),
                    }
            else:
                exit_executed = _execute_exit(
                    position,
                    token,
                    sample_time,
                    idx,
                    price,
                    fraction,
                    requested_fraction,
                    exit_reason,
                    exit_due_time=sample_time,
                )
                if exit_executed and exit_reason == "DEAD_FLOW_TIME_EXIT":
                    dead_flow_exit_count += 1
                if exit_executed and exit_reason == "PROFIT_LOCK_TAKE_PROFIT":
                    profit_lock_take_profit_count += 1
                if exit_executed and exit_reason == "ACTION_POLICY_CONTINUE_HOLD_TAKE_PROFIT":
                    action_policy_continue_hold_take_profit_count += 1

        _append_equity_point()

    for token, position in sorted(positions.items(), key=lambda item: item[1]["entry_time"]):
        final_price = latest_prices.get(token, position["entry_price"])
        final_time = latest_sample_times.get(token, position["entry_time"])
        proceeds, final_execution_price = _exit_proceeds(position["size"], final_price)
        exit_cost = min(exit_fixed_cost, proceeds)
        net_proceeds = max(0.0, proceeds - exit_cost)
        realized_cost_basis = position["cost_basis"] + float(position.get("entry_cost_basis", 0.0) or 0.0)
        position["realized_entry_fixed_cost_bnb"] = float(position.get("entry_cost_basis", 0.0) or 0.0)
        position["realized_exit_fixed_cost_bnb"] = float(exit_cost)
        _record_exit(
            position=position,
            exit_time=final_time,
            exit_index=position["entry_index"],
            exit_price=final_execution_price,
            exit_reason="REPLAY_END",
            proceeds=net_proceeds,
            realized_cost_basis=realized_cost_basis,
            size_fraction=1.0,
        )
        cash += net_proceeds
        positions.pop(token, None)
        _append_equity_point()

    final_equity = equity_curve[-1] if equity_curve else initial_equity
    total_trades = len(trade_returns)
    entry_fill_count = int(len(entry_wait_seconds))
    base_result = {
        "total_trades": int(total_trades),
        "entry_count": int(entry_count),
        "episode_count": episode_count,
        "entry_rate": float(entry_count / episode_count) if episode_count > 0 else 0.0,
        "entry_signal_count": int(entry_signal_count),
        "entry_signal_rate": float(entry_signal_count / episode_count) if episode_count > 0 else 0.0,
        "entry_attempt_count": int(entry_attempt_count),
        "entry_attempt_rate": float(entry_attempt_count / episode_count) if episode_count > 0 else 0.0,
        "entry_blocked_count": int(entry_blocked_count),
        "entry_blocked_rate": float(entry_blocked_count / entry_signal_count) if entry_signal_count > 0 else 0.0,
        "initial_equity_bnb": float(initial_equity),
        "fixed_stake_bnb": None if fixed_stake is None else float(fixed_stake),
        "stake_mode": stake_mode,
        "position_fraction": float(stake_fraction),
        "max_position_fraction": None if max_stake_fraction is None else float(max_stake_fraction),
        "fee_bps": float(fee_rate * 10000.0),
        "slippage_bps": float(slippage_rate * 10000.0),
        "entry_fixed_cost_bnb": float(entry_fixed_cost),
        "exit_fixed_cost_bnb": float(exit_fixed_cost),
        "one_entry_per_token": bool(one_entry_per_token),
        "max_trades_per_token": max_entries_per_token,
        "max_entry_age_seconds": entry_age_limit,
        "max_hold_seconds": hold_time_limit,
        "min_policy_hold_seconds": policy_hold_floor,
        "allow_partial_exits": bool(partial_exits_enabled),
        "entry_delay_seconds": entry_delay,
        "exit_delay_seconds": exit_delay,
        "max_open_positions": open_position_cap,
        "entry_ranking_mode": entry_ranking_mode,
        "min_entry_score": entry_score_floor,
        "min_entry_volume_30s": entry_volume_30s_floor,
        "min_entry_price_volatility": entry_price_volatility_floor,
        "buy_near_threshold_min_prob": near_threshold_prob_floor,
        "buy_near_min_pred_return": near_threshold_score_floor,
        "buy_near_min_entry_volume_30s": near_threshold_volume_30s_floor,
        "buy_near_min_entry_price_volatility": near_threshold_price_volatility_floor,
        "buy_near_min_age_seconds": near_threshold_min_age,
        "buy_primary_score_rescue_min_prob": primary_score_rescue_prob_floor,
        "buy_primary_score_rescue_min_pred_return": primary_score_rescue_score_floor,
        "buy_primary_score_rescue_min_entry_volume_30s": primary_score_rescue_volume_30s_floor,
        "buy_primary_score_rescue_min_entry_price_volatility": primary_score_rescue_price_volatility_floor,
        "buy_primary_score_rescue_min_age_seconds": primary_score_rescue_min_age,
        "buy_low_volume_rescue_min_prob": low_volume_rescue_prob_floor,
        "buy_low_volume_rescue_min_entry_volume_30s": low_volume_rescue_volume_floor,
        "buy_low_volume_rescue_max_entry_volume_30s": low_volume_rescue_volume_ceiling,
        "buy_low_volume_rescue_min_entry_price_volatility": low_volume_rescue_price_volatility_floor,
        "buy_low_volume_rescue_max_age_seconds": low_volume_rescue_age_ceiling,
        "buy_low_volume_rescue_take_profit_pct": low_volume_rescue_take_profit,
        "buy_low_volume_rescue_min_action_score": low_volume_rescue_action_score_floor,
        "buy_quick_profit_overlay_min_prob": quick_profit_overlay_prob_floor,
        "buy_quick_profit_overlay_min_pred_return": quick_profit_overlay_score_floor,
        "buy_quick_profit_overlay_max_pred_return": quick_profit_overlay_score_ceiling,
        "buy_quick_profit_overlay_min_entry_volume_30s": quick_profit_overlay_volume_floor,
        "buy_quick_profit_overlay_min_entry_price_volatility": quick_profit_overlay_price_volatility_floor,
        "buy_quick_profit_overlay_min_interval_regularity": quick_profit_overlay_interval_regularity_floor,
        "buy_quick_profit_overlay_max_age_seconds": quick_profit_overlay_age_ceiling,
        "buy_quick_profit_overlay_take_profit_pct": quick_profit_overlay_take_profit,
        "buy_quick_profit_overlay_max_hold_seconds": quick_profit_overlay_max_hold,
        "buy_quick_profit_overlay_min_total_buys": quick_profit_overlay_total_buys_floor,
        "buy_quick_profit_overlay_min_flow_event_count_30s": quick_profit_overlay_flow_event_floor,
        "buy_quick_profit_overlay_max_buy_sell_overlap_ratio_60s": quick_profit_overlay_overlap_ceiling,
        "buy_quick_profit_overlay_max_recent_seller_reentry_ratio_30s": quick_profit_overlay_reentry_ceiling,
        "buy_quick_profit_overlay_confirmation_delay_seconds": quick_profit_overlay_confirmation_delay,
        "buy_quick_profit_overlay_max_confirmation_drawdown_pct": quick_profit_overlay_confirmation_drawdown_ceiling,
        "buy_quick_profit_overlay_max_confirmation_chase_pct": quick_profit_overlay_confirmation_chase_ceiling,
        "buy_shadow_meta_gate_min_prob": shadow_meta_gate_prob_floor,
        "buy_shadow_meta_gate_max_entry_score": shadow_meta_gate_max_entry_score,
        "buy_shadow_meta_gate_min_entry_volume_30s": shadow_meta_gate_volume_30s_floor,
        "buy_shadow_meta_gate_min_entry_price_volatility": shadow_meta_gate_price_volatility_floor,
        "buy_shadow_meta_gate_max_age_seconds": shadow_meta_gate_age_ceiling,
        "buy_shadow_meta_gate_min_score": shadow_meta_gate_score_floor,
        "buy_path_state_meta_gate_min_score": path_state_meta_gate_score_floor,
        "buy_action_policy_router_min_confidence": action_policy_router_confidence_floor,
        "buy_action_policy_router_min_prob": action_policy_router_prob_floor,
        "buy_action_policy_router_max_pred_return": action_policy_router_score_ceiling,
        "buy_action_policy_router_skip_passthrough": action_policy_router_skip_passthrough,
        "buy_action_policy_continue_hold_activation_pct": action_policy_continue_hold_activation,
        "buy_action_policy_continue_hold_take_profit_pct": action_policy_continue_hold_take_profit,
        "buy_action_policy_continue_hold_release_pct": action_policy_continue_hold_release,
        "buy_flow_activation_min_prob": flow_activation_prob_floor,
        "buy_flow_activation_min_pred_return": flow_activation_score_floor,
        "buy_flow_activation_max_age_seconds": flow_activation_age_ceiling,
        "buy_flow_activation_lookback_seconds": flow_activation_lookback,
        "buy_flow_activation_min_volume_ramp_ratio": flow_activation_volume_ramp_ratio_floor,
        "buy_flow_activation_min_volume_ramp_delta": flow_activation_volume_ramp_delta_floor,
        "buy_flow_activation_min_pred_return_delta": flow_activation_pred_return_delta_floor,
        "buy_flow_activation_min_price_volatility_delta": flow_activation_price_volatility_delta_floor,
        "buy_flow_activation_min_current_volume_30s": flow_activation_current_volume_floor,
        "buy_dead_flow_exit_min_hold_seconds": dead_flow_exit_min_hold,
        "buy_dead_flow_exit_max_mfe_pct": dead_flow_exit_max_mfe,
        "buy_flow_abstention_min_prob": flow_abstention_prob_floor,
        "buy_flow_abstention_max_age_seconds": flow_abstention_age_ceiling,
        "buy_flow_abstention_min_entry_volume_30s": flow_abstention_volume_floor,
        "buy_flow_abstention_min_entry_price_volatility": flow_abstention_price_volatility_floor,
        "buy_flow_abstention_max_buy_sell_ratio_30s": flow_abstention_buy_sell_ratio_ceiling,
        "buy_flow_abstention_min_sell_pressure_30s": flow_abstention_sell_pressure_floor,
        "buy_flow_abstention_max_signed_imbalance_30s": flow_abstention_signed_imbalance_ceiling,
        "buy_flow_abstention_max_buy_sell_ratio_60s": flow_abstention_buy_sell_ratio_60s_ceiling,
        "buy_flow_abstention_min_sell_pressure_60s": flow_abstention_sell_pressure_60s_floor,
        "buy_flow_abstention_max_signed_imbalance_60s": flow_abstention_signed_imbalance_60s_ceiling,
        "buy_flow_abstention_min_buy_sell_overlap_ratio_60s": flow_abstention_overlap_60s_floor,
        "buy_flow_abstention_min_event_count_10s": flow_abstention_event_count_10s_floor,
        "buy_flow_abstention_min_toxic_entry_volume_30s": flow_abstention_toxic_volume_floor,
        "profit_lock_take_profit_pct": profit_lock_take_profit,
        "profit_lock_max_hold_seconds": profit_lock_max_hold,
        "buy_late_pump_veto_min_age_seconds": late_pump_veto_min_age,
        "buy_late_pump_veto_extension_window_seconds": late_pump_veto_extension_window,
        "buy_late_pump_veto_min_price_extension_pct": late_pump_veto_min_extension,
        "buy_late_pump_veto_min_drawdown_from_peak_pct": late_pump_veto_min_peak_drawdown,
        "buy_late_pump_veto_min_entry_volume_30s": late_pump_veto_volume_floor,
        "buy_late_pump_veto_min_entry_price_volatility": late_pump_veto_price_volatility_floor,
        "buy_entry_slippage_risk_veto_min_age_seconds": entry_slippage_risk_veto_min_age,
        "buy_entry_slippage_risk_veto_extension_window_seconds": entry_slippage_risk_veto_extension_window,
        "buy_entry_slippage_risk_veto_min_price_extension_pct": entry_slippage_risk_veto_min_extension,
        "buy_entry_slippage_risk_veto_min_drawdown_from_peak_pct": entry_slippage_risk_veto_min_peak_drawdown,
        "buy_entry_slippage_risk_veto_min_recent_jump_pct": entry_slippage_risk_veto_min_recent_jump,
        "buy_entry_slippage_risk_veto_min_entry_volume_30s": entry_slippage_risk_veto_volume_floor,
        "buy_entry_slippage_risk_veto_min_entry_price_volatility": entry_slippage_risk_veto_price_volatility_floor,
        "buy_dead_bounce_veto_max_age_seconds": dead_bounce_veto_max_age,
        "buy_dead_bounce_veto_min_peak_drawdown_pct": dead_bounce_veto_min_peak_drawdown,
        "buy_dead_bounce_veto_min_creator_sell_volume_bnb": dead_bounce_veto_min_creator_sell_volume,
        "buy_dead_bounce_veto_max_buy_pressure": dead_bounce_veto_max_buy_pressure,
        "buy_dead_bounce_veto_min_entry_volume_30s": dead_bounce_veto_volume_floor,
        "buy_dead_bounce_veto_min_entry_price_volatility": dead_bounce_veto_price_volatility_floor,
        "entry_max_fill_wait_seconds": entry_max_fill_wait,
        "exit_max_fill_wait_seconds": exit_max_fill_wait,
        "entry_price_protection_pct": entry_price_protection,
        "configured_entry_execution_failure_rate": float(entry_failure_rate),
        "configured_exit_execution_failure_rate": float(exit_failure_rate),
        "max_pending_entries": pending_entry_cap,
        "use_pred_return_filter": bool(entry_score_floor is not None),
        "entry_fill_count": entry_fill_count,
        "entry_fill_rate": float(entry_fill_count / entry_attempt_count) if entry_attempt_count > 0 else 0.0,
        "entry_timeout_count": int(entry_timeout_count),
        "entry_timeout_rate": float(entry_timeout_count / entry_attempt_count) if entry_attempt_count > 0 else 0.0,
        "entry_price_protection_skip_count": int(entry_price_protection_skip_count),
        "entry_price_protection_skip_rate": float(entry_price_protection_skip_count / entry_attempt_count) if entry_attempt_count > 0 else 0.0,
        "entry_execution_failure_count": int(entry_execution_failure_count),
        "entry_execution_failure_rate": float(entry_execution_failure_count / entry_attempt_count) if entry_attempt_count > 0 else 0.0,
        "entry_score_reject_count": int(entry_score_reject_count),
        "entry_score_reject_rate": float(entry_score_reject_count / entry_signal_count) if entry_signal_count > 0 else 0.0,
        "entry_quality_reject_count": int(entry_quality_reject_count),
        "entry_quality_reject_rate": float(entry_quality_reject_count / entry_signal_count) if entry_signal_count > 0 else 0.0,
        "near_threshold_signal_count": int(near_threshold_signal_count),
        "near_threshold_entry_count": int(near_threshold_entry_count),
        "near_threshold_reject_count": int(near_threshold_reject_count),
        "primary_score_rescue_signal_count": int(primary_score_rescue_signal_count),
        "primary_score_rescue_entry_count": int(primary_score_rescue_entry_count),
        "primary_score_rescue_reject_count": int(primary_score_rescue_reject_count),
        "low_volume_rescue_signal_count": int(low_volume_rescue_signal_count),
        "low_volume_rescue_entry_count": int(low_volume_rescue_entry_count),
        "low_volume_rescue_reject_count": int(low_volume_rescue_reject_count),
        "quick_profit_overlay_signal_count": int(quick_profit_overlay_signal_count),
        "quick_profit_overlay_entry_count": int(quick_profit_overlay_entry_count),
        "quick_profit_overlay_reject_count": int(quick_profit_overlay_reject_count),
        "quick_profit_overlay_confirmation_reject_count": int(quick_profit_overlay_confirmation_reject_count),
        "quick_profit_overlay_take_profit_count": int(quick_profit_overlay_take_profit_count),
        "quick_profit_overlay_timeout_count": int(quick_profit_overlay_timeout_count),
        "action_policy_router_signal_count": int(action_policy_router_signal_count),
        "action_policy_router_entry_count": int(action_policy_router_entry_count),
        "action_policy_router_reject_count": int(action_policy_router_reject_count),
        "action_policy_router_passthrough_count": int(action_policy_router_passthrough_count),
        "action_policy_router_quick_take_profit_entry_count": int(
            action_policy_router_quick_take_profit_entry_count
        ),
        "action_policy_router_continue_hold_entry_count": int(
            action_policy_router_continue_hold_entry_count
        ),
        "action_policy_continue_hold_take_profit_count": int(
            action_policy_continue_hold_take_profit_count
        ),
        "action_policy_continue_hold_forced_hold_count": int(
            action_policy_continue_hold_forced_hold_count
        ),
        "shadow_meta_gate_signal_count": int(shadow_meta_gate_signal_count),
        "shadow_meta_gate_entry_count": int(shadow_meta_gate_entry_count),
        "shadow_meta_gate_reject_count": int(shadow_meta_gate_reject_count),
        "path_state_meta_gate_signal_count": int(path_state_meta_gate_signal_count),
        "path_state_meta_gate_entry_count": int(path_state_meta_gate_entry_count),
        "path_state_meta_gate_reject_count": int(path_state_meta_gate_reject_count),
        "flow_activation_signal_count": int(flow_activation_signal_count),
        "flow_activation_entry_count": int(flow_activation_entry_count),
        "flow_activation_reject_count": int(flow_activation_reject_count),
        "dead_flow_exit_count": int(dead_flow_exit_count),
        "flow_abstention_veto_signal_count": int(flow_abstention_veto_signal_count),
        "flow_abstention_veto_reject_count": int(flow_abstention_veto_reject_count),
        "profit_lock_take_profit_count": int(profit_lock_take_profit_count),
        "late_pump_veto_signal_count": int(late_pump_veto_signal_count),
        "late_pump_veto_reject_count": int(late_pump_veto_reject_count),
        "entry_slippage_risk_veto_signal_count": int(entry_slippage_risk_veto_signal_count),
        "entry_slippage_risk_veto_reject_count": int(entry_slippage_risk_veto_reject_count),
        "dead_bounce_veto_signal_count": int(dead_bounce_veto_signal_count),
        "dead_bounce_veto_reject_count": int(dead_bounce_veto_reject_count),
        "entry_pending_at_replay_end_count": int(len(pending_entries)),
        "avg_entry_wait_seconds": float(np.mean(entry_wait_seconds)) if entry_wait_seconds else 0.0,
        "max_entry_wait_seconds": float(max(entry_wait_seconds)) if entry_wait_seconds else 0.0,
        "avg_entry_fill_lag_seconds": float(np.mean(entry_fill_lag_seconds)) if entry_fill_lag_seconds else 0.0,
        "max_entry_fill_lag_seconds": float(max(entry_fill_lag_seconds)) if entry_fill_lag_seconds else 0.0,
        "exit_attempt_count": int(exit_attempt_count),
        "exit_fill_count": int(len(exit_wait_seconds)),
        "exit_fill_rate": float(len(exit_wait_seconds) / exit_attempt_count) if exit_attempt_count > 0 else 0.0,
        "exit_execution_failure_count": int(exit_execution_failure_count),
        "exit_execution_failure_rate": float(exit_execution_failure_count / exit_attempt_count) if exit_attempt_count > 0 else 0.0,
        "exit_timeout_count": int(exit_timeout_count),
        "avg_exit_wait_seconds": float(np.mean(exit_wait_seconds)) if exit_wait_seconds else 0.0,
        "max_exit_wait_seconds": float(max(exit_wait_seconds)) if exit_wait_seconds else 0.0,
    }
    if total_trades == 0:
        result = dict(
            base_result,
            win_rate=0.0,
            net_return_pct=0.0,
            final_equity_bnb=float(final_equity),
            net_profit_bnb=float(final_equity - initial_equity),
            account_multiple=float(final_equity / initial_equity),
            max_drawdown_pct=0.0,
            sortino_ratio=0.0,
        )
        if include_trade_log:
            result["trade_log"] = trade_log
        return result

    wins = sum(1 for value in trade_returns if value > 0.0)
    result = dict(
        base_result,
        win_rate=float(wins / total_trades),
        net_return_pct=float((final_equity / initial_equity - 1.0) * 100.0),
        final_equity_bnb=float(final_equity),
        net_profit_bnb=float(final_equity - initial_equity),
        account_multiple=float(final_equity / initial_equity),
        max_drawdown_pct=float(_max_drawdown_pct(equity_curve)),
        sortino_ratio=float(_sortino_ratio_from_returns(step_returns)),
    )
    if include_trade_log:
        result["trade_log"] = trade_log
    return result


def _load_ppo_policy(policy_path):
    if not policy_path:
        return None
    try:
        from stable_baselines3 import PPO
    except Exception:
        logger.warning("stable_baselines3 unavailable; falling back to rule-based exits")
        return None

    try:
        return PPO.load(str(policy_path))
    except Exception as exc:
        logger.warning("failed to load PPO policy from %s: %s", policy_path, exc)
        return None


def _load_feature_contract_from_artifact(buy_artifact):
    feature_names = normalize_feature_names(buy_artifact.get("feature_names"))
    ignored_feature_names = buy_artifact.get("dropped_features")
    if feature_names is not None:
        return feature_names, ignored_feature_names

    feature_schema_path = buy_artifact.get("feature_schema_path")
    if not feature_schema_path:
        return None, ignored_feature_names

    schema = load_feature_schema_from_file(feature_schema_path)
    feature_names = schema["feature_names"]
    if ignored_feature_names is None:
        ignored_feature_names = schema["ignored_feature_names"]

    return feature_names, ignored_feature_names


def _feature_contract_for_replay(buy_artifact, config):
    feature_names, ignored_feature_names = _load_feature_contract_from_artifact(buy_artifact)
    if bool(config.get("include_flow_features", False)) and not requires_flow_features(feature_names):
        ignored_feature_names = sorted(
            set(normalize_ignored_feature_names(ignored_feature_names)).union(OPTIONAL_FLOW_FEATURE_NAMES)
        )
    ignored_feature_names = sorted(
        set(normalize_ignored_feature_names(ignored_feature_names)).union(REPLAY_CONTEXT_FEATURE_NAMES)
    )
    return feature_names, ignored_feature_names


def _episode_buy_probability_maxima(
    episodes,
    buy_model,
    *,
    feature_names=None,
    ignored_feature_names=None,
    max_entry_age_seconds=None,
):
    probabilities_by_episode = _episode_buy_probabilities(
        episodes,
        buy_model,
        feature_names=feature_names,
        ignored_feature_names=ignored_feature_names,
        max_entry_age_seconds=max_entry_age_seconds,
    )
    return [
        float(max(probabilities.values()))
        for probabilities in probabilities_by_episode
        if probabilities
    ]


def _risk_tune_threshold_candidates(config, current_threshold, probability_values=None):
    configured = config.get("risk_tune_thresholds")
    if configured:
        values = [float(value) for value in configured]
    else:
        values = list(np.linspace(0.5, 0.99, 11)) + [0.995, 0.999]
    values.append(float(current_threshold))

    probability_values = [
        max(0.0, min(1.0, float(value)))
        for value in (probability_values or [])
        if math.isfinite(float(value))
    ]
    if probability_values:
        raw_threshold_count = int(config.get("risk_tune_probability_threshold_count", 0) or 0)
        if bool(config.get("risk_tune_include_probability_values", False)) and raw_threshold_count <= 0:
            raw_threshold_count = int(config.get("risk_tune_max_probability_thresholds", 50) or 50)
        if raw_threshold_count > 0:
            unique_probabilities = sorted(set(probability_values))
            if len(unique_probabilities) <= raw_threshold_count:
                values.extend(unique_probabilities)
            else:
                positions = sorted(
                    {int(round(pos)) for pos in np.linspace(0, len(unique_probabilities) - 1, raw_threshold_count)}
                )
                values.extend(unique_probabilities[position] for position in positions)

        entry_rates = _coerce_float_list(
            config.get("risk_tune_candidate_entry_rates"),
            default=[0.05, 0.10, 0.15, 0.25, 0.40],
        )
        target_entry_rate = config.get("risk_tune_target_entry_rate")
        if target_entry_rate is not None:
            entry_rates.append(float(target_entry_rate))

        prob_arr = np.asarray(probability_values, dtype=float)
        for entry_rate in entry_rates:
            rate = max(0.0, min(1.0, float(entry_rate)))
            if rate <= 0.0:
                continue
            threshold = float(np.quantile(prob_arr, max(0.0, 1.0 - rate)))
            values.append(threshold)

    min_threshold = float(config.get("risk_tune_min_threshold", config.get("buy_min_threshold", 0.5)))
    return sorted(
        {
            max(0.0, min(1.0, float(value)))
            for value in values
            if float(value) >= min_threshold
        }
    )


def _replay_entry_rate(replay):
    if "entry_rate" in replay:
        return float(replay.get("entry_rate", 0.0))
    episode_count = int(replay.get("episode_count", 0) or 0)
    entry_count = int(replay.get("entry_count", replay.get("total_trades", 0)) or 0)
    return (entry_count / episode_count) if episode_count > 0 else 0.0


def _risk_tune_replay_score(config, replay):
    if "net_profit_bnb" in replay:
        base_score = float(replay.get("net_profit_bnb", 0.0))
    else:
        final_equity = max(1e-12, 1.0 + (float(replay.get("net_return_pct", 0.0)) / 100.0))
        base_score = math.log(final_equity)
    max_drawdown = float(replay.get("max_drawdown_pct", 0.0))
    preferred_drawdown = float(config.get("risk_tune_preferred_max_drawdown_pct", -30.0))
    excess_drawdown = (
        max(0.0, abs(min(0.0, max_drawdown)) - abs(min(0.0, preferred_drawdown))) / 100.0
    )
    excess_drawdown_penalty = excess_drawdown * float(config.get("risk_tune_excess_drawdown_penalty", 3.0))
    drawdown_penalty = (
        abs(min(0.0, max_drawdown)) / 100.0
    ) * float(config.get("risk_tune_drawdown_penalty", 0.0))
    entry_rate = _replay_entry_rate(replay)
    turnover_penalty = entry_rate * float(config.get("risk_tune_turnover_penalty", 0.0))
    entry_rate_penalty = 0.0
    target_entry_rate = config.get("risk_tune_target_entry_rate")
    if target_entry_rate is not None:
        entry_rate_penalty = (
            abs(entry_rate - float(target_entry_rate))
            * float(config.get("risk_tune_entry_rate_penalty", 0.0))
        )
    return float(base_score - drawdown_penalty - excess_drawdown_penalty - turnover_penalty - entry_rate_penalty)


def _tune_buy_threshold_by_replay(config, buy_artifact, ppo_artifact):
    if not bool(config.get("risk_tune_buy_threshold", False)):
        return None

    calibration_samples = list(buy_artifact.get("calibration_samples") or [])
    episodes = _build_eval_episodes(calibration_samples)
    if not episodes:
        return {
            "status": "skipped",
            "reason": "no calibration episodes",
            "threshold": float(buy_artifact.get("threshold", 1.0)),
            "candidates": [],
        }

    buy_model = buy_artifact.get("model")
    if buy_model is None:
        raise ValueError("buy artifact missing trained model")

    sell_policy = ppo_artifact.get("model")
    if sell_policy is None:
        sell_policy = _load_ppo_policy(ppo_artifact.get("policy_path"))

    feature_names, ignored_feature_names = _feature_contract_for_replay(buy_artifact, config)
    current_threshold = float(buy_artifact.get("threshold", 1.0))
    min_trades = int(config.get("risk_tune_min_trades", 10))
    max_trades = config.get("risk_tune_max_trades")
    max_trades = None if max_trades is None else int(max_trades)
    max_drawdown_pct = float(config.get("risk_tune_max_drawdown_pct", -40.0))
    min_win_rate = float(config.get("risk_tune_min_win_rate", 0.0))
    min_entry_rate = config.get("risk_tune_min_entry_rate")
    min_entry_rate = None if min_entry_rate is None else float(min_entry_rate)
    max_entry_rate = config.get("risk_tune_max_entry_rate")
    max_entry_rate = None if max_entry_rate is None else float(max_entry_rate)
    position_fraction = float(config.get("position_fraction", 1.0))
    fee_bps = float(config.get("fee_bps", 0.0))
    slippage_bps = float(config.get("slippage_bps", 0.0))
    one_entry_per_token = bool(config.get("one_entry_per_token", True))
    max_trades_per_token = config.get("max_trades_per_token")
    max_entry_age_seconds = _max_entry_age_seconds(config)
    max_hold_seconds = config.get("max_hold_seconds")
    min_policy_hold_seconds = int(config.get("min_policy_hold_seconds", 0) or 0)
    max_position_fraction = config.get("max_position_fraction", 0.1)
    allow_partial_exits = bool(config.get("allow_partial_exits", False))
    entry_delay_seconds = int(config.get("entry_delay_seconds", 0) or 0)
    exit_delay_seconds = int(config.get("exit_delay_seconds", 0) or 0)
    max_open_positions = config.get("max_open_positions")
    entry_max_fill_wait_seconds = config.get("entry_max_fill_wait_seconds")
    exit_max_fill_wait_seconds = config.get("exit_max_fill_wait_seconds")
    entry_price_protection_pct = config.get("entry_price_protection_pct")
    entry_execution_failure_rate = float(config.get("entry_execution_failure_rate", 0.0) or 0.0)
    exit_execution_failure_rate = float(config.get("exit_execution_failure_rate", 0.0) or 0.0)
    max_pending_entries = config.get("max_pending_entries")
    entry_ranking_mode = str(config.get("entry_ranking_mode", "chronological") or "chronological").strip().lower()
    min_entry_score = config.get("min_entry_score")
    min_entry_score = None if min_entry_score is None else float(min_entry_score)
    min_entry_volume_30s = config.get("min_entry_volume_30s")
    min_entry_volume_30s = None if min_entry_volume_30s is None else float(min_entry_volume_30s)
    min_entry_price_volatility = config.get("min_entry_price_volatility")
    min_entry_price_volatility = None if min_entry_price_volatility is None else float(min_entry_price_volatility)
    near_threshold_params = {
        "buy_near_threshold_min_prob": config.get("buy_near_threshold_min_prob"),
        "buy_near_min_pred_return": config.get("buy_near_min_pred_return"),
        "buy_near_min_entry_volume_30s": config.get("buy_near_min_entry_volume_30s"),
        "buy_near_min_entry_price_volatility": config.get("buy_near_min_entry_price_volatility"),
        "buy_near_min_age_seconds": config.get("buy_near_min_age_seconds"),
    }
    primary_score_rescue_params = {
        "buy_primary_score_rescue_min_prob": config.get("buy_primary_score_rescue_min_prob"),
        "buy_primary_score_rescue_min_pred_return": config.get("buy_primary_score_rescue_min_pred_return"),
        "buy_primary_score_rescue_min_entry_volume_30s": config.get("buy_primary_score_rescue_min_entry_volume_30s"),
        "buy_primary_score_rescue_min_entry_price_volatility": config.get("buy_primary_score_rescue_min_entry_price_volatility"),
        "buy_primary_score_rescue_min_age_seconds": config.get("buy_primary_score_rescue_min_age_seconds"),
    }
    initial_equity_bnb = float(config.get("initial_equity_bnb", 1.0))
    fixed_stake_bnb = config.get("fixed_stake_bnb")
    fixed_stake_bnb = None if fixed_stake_bnb is None else float(fixed_stake_bnb)
    entry_fixed_cost_bnb = float(config.get("entry_fixed_cost_bnb", 0.0) or 0.0)
    exit_fixed_cost_bnb = float(config.get("exit_fixed_cost_bnb", 0.0) or 0.0)
    buy_probabilities_by_episode = _episode_buy_probabilities(
        episodes,
        buy_model,
        feature_names=feature_names,
        ignored_feature_names=ignored_feature_names,
        max_entry_age_seconds=max_entry_age_seconds,
    )
    entry_value_artifact = buy_artifact.get("entry_value_model")
    entry_value_model = entry_value_artifact.get("model") if isinstance(entry_value_artifact, dict) else None
    near_threshold_needs_entry_value = (
        near_threshold_params["buy_near_threshold_min_prob"] is not None
        and near_threshold_params["buy_near_min_pred_return"] is not None
    )
    primary_score_rescue_needs_entry_value = (
        min_entry_score is not None
        and primary_score_rescue_params["buy_primary_score_rescue_min_prob"] is not None
        and primary_score_rescue_params["buy_primary_score_rescue_min_pred_return"] is not None
    )
    needs_entry_value_model = (
        entry_ranking_mode == "entry_value"
        or min_entry_score is not None
        or near_threshold_needs_entry_value
        or primary_score_rescue_needs_entry_value
    )
    if needs_entry_value_model and entry_value_model is None:
        raise ValueError(
            "entry_ranking_mode=entry_value, min_entry_score, near-threshold rescue, "
            "or primary score rescue requires an entry_value_model artifact"
        )
    entry_scores_by_episode = _episode_entry_scores(
        episodes,
        entry_value_model if needs_entry_value_model else None,
        feature_names=feature_names,
        ignored_feature_names=ignored_feature_names,
        max_entry_age_seconds=max_entry_age_seconds,
    )
    probability_maxima = [
        float(max(probabilities.values()))
        for probabilities in buy_probabilities_by_episode
        if probabilities
    ]
    candidates = []

    best = None
    best_score = None
    fallback = None
    fallback_score = None
    for threshold in _risk_tune_threshold_candidates(config, current_threshold, probability_maxima):
        replay = _run_eval_replay(
            episodes,
            buy_model,
            threshold,
            sell_policy,
            feature_names=feature_names,
            ignored_feature_names=ignored_feature_names,
            stop_loss=float(config.get("stop_loss", -0.50)),
            position_fraction=position_fraction,
            include_trade_log=False,
            trailing_start_pct=config.get("trailing_start_pct"),
            trailing_stop_pct=config.get("trailing_stop_pct"),
            rug_sell_pressure=config.get("rug_sell_pressure"),
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            one_entry_per_token=one_entry_per_token,
            max_trades_per_token=max_trades_per_token,
            max_entry_age_seconds=max_entry_age_seconds,
            max_hold_seconds=max_hold_seconds,
            min_policy_hold_seconds=min_policy_hold_seconds,
            max_position_fraction=max_position_fraction,
            allow_partial_exits=allow_partial_exits,
            buy_probabilities_by_episode=buy_probabilities_by_episode,
            entry_scores_by_episode=entry_scores_by_episode,
            entry_delay_seconds=entry_delay_seconds,
            exit_delay_seconds=exit_delay_seconds,
            max_open_positions=max_open_positions,
            initial_equity_bnb=initial_equity_bnb,
            fixed_stake_bnb=fixed_stake_bnb,
            entry_fixed_cost_bnb=entry_fixed_cost_bnb,
            exit_fixed_cost_bnb=exit_fixed_cost_bnb,
            entry_max_fill_wait_seconds=entry_max_fill_wait_seconds,
            exit_max_fill_wait_seconds=exit_max_fill_wait_seconds,
            entry_price_protection_pct=entry_price_protection_pct,
            entry_execution_failure_rate=entry_execution_failure_rate,
            exit_execution_failure_rate=exit_execution_failure_rate,
            max_pending_entries=max_pending_entries,
            entry_ranking_mode=entry_ranking_mode,
            min_entry_score=min_entry_score,
            min_entry_volume_30s=min_entry_volume_30s,
            min_entry_price_volatility=min_entry_price_volatility,
            **near_threshold_params,
            **primary_score_rescue_params,
        )
        feasible = (
            int(replay["total_trades"]) >= min_trades
            and (max_trades is None or int(replay["total_trades"]) <= max_trades)
            and (min_entry_rate is None or float(replay.get("entry_rate", 0.0)) >= min_entry_rate)
            and (max_entry_rate is None or float(replay.get("entry_rate", 0.0)) <= max_entry_rate)
            and float(replay["max_drawdown_pct"]) >= max_drawdown_pct
            and float(replay["win_rate"]) >= min_win_rate
        )
        score_value = _risk_tune_replay_score(config, replay)
        candidate = {
            "threshold": float(threshold),
            "feasible": bool(feasible),
            "score": float(score_value),
            "replay": replay,
        }
        candidates.append(candidate)
        if int(replay["total_trades"]) > 0:
            candidate_fallback_score = (
                score_value,
                float(replay.get("net_profit_bnb", replay.get("net_return_pct", 0.0))),
                float(replay["max_drawdown_pct"]),
                float(replay["win_rate"]),
                -int(replay["total_trades"]),
                float(threshold),
            )
            if fallback_score is None or candidate_fallback_score > fallback_score:
                fallback_score = candidate_fallback_score
                fallback = candidate
        if feasible:
            score = (
                score_value,
                float(replay["max_drawdown_pct"]),
                float(replay["win_rate"]),
                -int(replay["total_trades"]),
                float(threshold),
            )
            if best_score is None or score > best_score:
                best_score = score
                best = candidate

    if best is None:
        constraints = {
            "min_trades": min_trades,
            "max_trades": max_trades,
            "max_drawdown_pct": max_drawdown_pct,
            "min_win_rate": min_win_rate,
            "min_entry_rate": min_entry_rate,
            "max_entry_rate": max_entry_rate,
            "position_fraction": position_fraction,
            "fee_bps": fee_bps,
            "slippage_bps": slippage_bps,
            "one_entry_per_token": one_entry_per_token,
            "max_trades_per_token": max_trades_per_token,
            "max_entry_age_seconds": max_entry_age_seconds,
            "min_entry_unique_buyers": int(config.get("min_entry_unique_buyers", 3) or 3),
            "min_entry_buy_count": int(config.get("min_entry_buy_count", 5) or 5),
            "max_hold_seconds": max_hold_seconds,
            "min_policy_hold_seconds": min_policy_hold_seconds,
            "max_position_fraction": None if max_position_fraction is None else float(max_position_fraction),
            "initial_equity_bnb": initial_equity_bnb,
            "fixed_stake_bnb": fixed_stake_bnb,
            "entry_fixed_cost_bnb": entry_fixed_cost_bnb,
            "exit_fixed_cost_bnb": exit_fixed_cost_bnb,
            "allow_partial_exits": allow_partial_exits,
            "entry_delay_seconds": entry_delay_seconds,
            "exit_delay_seconds": exit_delay_seconds,
            "max_open_positions": _open_position_cap(max_open_positions),
            "entry_max_fill_wait_seconds": None if entry_max_fill_wait_seconds is None else int(entry_max_fill_wait_seconds),
            "exit_max_fill_wait_seconds": None if exit_max_fill_wait_seconds is None else int(exit_max_fill_wait_seconds),
            "entry_price_protection_pct": None if entry_price_protection_pct is None else float(entry_price_protection_pct),
            "entry_execution_failure_rate": float(entry_execution_failure_rate),
            "exit_execution_failure_rate": float(exit_execution_failure_rate),
            "max_pending_entries": None if max_pending_entries is None else int(max_pending_entries),
            **near_threshold_params,
            **primary_score_rescue_params,
            "target_entry_rate": config.get("risk_tune_target_entry_rate"),
            "entry_rate_penalty": config.get("risk_tune_entry_rate_penalty"),
            "candidate_entry_rates": _coerce_float_list(config.get("risk_tune_candidate_entry_rates")),
        }
        if fallback is not None and bool(config.get("risk_tune_fallback_if_infeasible", True)):
            return {
                "status": "fallback_selected",
                "threshold": float(fallback["threshold"]),
                "previous_threshold": current_threshold,
                "feasible": False,
                "fallback_reason": "no_candidate_satisfied_all_constraints",
                "constraints": constraints,
                "candidates": candidates,
                "replay": fallback["replay"],
            }
        blocked_threshold = 1.0 if bool(config.get("risk_tune_block_if_infeasible", True)) else current_threshold
        return {
            "status": "infeasible",
            "threshold": float(blocked_threshold),
            "previous_threshold": current_threshold,
            "constraints": constraints,
            "candidates": candidates,
            "replay": {
                "total_trades": 0,
                "entry_count": 0,
                "win_rate": 0.0,
                "net_return_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "sortino_ratio": 0.0,
                "final_equity_bnb": initial_equity_bnb,
                "net_profit_bnb": 0.0,
                "account_multiple": 1.0,
                "initial_equity_bnb": initial_equity_bnb,
                "fixed_stake_bnb": fixed_stake_bnb,
                "position_fraction": position_fraction,
                "fee_bps": fee_bps,
                "slippage_bps": slippage_bps,
                "one_entry_per_token": one_entry_per_token,
                "max_trades_per_token": max_trades_per_token,
                "max_entry_age_seconds": max_entry_age_seconds,
                "max_hold_seconds": max_hold_seconds,
                "min_policy_hold_seconds": min_policy_hold_seconds,
                "max_position_fraction": None if max_position_fraction is None else float(max_position_fraction),
                "allow_partial_exits": allow_partial_exits,
                "entry_delay_seconds": entry_delay_seconds,
                "exit_delay_seconds": exit_delay_seconds,
                "max_open_positions": _open_position_cap(max_open_positions),
                "entry_max_fill_wait_seconds": None if entry_max_fill_wait_seconds is None else int(entry_max_fill_wait_seconds),
                "exit_max_fill_wait_seconds": None if exit_max_fill_wait_seconds is None else int(exit_max_fill_wait_seconds),
                "entry_price_protection_pct": None if entry_price_protection_pct is None else float(entry_price_protection_pct),
                "episode_count": int(len(episodes)),
                "entry_rate": 0.0,
            },
        }

    return {
        "status": "selected",
        "threshold": float(best["threshold"]),
        "previous_threshold": current_threshold,
        "constraints": {
            "min_trades": min_trades,
            "max_trades": max_trades,
            "max_drawdown_pct": max_drawdown_pct,
            "min_win_rate": min_win_rate,
            "min_entry_rate": min_entry_rate,
            "max_entry_rate": max_entry_rate,
            "position_fraction": position_fraction,
            "fee_bps": fee_bps,
            "slippage_bps": slippage_bps,
            "one_entry_per_token": one_entry_per_token,
            "max_trades_per_token": max_trades_per_token,
            "max_entry_age_seconds": max_entry_age_seconds,
            "min_entry_unique_buyers": int(config.get("min_entry_unique_buyers", 3) or 3),
            "min_entry_buy_count": int(config.get("min_entry_buy_count", 5) or 5),
            "max_hold_seconds": max_hold_seconds,
            "min_policy_hold_seconds": min_policy_hold_seconds,
            "max_position_fraction": None if max_position_fraction is None else float(max_position_fraction),
            "initial_equity_bnb": initial_equity_bnb,
            "fixed_stake_bnb": fixed_stake_bnb,
            "allow_partial_exits": allow_partial_exits,
            "entry_delay_seconds": entry_delay_seconds,
            "exit_delay_seconds": exit_delay_seconds,
            "max_open_positions": _open_position_cap(max_open_positions),
            "entry_max_fill_wait_seconds": None if entry_max_fill_wait_seconds is None else int(entry_max_fill_wait_seconds),
            "exit_max_fill_wait_seconds": None if exit_max_fill_wait_seconds is None else int(exit_max_fill_wait_seconds),
            "entry_price_protection_pct": None if entry_price_protection_pct is None else float(entry_price_protection_pct),
            "entry_execution_failure_rate": float(entry_execution_failure_rate),
            "exit_execution_failure_rate": float(exit_execution_failure_rate),
            "max_pending_entries": None if max_pending_entries is None else int(max_pending_entries),
            **near_threshold_params,
            **primary_score_rescue_params,
            "target_entry_rate": config.get("risk_tune_target_entry_rate"),
            "entry_rate_penalty": config.get("risk_tune_entry_rate_penalty"),
            "candidate_entry_rates": _coerce_float_list(config.get("risk_tune_candidate_entry_rates")),
        },
        "candidates": candidates,
        "replay": best["replay"],
    }


def run_ab_evaluation(config, buy_artifact, ppo_artifact, bc_artifact):
    eval_samples = list(config.get("eval_samples") or [])
    episodes = _build_eval_episodes(eval_samples)
    if not episodes:
        raise ValueError("no eval episodes could be built from eval samples")

    buy_model = buy_artifact.get("model")
    threshold = float(buy_artifact.get("threshold", 1.0))
    if buy_model is None:
        raise ValueError("buy artifact missing trained model")

    sell_policy = ppo_artifact.get("model")
    if sell_policy is None:
        sell_policy = _load_ppo_policy(ppo_artifact.get("policy_path"))

    feature_names, ignored_feature_names = _feature_contract_for_replay(buy_artifact, config)

    position_fraction = float(config.get("position_fraction", 1.0))
    include_trade_log = bool(config.get("include_trade_log", False))
    fee_bps = float(config.get("fee_bps", 0.0))
    slippage_bps = float(config.get("slippage_bps", 0.0))
    one_entry_per_token = bool(config.get("one_entry_per_token", True))
    max_trades_per_token = config.get("max_trades_per_token")
    max_entry_age_seconds = _max_entry_age_seconds(config)
    max_hold_seconds = config.get("max_hold_seconds")
    min_policy_hold_seconds = int(config.get("min_policy_hold_seconds", 0) or 0)
    max_position_fraction = config.get("max_position_fraction", 0.1)
    allow_partial_exits = bool(config.get("allow_partial_exits", False))
    entry_delay_seconds = int(config.get("entry_delay_seconds", 0) or 0)
    exit_delay_seconds = int(config.get("exit_delay_seconds", 0) or 0)
    max_open_positions = config.get("max_open_positions")
    entry_max_fill_wait_seconds = config.get("entry_max_fill_wait_seconds")
    exit_max_fill_wait_seconds = config.get("exit_max_fill_wait_seconds")
    entry_price_protection_pct = config.get("entry_price_protection_pct")
    entry_execution_failure_rate = float(config.get("entry_execution_failure_rate", 0.0) or 0.0)
    exit_execution_failure_rate = float(config.get("exit_execution_failure_rate", 0.0) or 0.0)
    max_pending_entries = config.get("max_pending_entries")
    entry_ranking_mode = str(config.get("entry_ranking_mode", "chronological") or "chronological").strip().lower()
    min_entry_score = config.get("min_entry_score")
    min_entry_score = None if min_entry_score is None else float(min_entry_score)
    min_entry_volume_30s = config.get("min_entry_volume_30s")
    min_entry_volume_30s = None if min_entry_volume_30s is None else float(min_entry_volume_30s)
    min_entry_price_volatility = config.get("min_entry_price_volatility")
    min_entry_price_volatility = None if min_entry_price_volatility is None else float(min_entry_price_volatility)
    near_threshold_params = {
        "buy_near_threshold_min_prob": config.get("buy_near_threshold_min_prob"),
        "buy_near_min_pred_return": config.get("buy_near_min_pred_return"),
        "buy_near_min_entry_volume_30s": config.get("buy_near_min_entry_volume_30s"),
        "buy_near_min_entry_price_volatility": config.get("buy_near_min_entry_price_volatility"),
        "buy_near_min_age_seconds": config.get("buy_near_min_age_seconds"),
    }
    primary_score_rescue_params = {
        "buy_primary_score_rescue_min_prob": config.get("buy_primary_score_rescue_min_prob"),
        "buy_primary_score_rescue_min_pred_return": config.get("buy_primary_score_rescue_min_pred_return"),
        "buy_primary_score_rescue_min_entry_volume_30s": config.get("buy_primary_score_rescue_min_entry_volume_30s"),
        "buy_primary_score_rescue_min_entry_price_volatility": config.get("buy_primary_score_rescue_min_entry_price_volatility"),
        "buy_primary_score_rescue_min_age_seconds": config.get("buy_primary_score_rescue_min_age_seconds"),
    }
    low_volume_rescue_params = {
        "buy_low_volume_rescue_min_prob": config.get("buy_low_volume_rescue_min_prob"),
        "buy_low_volume_rescue_min_entry_volume_30s": config.get("buy_low_volume_rescue_min_entry_volume_30s"),
        "buy_low_volume_rescue_max_entry_volume_30s": config.get("buy_low_volume_rescue_max_entry_volume_30s"),
        "buy_low_volume_rescue_min_entry_price_volatility": config.get("buy_low_volume_rescue_min_entry_price_volatility"),
        "buy_low_volume_rescue_max_age_seconds": config.get("buy_low_volume_rescue_max_age_seconds"),
        "buy_low_volume_rescue_take_profit_pct": config.get("buy_low_volume_rescue_take_profit_pct"),
        "buy_low_volume_rescue_min_action_score": config.get("buy_low_volume_rescue_min_action_score"),
    }
    quick_profit_overlay_params = {
        "buy_quick_profit_overlay_min_prob": config.get("buy_quick_profit_overlay_min_prob"),
        "buy_quick_profit_overlay_min_pred_return": config.get("buy_quick_profit_overlay_min_pred_return"),
        "buy_quick_profit_overlay_max_pred_return": config.get("buy_quick_profit_overlay_max_pred_return"),
        "buy_quick_profit_overlay_min_entry_volume_30s": config.get("buy_quick_profit_overlay_min_entry_volume_30s"),
        "buy_quick_profit_overlay_min_entry_price_volatility": config.get("buy_quick_profit_overlay_min_entry_price_volatility"),
        "buy_quick_profit_overlay_min_interval_regularity": config.get("buy_quick_profit_overlay_min_interval_regularity"),
        "buy_quick_profit_overlay_max_age_seconds": config.get("buy_quick_profit_overlay_max_age_seconds"),
        "buy_quick_profit_overlay_take_profit_pct": config.get("buy_quick_profit_overlay_take_profit_pct"),
        "buy_quick_profit_overlay_max_hold_seconds": config.get("buy_quick_profit_overlay_max_hold_seconds"),
        "buy_quick_profit_overlay_min_total_buys": config.get("buy_quick_profit_overlay_min_total_buys"),
        "buy_quick_profit_overlay_min_flow_event_count_30s": config.get("buy_quick_profit_overlay_min_flow_event_count_30s"),
        "buy_quick_profit_overlay_max_buy_sell_overlap_ratio_60s": config.get("buy_quick_profit_overlay_max_buy_sell_overlap_ratio_60s"),
        "buy_quick_profit_overlay_max_recent_seller_reentry_ratio_30s": config.get("buy_quick_profit_overlay_max_recent_seller_reentry_ratio_30s"),
        "buy_quick_profit_overlay_confirmation_delay_seconds": config.get("buy_quick_profit_overlay_confirmation_delay_seconds"),
        "buy_quick_profit_overlay_max_confirmation_drawdown_pct": config.get("buy_quick_profit_overlay_max_confirmation_drawdown_pct"),
        "buy_quick_profit_overlay_max_confirmation_chase_pct": config.get("buy_quick_profit_overlay_max_confirmation_chase_pct"),
    }
    shadow_meta_gate_params = {
        "buy_shadow_meta_gate_min_prob": config.get("buy_shadow_meta_gate_min_prob"),
        "buy_shadow_meta_gate_max_entry_score": config.get("buy_shadow_meta_gate_max_entry_score"),
        "buy_shadow_meta_gate_min_entry_volume_30s": config.get("buy_shadow_meta_gate_min_entry_volume_30s"),
        "buy_shadow_meta_gate_min_entry_price_volatility": config.get("buy_shadow_meta_gate_min_entry_price_volatility"),
        "buy_shadow_meta_gate_max_age_seconds": config.get("buy_shadow_meta_gate_max_age_seconds"),
        "buy_shadow_meta_gate_min_score": config.get("buy_shadow_meta_gate_min_score"),
    }
    path_state_meta_gate_params = {
        "buy_path_state_meta_gate_min_score": config.get("buy_path_state_meta_gate_min_score"),
    }
    action_policy_router_params = {
        "buy_action_policy_router_min_confidence": config.get("buy_action_policy_router_min_confidence"),
        "buy_action_policy_router_min_prob": config.get("buy_action_policy_router_min_prob"),
        "buy_action_policy_router_max_pred_return": config.get("buy_action_policy_router_max_pred_return"),
        "buy_action_policy_router_skip_passthrough": config.get("buy_action_policy_router_skip_passthrough"),
    }
    action_policy_continue_hold_params = {
        "buy_action_policy_continue_hold_activation_pct": config.get(
            "buy_action_policy_continue_hold_activation_pct"
        ),
        "buy_action_policy_continue_hold_take_profit_pct": config.get(
            "buy_action_policy_continue_hold_take_profit_pct"
        ),
        "buy_action_policy_continue_hold_release_pct": config.get(
            "buy_action_policy_continue_hold_release_pct"
        ),
    }
    flow_activation_params = {
        "buy_flow_activation_min_prob": config.get("buy_flow_activation_min_prob"),
        "buy_flow_activation_min_pred_return": config.get("buy_flow_activation_min_pred_return"),
        "buy_flow_activation_max_age_seconds": config.get("buy_flow_activation_max_age_seconds"),
        "buy_flow_activation_lookback_seconds": config.get("buy_flow_activation_lookback_seconds"),
        "buy_flow_activation_min_volume_ramp_ratio": config.get("buy_flow_activation_min_volume_ramp_ratio"),
        "buy_flow_activation_min_volume_ramp_delta": config.get("buy_flow_activation_min_volume_ramp_delta"),
        "buy_flow_activation_min_pred_return_delta": config.get("buy_flow_activation_min_pred_return_delta"),
        "buy_flow_activation_min_price_volatility_delta": config.get("buy_flow_activation_min_price_volatility_delta"),
        "buy_flow_activation_min_current_volume_30s": config.get("buy_flow_activation_min_current_volume_30s"),
        "buy_dead_flow_exit_min_hold_seconds": config.get("buy_dead_flow_exit_min_hold_seconds"),
        "buy_dead_flow_exit_max_mfe_pct": config.get("buy_dead_flow_exit_max_mfe_pct"),
    }
    flow_abstention_params = {
        "buy_flow_abstention_min_prob": config.get("buy_flow_abstention_min_prob"),
        "buy_flow_abstention_max_age_seconds": config.get("buy_flow_abstention_max_age_seconds"),
        "buy_flow_abstention_min_entry_volume_30s": config.get("buy_flow_abstention_min_entry_volume_30s"),
        "buy_flow_abstention_min_entry_price_volatility": config.get("buy_flow_abstention_min_entry_price_volatility"),
        "buy_flow_abstention_max_buy_sell_ratio_30s": config.get("buy_flow_abstention_max_buy_sell_ratio_30s"),
        "buy_flow_abstention_min_sell_pressure_30s": config.get("buy_flow_abstention_min_sell_pressure_30s"),
        "buy_flow_abstention_max_signed_imbalance_30s": config.get("buy_flow_abstention_max_signed_imbalance_30s"),
        "buy_flow_abstention_max_buy_sell_ratio_60s": config.get("buy_flow_abstention_max_buy_sell_ratio_60s"),
        "buy_flow_abstention_min_sell_pressure_60s": config.get("buy_flow_abstention_min_sell_pressure_60s"),
        "buy_flow_abstention_max_signed_imbalance_60s": config.get("buy_flow_abstention_max_signed_imbalance_60s"),
        "buy_flow_abstention_min_buy_sell_overlap_ratio_60s": config.get(
            "buy_flow_abstention_min_buy_sell_overlap_ratio_60s"
        ),
        "buy_flow_abstention_min_event_count_10s": config.get("buy_flow_abstention_min_event_count_10s"),
        "buy_flow_abstention_min_toxic_entry_volume_30s": config.get(
            "buy_flow_abstention_min_toxic_entry_volume_30s"
        ),
    }
    profit_lock_params = {
        "profit_lock_take_profit_pct": config.get("profit_lock_take_profit_pct"),
        "profit_lock_max_hold_seconds": config.get("profit_lock_max_hold_seconds"),
    }
    late_pump_veto_params = {
        "buy_late_pump_veto_min_age_seconds": config.get("buy_late_pump_veto_min_age_seconds"),
        "buy_late_pump_veto_extension_window_seconds": config.get("buy_late_pump_veto_extension_window_seconds"),
        "buy_late_pump_veto_min_price_extension_pct": config.get("buy_late_pump_veto_min_price_extension_pct"),
        "buy_late_pump_veto_min_drawdown_from_peak_pct": config.get("buy_late_pump_veto_min_drawdown_from_peak_pct"),
        "buy_late_pump_veto_min_entry_volume_30s": config.get("buy_late_pump_veto_min_entry_volume_30s"),
        "buy_late_pump_veto_min_entry_price_volatility": config.get("buy_late_pump_veto_min_entry_price_volatility"),
    }
    entry_slippage_risk_veto_params = {
        "buy_entry_slippage_risk_veto_min_age_seconds": config.get("buy_entry_slippage_risk_veto_min_age_seconds"),
        "buy_entry_slippage_risk_veto_extension_window_seconds": config.get(
            "buy_entry_slippage_risk_veto_extension_window_seconds"
        ),
        "buy_entry_slippage_risk_veto_min_price_extension_pct": config.get(
            "buy_entry_slippage_risk_veto_min_price_extension_pct"
        ),
        "buy_entry_slippage_risk_veto_min_drawdown_from_peak_pct": config.get(
            "buy_entry_slippage_risk_veto_min_drawdown_from_peak_pct"
        ),
        "buy_entry_slippage_risk_veto_min_recent_jump_pct": config.get(
            "buy_entry_slippage_risk_veto_min_recent_jump_pct"
        ),
        "buy_entry_slippage_risk_veto_min_entry_volume_30s": config.get(
            "buy_entry_slippage_risk_veto_min_entry_volume_30s"
        ),
        "buy_entry_slippage_risk_veto_min_entry_price_volatility": config.get(
            "buy_entry_slippage_risk_veto_min_entry_price_volatility"
        ),
    }
    dead_bounce_veto_params = {
        "buy_dead_bounce_veto_max_age_seconds": config.get("buy_dead_bounce_veto_max_age_seconds"),
        "buy_dead_bounce_veto_min_peak_drawdown_pct": config.get("buy_dead_bounce_veto_min_peak_drawdown_pct"),
        "buy_dead_bounce_veto_min_creator_sell_volume_bnb": config.get(
            "buy_dead_bounce_veto_min_creator_sell_volume_bnb"
        ),
        "buy_dead_bounce_veto_max_buy_pressure": config.get("buy_dead_bounce_veto_max_buy_pressure"),
        "buy_dead_bounce_veto_min_entry_volume_30s": config.get("buy_dead_bounce_veto_min_entry_volume_30s"),
        "buy_dead_bounce_veto_min_entry_price_volatility": config.get(
            "buy_dead_bounce_veto_min_entry_price_volatility"
        ),
    }
    initial_equity_bnb = float(config.get("initial_equity_bnb", 1.0))
    fixed_stake_bnb = config.get("fixed_stake_bnb")
    fixed_stake_bnb = None if fixed_stake_bnb is None else float(fixed_stake_bnb)
    entry_fixed_cost_bnb = float(config.get("entry_fixed_cost_bnb", 0.0) or 0.0)
    exit_fixed_cost_bnb = float(config.get("exit_fixed_cost_bnb", 0.0) or 0.0)
    buy_probabilities_by_episode = _episode_buy_probabilities(
        episodes,
        buy_model,
        feature_names=feature_names,
        ignored_feature_names=ignored_feature_names,
        max_entry_age_seconds=max_entry_age_seconds,
    )
    entry_value_artifact = buy_artifact.get("entry_value_model")
    entry_value_model = entry_value_artifact.get("model") if isinstance(entry_value_artifact, dict) else None
    near_threshold_needs_entry_value = (
        near_threshold_params["buy_near_threshold_min_prob"] is not None
        and near_threshold_params["buy_near_min_pred_return"] is not None
    )
    primary_score_rescue_needs_entry_value = (
        min_entry_score is not None
        and primary_score_rescue_params["buy_primary_score_rescue_min_prob"] is not None
        and primary_score_rescue_params["buy_primary_score_rescue_min_pred_return"] is not None
    )
    quick_profit_overlay_needs_entry_value = (
        min_entry_score is not None
        and quick_profit_overlay_params["buy_quick_profit_overlay_min_prob"] is not None
        and quick_profit_overlay_params["buy_quick_profit_overlay_min_pred_return"] is not None
    )
    shadow_meta_gate_needs_entry_value = (
        min_entry_score is not None
        and shadow_meta_gate_params["buy_shadow_meta_gate_min_score"] is not None
    )
    flow_activation_needs_entry_value = (
        flow_activation_params["buy_flow_activation_min_prob"] is not None
        and (
            flow_activation_params["buy_flow_activation_min_pred_return"] is not None
            or flow_activation_params["buy_flow_activation_min_pred_return_delta"] is not None
        )
    )
    needs_entry_value_model = (
        entry_ranking_mode == "entry_value"
        or min_entry_score is not None
        or near_threshold_needs_entry_value
        or primary_score_rescue_needs_entry_value
        or quick_profit_overlay_needs_entry_value
        or shadow_meta_gate_needs_entry_value
        or flow_activation_needs_entry_value
    )
    if needs_entry_value_model and entry_value_model is None:
        raise ValueError(
            "entry_ranking_mode=entry_value, min_entry_score, near-threshold rescue, "
            "primary score rescue, quick-profit overlay, shadow meta gate, or flow activation requires an "
            "entry_value_model artifact"
        )
    entry_scores_by_episode = _episode_entry_scores(
        episodes,
        entry_value_model if needs_entry_value_model else None,
        feature_names=feature_names,
        ignored_feature_names=ignored_feature_names,
        max_entry_age_seconds=max_entry_age_seconds,
    )
    buy_probabilities_by_episode_id = {
        id(episode): probabilities
        for episode, probabilities in zip(episodes, buy_probabilities_by_episode)
    }
    entry_scores_by_episode_id = {
        id(episode): scores
        for episode, scores in zip(episodes, entry_scores_by_episode)
    }
    shadow_scores_by_episode = list(config.get("shadow_scores_by_episode") or [])
    if not shadow_scores_by_episode:
        shadow_scores_by_episode = [{} for _episode in episodes]
    if len(shadow_scores_by_episode) < len(episodes):
        shadow_scores_by_episode.extend({} for _episode in episodes[len(shadow_scores_by_episode):])
    elif len(shadow_scores_by_episode) > len(episodes):
        shadow_scores_by_episode = shadow_scores_by_episode[:len(episodes)]
    shadow_scores_by_episode_id = {
        id(episode): scores
        for episode, scores in zip(episodes, shadow_scores_by_episode)
    }
    low_volume_rescue_scores_by_episode = list(config.get("low_volume_rescue_scores_by_episode") or [])
    if not low_volume_rescue_scores_by_episode:
        low_volume_rescue_scores_by_episode = [{} for _episode in episodes]
    if len(low_volume_rescue_scores_by_episode) < len(episodes):
        low_volume_rescue_scores_by_episode.extend(
            {} for _episode in episodes[len(low_volume_rescue_scores_by_episode):]
        )
    elif len(low_volume_rescue_scores_by_episode) > len(episodes):
        low_volume_rescue_scores_by_episode = low_volume_rescue_scores_by_episode[:len(episodes)]
    low_volume_rescue_scores_by_episode_id = {
        id(episode): scores
        for episode, scores in zip(episodes, low_volume_rescue_scores_by_episode)
    }
    path_state_gate_enabled = (
        _optional_nonnegative_finite(
            path_state_meta_gate_params["buy_path_state_meta_gate_min_score"],
            "buy_path_state_meta_gate_min_score",
        )
        is not None
    )
    path_state_scores_by_episode = _validated_path_state_scores_by_episode(
        episodes,
        config.get("path_state_scores_by_episode"),
        gate_enabled=path_state_gate_enabled,
    )
    path_state_scores_by_episode_id = {
        id(episode): scores
        for episode, scores in zip(episodes, path_state_scores_by_episode)
    }
    action_policy_router_confidence_floor = _optional_runtime_probability(
        config.get("buy_action_policy_router_min_confidence"),
        "buy_action_policy_router_min_confidence",
    )
    action_policy_router_enabled = action_policy_router_confidence_floor is not None
    action_policy_routes_by_episode = _validated_action_policy_routes_by_episode(
        episodes,
        config.get("action_policy_routes_by_episode"),
        gate_enabled=action_policy_router_enabled,
    )
    action_policy_routes_by_episode_id = {
        id(episode): routes
        for episode, routes in zip(episodes, action_policy_routes_by_episode)
    }
    runtime_replay = _run_eval_replay(
        episodes,
        buy_model,
        threshold,
        sell_policy,
        feature_names=feature_names,
        ignored_feature_names=ignored_feature_names,
        stop_loss=float(config.get("stop_loss", -0.50)),
        position_fraction=position_fraction,
        include_trade_log=include_trade_log,
        trailing_start_pct=config.get("trailing_start_pct"),
        trailing_stop_pct=config.get("trailing_stop_pct"),
        rug_sell_pressure=config.get("rug_sell_pressure"),
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        one_entry_per_token=one_entry_per_token,
        max_trades_per_token=max_trades_per_token,
        max_entry_age_seconds=max_entry_age_seconds,
        max_hold_seconds=max_hold_seconds,
        min_policy_hold_seconds=min_policy_hold_seconds,
        max_position_fraction=max_position_fraction,
        allow_partial_exits=allow_partial_exits,
        buy_probabilities_by_episode=buy_probabilities_by_episode,
        entry_scores_by_episode=entry_scores_by_episode,
        shadow_scores_by_episode=shadow_scores_by_episode,
        low_volume_rescue_scores_by_episode=low_volume_rescue_scores_by_episode,
        path_state_scores_by_episode=path_state_scores_by_episode,
        action_policy_routes_by_episode=action_policy_routes_by_episode,
        entry_delay_seconds=entry_delay_seconds,
        exit_delay_seconds=exit_delay_seconds,
        max_open_positions=max_open_positions,
        initial_equity_bnb=initial_equity_bnb,
        fixed_stake_bnb=fixed_stake_bnb,
        entry_fixed_cost_bnb=entry_fixed_cost_bnb,
        exit_fixed_cost_bnb=exit_fixed_cost_bnb,
        entry_max_fill_wait_seconds=entry_max_fill_wait_seconds,
        exit_max_fill_wait_seconds=exit_max_fill_wait_seconds,
        entry_price_protection_pct=entry_price_protection_pct,
        entry_execution_failure_rate=entry_execution_failure_rate,
        exit_execution_failure_rate=exit_execution_failure_rate,
        max_pending_entries=max_pending_entries,
        entry_ranking_mode=entry_ranking_mode,
        min_entry_score=min_entry_score,
        min_entry_volume_30s=min_entry_volume_30s,
        min_entry_price_volatility=min_entry_price_volatility,
        **near_threshold_params,
        **primary_score_rescue_params,
        **low_volume_rescue_params,
        **quick_profit_overlay_params,
        **shadow_meta_gate_params,
        **path_state_meta_gate_params,
        **action_policy_router_params,
        **action_policy_continue_hold_params,
        **flow_activation_params,
        **flow_abstention_params,
        **profit_lock_params,
        **late_pump_veto_params,
        **entry_slippage_risk_veto_params,
        **dead_bounce_veto_params,
    )
    all_in_replay = None
    if not bool(config.get("skip_all_in_replay", False)):
        all_in_replay = _run_eval_replay(
            episodes,
            buy_model,
            threshold,
            sell_policy,
            feature_names=feature_names,
            ignored_feature_names=ignored_feature_names,
            stop_loss=float(config.get("stop_loss", -0.50)),
            position_fraction=1.0,
            include_trade_log=False,
            trailing_start_pct=config.get("trailing_start_pct"),
            trailing_stop_pct=config.get("trailing_stop_pct"),
            rug_sell_pressure=config.get("rug_sell_pressure"),
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            one_entry_per_token=one_entry_per_token,
            max_trades_per_token=max_trades_per_token,
            max_entry_age_seconds=max_entry_age_seconds,
            max_hold_seconds=max_hold_seconds,
            min_policy_hold_seconds=min_policy_hold_seconds,
            max_position_fraction=None,
            allow_partial_exits=allow_partial_exits,
            buy_probabilities_by_episode=buy_probabilities_by_episode,
            entry_scores_by_episode=entry_scores_by_episode,
            shadow_scores_by_episode=shadow_scores_by_episode,
            low_volume_rescue_scores_by_episode=low_volume_rescue_scores_by_episode,
            path_state_scores_by_episode=path_state_scores_by_episode,
            action_policy_routes_by_episode=action_policy_routes_by_episode,
            entry_delay_seconds=entry_delay_seconds,
            exit_delay_seconds=exit_delay_seconds,
            max_open_positions=max_open_positions,
            initial_equity_bnb=initial_equity_bnb,
            fixed_stake_bnb=fixed_stake_bnb,
            entry_fixed_cost_bnb=entry_fixed_cost_bnb,
            exit_fixed_cost_bnb=exit_fixed_cost_bnb,
            entry_max_fill_wait_seconds=entry_max_fill_wait_seconds,
            exit_max_fill_wait_seconds=exit_max_fill_wait_seconds,
            entry_price_protection_pct=entry_price_protection_pct,
            entry_execution_failure_rate=entry_execution_failure_rate,
            exit_execution_failure_rate=exit_execution_failure_rate,
            max_pending_entries=max_pending_entries,
            entry_ranking_mode=entry_ranking_mode,
            min_entry_score=min_entry_score,
            min_entry_volume_30s=min_entry_volume_30s,
            min_entry_price_volatility=min_entry_price_volatility,
            **near_threshold_params,
            **primary_score_rescue_params,
            **low_volume_rescue_params,
            **quick_profit_overlay_params,
            **shadow_meta_gate_params,
            **path_state_meta_gate_params,
            **action_policy_router_params,
            **action_policy_continue_hold_params,
            **flow_activation_params,
            **flow_abstention_params,
            **profit_lock_params,
            **late_pump_veto_params,
            **entry_slippage_risk_veto_params,
            **dead_bounce_veto_params,
        )

    result = {
        "total_trades": int(runtime_replay["total_trades"]),
        "entry_count": int(runtime_replay.get("entry_count", runtime_replay["total_trades"])),
        "entry_rate": float(runtime_replay.get("entry_rate", 0.0)),
        "win_rate": float(runtime_replay["win_rate"]),
        "net_return_pct": float(runtime_replay["net_return_pct"]),
        "max_drawdown_pct": float(runtime_replay["max_drawdown_pct"]),
        "sortino_ratio": float(runtime_replay["sortino_ratio"]),
        "buy_threshold": threshold,
        "stop_loss": float(config.get("stop_loss", -0.50)),
        "position_fraction": position_fraction,
        "trailing_start_pct": config.get("trailing_start_pct"),
        "trailing_stop_pct": config.get("trailing_stop_pct"),
        "rug_sell_pressure": config.get("rug_sell_pressure"),
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "one_entry_per_token": one_entry_per_token,
        "max_trades_per_token": max_trades_per_token,
        "max_entry_age_seconds": max_entry_age_seconds,
        "min_entry_unique_buyers": int(config.get("min_entry_unique_buyers", 3) or 3),
        "min_entry_buy_count": int(config.get("min_entry_buy_count", 5) or 5),
        "max_hold_seconds": max_hold_seconds,
        "min_policy_hold_seconds": min_policy_hold_seconds,
        "max_position_fraction": None if max_position_fraction is None else float(max_position_fraction),
        "initial_equity_bnb": initial_equity_bnb,
        "fixed_stake_bnb": fixed_stake_bnb,
        "entry_fixed_cost_bnb": entry_fixed_cost_bnb,
        "exit_fixed_cost_bnb": exit_fixed_cost_bnb,
        "stake_mode": runtime_replay.get("stake_mode", "fraction"),
        "final_equity_bnb": float(runtime_replay.get("final_equity_bnb", 1.0)),
        "net_profit_bnb": float(runtime_replay.get("net_profit_bnb", 0.0)),
        "account_multiple": float(runtime_replay.get("account_multiple", 1.0)),
        "allow_partial_exits": allow_partial_exits,
        "entry_delay_seconds": entry_delay_seconds,
        "exit_delay_seconds": exit_delay_seconds,
        "max_open_positions": runtime_replay.get("max_open_positions"),
        "entry_ranking_mode": entry_ranking_mode,
        "min_entry_score": min_entry_score,
        "min_entry_volume_30s": min_entry_volume_30s,
        "min_entry_price_volatility": min_entry_price_volatility,
        "buy_near_threshold_min_prob": runtime_replay.get("buy_near_threshold_min_prob"),
        "buy_near_min_pred_return": runtime_replay.get("buy_near_min_pred_return"),
        "buy_near_min_entry_volume_30s": runtime_replay.get("buy_near_min_entry_volume_30s"),
        "buy_near_min_entry_price_volatility": runtime_replay.get("buy_near_min_entry_price_volatility"),
        "buy_near_min_age_seconds": runtime_replay.get("buy_near_min_age_seconds"),
        "buy_primary_score_rescue_min_prob": runtime_replay.get("buy_primary_score_rescue_min_prob"),
        "buy_primary_score_rescue_min_pred_return": runtime_replay.get("buy_primary_score_rescue_min_pred_return"),
        "buy_primary_score_rescue_min_entry_volume_30s": runtime_replay.get("buy_primary_score_rescue_min_entry_volume_30s"),
        "buy_primary_score_rescue_min_entry_price_volatility": runtime_replay.get("buy_primary_score_rescue_min_entry_price_volatility"),
        "buy_primary_score_rescue_min_age_seconds": runtime_replay.get("buy_primary_score_rescue_min_age_seconds"),
        "buy_low_volume_rescue_min_prob": runtime_replay.get("buy_low_volume_rescue_min_prob"),
        "buy_low_volume_rescue_min_entry_volume_30s": runtime_replay.get("buy_low_volume_rescue_min_entry_volume_30s"),
        "buy_low_volume_rescue_max_entry_volume_30s": runtime_replay.get("buy_low_volume_rescue_max_entry_volume_30s"),
        "buy_low_volume_rescue_min_entry_price_volatility": runtime_replay.get("buy_low_volume_rescue_min_entry_price_volatility"),
        "buy_low_volume_rescue_max_age_seconds": runtime_replay.get("buy_low_volume_rescue_max_age_seconds"),
        "buy_low_volume_rescue_take_profit_pct": runtime_replay.get("buy_low_volume_rescue_take_profit_pct"),
        "buy_low_volume_rescue_min_action_score": runtime_replay.get("buy_low_volume_rescue_min_action_score"),
        "buy_quick_profit_overlay_min_prob": runtime_replay.get("buy_quick_profit_overlay_min_prob"),
        "buy_quick_profit_overlay_min_pred_return": runtime_replay.get("buy_quick_profit_overlay_min_pred_return"),
        "buy_quick_profit_overlay_max_pred_return": runtime_replay.get("buy_quick_profit_overlay_max_pred_return"),
        "buy_quick_profit_overlay_min_entry_volume_30s": runtime_replay.get("buy_quick_profit_overlay_min_entry_volume_30s"),
        "buy_quick_profit_overlay_min_entry_price_volatility": runtime_replay.get("buy_quick_profit_overlay_min_entry_price_volatility"),
        "buy_quick_profit_overlay_min_interval_regularity": runtime_replay.get("buy_quick_profit_overlay_min_interval_regularity"),
        "buy_quick_profit_overlay_max_age_seconds": runtime_replay.get("buy_quick_profit_overlay_max_age_seconds"),
        "buy_quick_profit_overlay_take_profit_pct": runtime_replay.get("buy_quick_profit_overlay_take_profit_pct"),
        "buy_quick_profit_overlay_max_hold_seconds": runtime_replay.get("buy_quick_profit_overlay_max_hold_seconds"),
        "buy_quick_profit_overlay_min_total_buys": runtime_replay.get("buy_quick_profit_overlay_min_total_buys"),
        "buy_quick_profit_overlay_min_flow_event_count_30s": runtime_replay.get("buy_quick_profit_overlay_min_flow_event_count_30s"),
        "buy_quick_profit_overlay_max_buy_sell_overlap_ratio_60s": runtime_replay.get("buy_quick_profit_overlay_max_buy_sell_overlap_ratio_60s"),
        "buy_quick_profit_overlay_max_recent_seller_reentry_ratio_30s": runtime_replay.get("buy_quick_profit_overlay_max_recent_seller_reentry_ratio_30s"),
        "buy_quick_profit_overlay_confirmation_delay_seconds": runtime_replay.get("buy_quick_profit_overlay_confirmation_delay_seconds"),
        "buy_quick_profit_overlay_max_confirmation_drawdown_pct": runtime_replay.get("buy_quick_profit_overlay_max_confirmation_drawdown_pct"),
        "buy_quick_profit_overlay_max_confirmation_chase_pct": runtime_replay.get("buy_quick_profit_overlay_max_confirmation_chase_pct"),
        "buy_shadow_meta_gate_min_prob": runtime_replay.get("buy_shadow_meta_gate_min_prob"),
        "buy_shadow_meta_gate_max_entry_score": runtime_replay.get("buy_shadow_meta_gate_max_entry_score"),
        "buy_shadow_meta_gate_min_entry_volume_30s": runtime_replay.get("buy_shadow_meta_gate_min_entry_volume_30s"),
        "buy_shadow_meta_gate_min_entry_price_volatility": runtime_replay.get("buy_shadow_meta_gate_min_entry_price_volatility"),
        "buy_shadow_meta_gate_max_age_seconds": runtime_replay.get("buy_shadow_meta_gate_max_age_seconds"),
        "buy_shadow_meta_gate_min_score": runtime_replay.get("buy_shadow_meta_gate_min_score"),
        "buy_path_state_meta_gate_min_score": runtime_replay.get("buy_path_state_meta_gate_min_score"),
        "buy_action_policy_router_min_confidence": runtime_replay.get("buy_action_policy_router_min_confidence"),
        "buy_action_policy_router_min_prob": runtime_replay.get("buy_action_policy_router_min_prob"),
        "buy_action_policy_router_max_pred_return": runtime_replay.get(
            "buy_action_policy_router_max_pred_return"
        ),
        "buy_action_policy_router_skip_passthrough": runtime_replay.get(
            "buy_action_policy_router_skip_passthrough"
        ),
        "buy_action_policy_continue_hold_activation_pct": runtime_replay.get(
            "buy_action_policy_continue_hold_activation_pct"
        ),
        "buy_action_policy_continue_hold_take_profit_pct": runtime_replay.get(
            "buy_action_policy_continue_hold_take_profit_pct"
        ),
        "buy_action_policy_continue_hold_release_pct": runtime_replay.get(
            "buy_action_policy_continue_hold_release_pct"
        ),
        "buy_flow_activation_min_prob": runtime_replay.get("buy_flow_activation_min_prob"),
        "buy_flow_activation_min_pred_return": runtime_replay.get("buy_flow_activation_min_pred_return"),
        "buy_flow_activation_max_age_seconds": runtime_replay.get("buy_flow_activation_max_age_seconds"),
        "buy_flow_activation_lookback_seconds": runtime_replay.get("buy_flow_activation_lookback_seconds"),
        "buy_flow_activation_min_volume_ramp_ratio": runtime_replay.get("buy_flow_activation_min_volume_ramp_ratio"),
        "buy_flow_activation_min_volume_ramp_delta": runtime_replay.get("buy_flow_activation_min_volume_ramp_delta"),
        "buy_flow_activation_min_pred_return_delta": runtime_replay.get("buy_flow_activation_min_pred_return_delta"),
        "buy_flow_activation_min_price_volatility_delta": runtime_replay.get("buy_flow_activation_min_price_volatility_delta"),
        "buy_flow_activation_min_current_volume_30s": runtime_replay.get("buy_flow_activation_min_current_volume_30s"),
        "buy_dead_flow_exit_min_hold_seconds": runtime_replay.get("buy_dead_flow_exit_min_hold_seconds"),
        "buy_dead_flow_exit_max_mfe_pct": runtime_replay.get("buy_dead_flow_exit_max_mfe_pct"),
        "buy_flow_abstention_min_prob": runtime_replay.get("buy_flow_abstention_min_prob"),
        "buy_flow_abstention_max_age_seconds": runtime_replay.get("buy_flow_abstention_max_age_seconds"),
        "buy_flow_abstention_min_entry_volume_30s": runtime_replay.get("buy_flow_abstention_min_entry_volume_30s"),
        "buy_flow_abstention_min_entry_price_volatility": runtime_replay.get(
            "buy_flow_abstention_min_entry_price_volatility"
        ),
        "buy_flow_abstention_max_buy_sell_ratio_30s": runtime_replay.get(
            "buy_flow_abstention_max_buy_sell_ratio_30s"
        ),
        "buy_flow_abstention_min_sell_pressure_30s": runtime_replay.get(
            "buy_flow_abstention_min_sell_pressure_30s"
        ),
        "buy_flow_abstention_max_signed_imbalance_30s": runtime_replay.get(
            "buy_flow_abstention_max_signed_imbalance_30s"
        ),
        "buy_flow_abstention_max_buy_sell_ratio_60s": runtime_replay.get(
            "buy_flow_abstention_max_buy_sell_ratio_60s"
        ),
        "buy_flow_abstention_min_sell_pressure_60s": runtime_replay.get(
            "buy_flow_abstention_min_sell_pressure_60s"
        ),
        "buy_flow_abstention_max_signed_imbalance_60s": runtime_replay.get(
            "buy_flow_abstention_max_signed_imbalance_60s"
        ),
        "buy_flow_abstention_min_buy_sell_overlap_ratio_60s": runtime_replay.get(
            "buy_flow_abstention_min_buy_sell_overlap_ratio_60s"
        ),
        "buy_flow_abstention_min_event_count_10s": runtime_replay.get(
            "buy_flow_abstention_min_event_count_10s"
        ),
        "buy_flow_abstention_min_toxic_entry_volume_30s": runtime_replay.get(
            "buy_flow_abstention_min_toxic_entry_volume_30s"
        ),
        "profit_lock_take_profit_pct": runtime_replay.get("profit_lock_take_profit_pct"),
        "profit_lock_max_hold_seconds": runtime_replay.get("profit_lock_max_hold_seconds"),
        "buy_late_pump_veto_min_age_seconds": runtime_replay.get("buy_late_pump_veto_min_age_seconds"),
        "buy_late_pump_veto_extension_window_seconds": runtime_replay.get("buy_late_pump_veto_extension_window_seconds"),
        "buy_late_pump_veto_min_price_extension_pct": runtime_replay.get("buy_late_pump_veto_min_price_extension_pct"),
        "buy_late_pump_veto_min_drawdown_from_peak_pct": runtime_replay.get("buy_late_pump_veto_min_drawdown_from_peak_pct"),
        "buy_late_pump_veto_min_entry_volume_30s": runtime_replay.get("buy_late_pump_veto_min_entry_volume_30s"),
        "buy_late_pump_veto_min_entry_price_volatility": runtime_replay.get("buy_late_pump_veto_min_entry_price_volatility"),
        "buy_entry_slippage_risk_veto_min_age_seconds": runtime_replay.get(
            "buy_entry_slippage_risk_veto_min_age_seconds"
        ),
        "buy_entry_slippage_risk_veto_extension_window_seconds": runtime_replay.get(
            "buy_entry_slippage_risk_veto_extension_window_seconds"
        ),
        "buy_entry_slippage_risk_veto_min_price_extension_pct": runtime_replay.get(
            "buy_entry_slippage_risk_veto_min_price_extension_pct"
        ),
        "buy_entry_slippage_risk_veto_min_drawdown_from_peak_pct": runtime_replay.get(
            "buy_entry_slippage_risk_veto_min_drawdown_from_peak_pct"
        ),
        "buy_entry_slippage_risk_veto_min_recent_jump_pct": runtime_replay.get(
            "buy_entry_slippage_risk_veto_min_recent_jump_pct"
        ),
        "buy_entry_slippage_risk_veto_min_entry_volume_30s": runtime_replay.get(
            "buy_entry_slippage_risk_veto_min_entry_volume_30s"
        ),
        "buy_entry_slippage_risk_veto_min_entry_price_volatility": runtime_replay.get(
            "buy_entry_slippage_risk_veto_min_entry_price_volatility"
        ),
        "buy_dead_bounce_veto_max_age_seconds": runtime_replay.get("buy_dead_bounce_veto_max_age_seconds"),
        "buy_dead_bounce_veto_min_peak_drawdown_pct": runtime_replay.get("buy_dead_bounce_veto_min_peak_drawdown_pct"),
        "buy_dead_bounce_veto_min_creator_sell_volume_bnb": runtime_replay.get("buy_dead_bounce_veto_min_creator_sell_volume_bnb"),
        "buy_dead_bounce_veto_max_buy_pressure": runtime_replay.get("buy_dead_bounce_veto_max_buy_pressure"),
        "buy_dead_bounce_veto_min_entry_volume_30s": runtime_replay.get("buy_dead_bounce_veto_min_entry_volume_30s"),
        "buy_dead_bounce_veto_min_entry_price_volatility": runtime_replay.get("buy_dead_bounce_veto_min_entry_price_volatility"),
        "use_pred_return_filter": bool(min_entry_score is not None),
        "entry_max_fill_wait_seconds": None if entry_max_fill_wait_seconds is None else int(entry_max_fill_wait_seconds),
        "exit_max_fill_wait_seconds": None if exit_max_fill_wait_seconds is None else int(exit_max_fill_wait_seconds),
        "entry_price_protection_pct": None if entry_price_protection_pct is None else float(entry_price_protection_pct),
        "entry_execution_failure_rate": float(entry_execution_failure_rate),
        "exit_execution_failure_rate": float(exit_execution_failure_rate),
        "max_pending_entries": None if max_pending_entries is None else int(max_pending_entries),
        "entry_signal_count": int(runtime_replay.get("entry_signal_count", 0)),
        "entry_signal_rate": float(runtime_replay.get("entry_signal_rate", 0.0)),
        "entry_attempt_count": int(runtime_replay.get("entry_attempt_count", 0)),
        "entry_attempt_rate": float(runtime_replay.get("entry_attempt_rate", 0.0)),
        "entry_blocked_count": int(runtime_replay.get("entry_blocked_count", 0)),
        "entry_blocked_rate": float(runtime_replay.get("entry_blocked_rate", 0.0)),
        "entry_fill_count": int(runtime_replay.get("entry_fill_count", 0)),
        "entry_fill_rate": float(runtime_replay.get("entry_fill_rate", 0.0)),
        "entry_timeout_count": int(runtime_replay.get("entry_timeout_count", 0)),
        "entry_timeout_rate": float(runtime_replay.get("entry_timeout_rate", 0.0)),
        "entry_price_protection_skip_count": int(runtime_replay.get("entry_price_protection_skip_count", 0)),
        "entry_price_protection_skip_rate": float(runtime_replay.get("entry_price_protection_skip_rate", 0.0)),
        "entry_execution_failure_count": int(runtime_replay.get("entry_execution_failure_count", 0)),
        "entry_execution_failure_observed_rate": float(runtime_replay.get("entry_execution_failure_rate", 0.0)),
        "entry_score_reject_count": int(runtime_replay.get("entry_score_reject_count", 0)),
        "entry_score_reject_rate": float(runtime_replay.get("entry_score_reject_rate", 0.0)),
        "entry_quality_reject_count": int(runtime_replay.get("entry_quality_reject_count", 0)),
        "entry_quality_reject_rate": float(runtime_replay.get("entry_quality_reject_rate", 0.0)),
        "near_threshold_signal_count": int(runtime_replay.get("near_threshold_signal_count", 0)),
        "near_threshold_entry_count": int(runtime_replay.get("near_threshold_entry_count", 0)),
        "near_threshold_reject_count": int(runtime_replay.get("near_threshold_reject_count", 0)),
        "primary_score_rescue_signal_count": int(runtime_replay.get("primary_score_rescue_signal_count", 0)),
        "primary_score_rescue_entry_count": int(runtime_replay.get("primary_score_rescue_entry_count", 0)),
        "primary_score_rescue_reject_count": int(runtime_replay.get("primary_score_rescue_reject_count", 0)),
        "low_volume_rescue_signal_count": int(runtime_replay.get("low_volume_rescue_signal_count", 0)),
        "low_volume_rescue_entry_count": int(runtime_replay.get("low_volume_rescue_entry_count", 0)),
        "low_volume_rescue_reject_count": int(runtime_replay.get("low_volume_rescue_reject_count", 0)),
        "quick_profit_overlay_signal_count": int(runtime_replay.get("quick_profit_overlay_signal_count", 0)),
        "quick_profit_overlay_entry_count": int(runtime_replay.get("quick_profit_overlay_entry_count", 0)),
        "quick_profit_overlay_reject_count": int(runtime_replay.get("quick_profit_overlay_reject_count", 0)),
        "quick_profit_overlay_confirmation_reject_count": int(
            runtime_replay.get("quick_profit_overlay_confirmation_reject_count", 0)
        ),
        "quick_profit_overlay_take_profit_count": int(runtime_replay.get("quick_profit_overlay_take_profit_count", 0)),
        "quick_profit_overlay_timeout_count": int(runtime_replay.get("quick_profit_overlay_timeout_count", 0)),
        "action_policy_router_signal_count": int(runtime_replay.get("action_policy_router_signal_count", 0)),
        "action_policy_router_entry_count": int(runtime_replay.get("action_policy_router_entry_count", 0)),
        "action_policy_router_reject_count": int(runtime_replay.get("action_policy_router_reject_count", 0)),
        "action_policy_router_passthrough_count": int(
            runtime_replay.get("action_policy_router_passthrough_count", 0)
        ),
        "action_policy_router_quick_take_profit_entry_count": int(
            runtime_replay.get("action_policy_router_quick_take_profit_entry_count", 0)
        ),
        "action_policy_router_continue_hold_entry_count": int(
            runtime_replay.get("action_policy_router_continue_hold_entry_count", 0)
        ),
        "action_policy_continue_hold_take_profit_count": int(
            runtime_replay.get("action_policy_continue_hold_take_profit_count", 0)
        ),
        "action_policy_continue_hold_forced_hold_count": int(
            runtime_replay.get("action_policy_continue_hold_forced_hold_count", 0)
        ),
        "shadow_meta_gate_signal_count": int(runtime_replay.get("shadow_meta_gate_signal_count", 0)),
        "shadow_meta_gate_entry_count": int(runtime_replay.get("shadow_meta_gate_entry_count", 0)),
        "shadow_meta_gate_reject_count": int(runtime_replay.get("shadow_meta_gate_reject_count", 0)),
        "path_state_meta_gate_signal_count": int(runtime_replay.get("path_state_meta_gate_signal_count", 0)),
        "path_state_meta_gate_entry_count": int(runtime_replay.get("path_state_meta_gate_entry_count", 0)),
        "path_state_meta_gate_reject_count": int(runtime_replay.get("path_state_meta_gate_reject_count", 0)),
        "flow_activation_signal_count": int(runtime_replay.get("flow_activation_signal_count", 0)),
        "flow_activation_entry_count": int(runtime_replay.get("flow_activation_entry_count", 0)),
        "flow_activation_reject_count": int(runtime_replay.get("flow_activation_reject_count", 0)),
        "dead_flow_exit_count": int(runtime_replay.get("dead_flow_exit_count", 0)),
        "flow_abstention_veto_signal_count": int(runtime_replay.get("flow_abstention_veto_signal_count", 0)),
        "flow_abstention_veto_reject_count": int(runtime_replay.get("flow_abstention_veto_reject_count", 0)),
        "profit_lock_take_profit_count": int(runtime_replay.get("profit_lock_take_profit_count", 0)),
        "late_pump_veto_signal_count": int(runtime_replay.get("late_pump_veto_signal_count", 0)),
        "late_pump_veto_reject_count": int(runtime_replay.get("late_pump_veto_reject_count", 0)),
        "entry_slippage_risk_veto_signal_count": int(runtime_replay.get("entry_slippage_risk_veto_signal_count", 0)),
        "entry_slippage_risk_veto_reject_count": int(runtime_replay.get("entry_slippage_risk_veto_reject_count", 0)),
        "dead_bounce_veto_signal_count": int(runtime_replay.get("dead_bounce_veto_signal_count", 0)),
        "dead_bounce_veto_reject_count": int(runtime_replay.get("dead_bounce_veto_reject_count", 0)),
        "entry_pending_at_replay_end_count": int(runtime_replay.get("entry_pending_at_replay_end_count", 0)),
        "avg_entry_wait_seconds": float(runtime_replay.get("avg_entry_wait_seconds", 0.0)),
        "max_entry_wait_seconds": float(runtime_replay.get("max_entry_wait_seconds", 0.0)),
        "avg_entry_fill_lag_seconds": float(runtime_replay.get("avg_entry_fill_lag_seconds", 0.0)),
        "max_entry_fill_lag_seconds": float(runtime_replay.get("max_entry_fill_lag_seconds", 0.0)),
        "exit_attempt_count": int(runtime_replay.get("exit_attempt_count", 0)),
        "exit_fill_count": int(runtime_replay.get("exit_fill_count", 0)),
        "exit_fill_rate": float(runtime_replay.get("exit_fill_rate", 0.0)),
        "exit_execution_failure_count": int(runtime_replay.get("exit_execution_failure_count", 0)),
        "exit_execution_failure_observed_rate": float(runtime_replay.get("exit_execution_failure_rate", 0.0)),
        "exit_timeout_count": int(runtime_replay.get("exit_timeout_count", 0)),
        "avg_exit_wait_seconds": float(runtime_replay.get("avg_exit_wait_seconds", 0.0)),
        "max_exit_wait_seconds": float(runtime_replay.get("max_exit_wait_seconds", 0.0)),
        "runtime_replay": {key: value for key, value in runtime_replay.items() if key != "trade_log"},
        **({} if all_in_replay is None else {"all_in_replay": all_in_replay}),
        "sell_episode_count": int(len(episodes)),
        "bc_samples": int(bc_artifact.get("bc_samples", 0)),
        "ppo_total_timesteps": int(ppo_artifact.get("total_timesteps", 0)),
        "train_file_count": int(config.get("train_file_count", 0)),
        "eval_file_count": int(config.get("eval_file_count", 0)),
        "overlap_token_count": int(config.get("overlap_token_count", 0)),
        "raw_overlap_token_count": int(config.get("raw_overlap_token_count", config.get("overlap_token_count", 0))),
        "excluded_eval_token_count": int(config.get("excluded_eval_token_count", 0)),
        "pipeline_status": "ok",
    }
    preferred_max_drawdown_pct = float(config.get("preferred_max_drawdown_pct", -30.0))
    result["preferred_max_drawdown_pct"] = preferred_max_drawdown_pct
    result["drawdown_within_preferred_limit"] = bool(
        float(result["max_drawdown_pct"]) >= preferred_max_drawdown_pct
    )
    result["top_trade_profit_concentration"] = _trade_profit_concentration(runtime_replay.get("trade_log", []))
    if include_trade_log:
        result["trade_log"] = runtime_replay.get("trade_log", [])

    stress_replays = []
    for scenario in _stress_replay_scenarios(config):
        scenario_replay = _run_eval_replay(
            episodes,
            buy_model,
            threshold,
            sell_policy,
            feature_names=feature_names,
            ignored_feature_names=ignored_feature_names,
            stop_loss=float(scenario.get("stop_loss", config.get("stop_loss", -0.50))),
            position_fraction=float(scenario.get("position_fraction", position_fraction)),
            include_trade_log=False,
            trailing_start_pct=scenario.get("trailing_start_pct", config.get("trailing_start_pct")),
            trailing_stop_pct=scenario.get("trailing_stop_pct", config.get("trailing_stop_pct")),
            rug_sell_pressure=scenario.get("rug_sell_pressure", config.get("rug_sell_pressure")),
            fee_bps=float(scenario.get("fee_bps", fee_bps)),
            slippage_bps=float(scenario.get("slippage_bps", slippage_bps)),
            one_entry_per_token=bool(scenario.get("one_entry_per_token", one_entry_per_token)),
            max_trades_per_token=scenario.get("max_trades_per_token", max_trades_per_token),
            max_entry_age_seconds=scenario.get("max_entry_age_seconds", max_entry_age_seconds),
            max_hold_seconds=scenario.get("max_hold_seconds", max_hold_seconds),
            min_policy_hold_seconds=int(scenario.get("min_policy_hold_seconds", min_policy_hold_seconds) or 0),
            max_position_fraction=scenario.get("max_position_fraction", max_position_fraction),
            allow_partial_exits=bool(scenario.get("allow_partial_exits", allow_partial_exits)),
            buy_probabilities_by_episode=buy_probabilities_by_episode,
            entry_scores_by_episode=entry_scores_by_episode,
            shadow_scores_by_episode=shadow_scores_by_episode,
            low_volume_rescue_scores_by_episode=low_volume_rescue_scores_by_episode,
            path_state_scores_by_episode=path_state_scores_by_episode,
            action_policy_routes_by_episode=action_policy_routes_by_episode,
            entry_delay_seconds=int(scenario.get("entry_delay_seconds", entry_delay_seconds) or 0),
            exit_delay_seconds=int(scenario.get("exit_delay_seconds", exit_delay_seconds) or 0),
            max_open_positions=scenario.get("max_open_positions", max_open_positions),
            initial_equity_bnb=float(scenario.get("initial_equity_bnb", initial_equity_bnb)),
            fixed_stake_bnb=scenario.get("fixed_stake_bnb", fixed_stake_bnb),
            entry_fixed_cost_bnb=float(scenario.get("entry_fixed_cost_bnb", entry_fixed_cost_bnb) or 0.0),
            exit_fixed_cost_bnb=float(scenario.get("exit_fixed_cost_bnb", exit_fixed_cost_bnb) or 0.0),
            entry_max_fill_wait_seconds=scenario.get("entry_max_fill_wait_seconds", entry_max_fill_wait_seconds),
            exit_max_fill_wait_seconds=scenario.get("exit_max_fill_wait_seconds", exit_max_fill_wait_seconds),
            entry_price_protection_pct=scenario.get("entry_price_protection_pct", entry_price_protection_pct),
            entry_execution_failure_rate=float(scenario.get("entry_execution_failure_rate", entry_execution_failure_rate) or 0.0),
            exit_execution_failure_rate=float(scenario.get("exit_execution_failure_rate", exit_execution_failure_rate) or 0.0),
            max_pending_entries=scenario.get("max_pending_entries", max_pending_entries),
            entry_ranking_mode=str(scenario.get("entry_ranking_mode", entry_ranking_mode) or "chronological"),
            min_entry_score=scenario.get("min_entry_score", min_entry_score),
            min_entry_volume_30s=scenario.get("min_entry_volume_30s", min_entry_volume_30s),
            min_entry_price_volatility=scenario.get("min_entry_price_volatility", min_entry_price_volatility),
            buy_near_threshold_min_prob=scenario.get(
                "buy_near_threshold_min_prob",
                near_threshold_params["buy_near_threshold_min_prob"],
            ),
            buy_near_min_pred_return=scenario.get(
                "buy_near_min_pred_return",
                near_threshold_params["buy_near_min_pred_return"],
            ),
            buy_near_min_entry_volume_30s=scenario.get(
                "buy_near_min_entry_volume_30s",
                near_threshold_params["buy_near_min_entry_volume_30s"],
            ),
            buy_near_min_entry_price_volatility=scenario.get(
                "buy_near_min_entry_price_volatility",
                near_threshold_params["buy_near_min_entry_price_volatility"],
            ),
            buy_near_min_age_seconds=scenario.get(
                "buy_near_min_age_seconds",
                near_threshold_params["buy_near_min_age_seconds"],
            ),
            buy_primary_score_rescue_min_prob=scenario.get(
                "buy_primary_score_rescue_min_prob",
                primary_score_rescue_params["buy_primary_score_rescue_min_prob"],
            ),
            buy_primary_score_rescue_min_pred_return=scenario.get(
                "buy_primary_score_rescue_min_pred_return",
                primary_score_rescue_params["buy_primary_score_rescue_min_pred_return"],
            ),
            buy_primary_score_rescue_min_entry_volume_30s=scenario.get(
                "buy_primary_score_rescue_min_entry_volume_30s",
                primary_score_rescue_params["buy_primary_score_rescue_min_entry_volume_30s"],
            ),
            buy_primary_score_rescue_min_entry_price_volatility=scenario.get(
                "buy_primary_score_rescue_min_entry_price_volatility",
                primary_score_rescue_params["buy_primary_score_rescue_min_entry_price_volatility"],
            ),
            buy_primary_score_rescue_min_age_seconds=scenario.get(
                "buy_primary_score_rescue_min_age_seconds",
                primary_score_rescue_params["buy_primary_score_rescue_min_age_seconds"],
            ),
            buy_low_volume_rescue_min_prob=scenario.get(
                "buy_low_volume_rescue_min_prob",
                low_volume_rescue_params["buy_low_volume_rescue_min_prob"],
            ),
            buy_low_volume_rescue_min_entry_volume_30s=scenario.get(
                "buy_low_volume_rescue_min_entry_volume_30s",
                low_volume_rescue_params["buy_low_volume_rescue_min_entry_volume_30s"],
            ),
            buy_low_volume_rescue_max_entry_volume_30s=scenario.get(
                "buy_low_volume_rescue_max_entry_volume_30s",
                low_volume_rescue_params["buy_low_volume_rescue_max_entry_volume_30s"],
            ),
            buy_low_volume_rescue_min_entry_price_volatility=scenario.get(
                "buy_low_volume_rescue_min_entry_price_volatility",
                low_volume_rescue_params["buy_low_volume_rescue_min_entry_price_volatility"],
            ),
            buy_low_volume_rescue_max_age_seconds=scenario.get(
                "buy_low_volume_rescue_max_age_seconds",
                low_volume_rescue_params["buy_low_volume_rescue_max_age_seconds"],
            ),
            buy_low_volume_rescue_take_profit_pct=scenario.get(
                "buy_low_volume_rescue_take_profit_pct",
                low_volume_rescue_params["buy_low_volume_rescue_take_profit_pct"],
            ),
            buy_low_volume_rescue_min_action_score=scenario.get(
                "buy_low_volume_rescue_min_action_score",
                low_volume_rescue_params["buy_low_volume_rescue_min_action_score"],
            ),
            buy_quick_profit_overlay_min_prob=scenario.get(
                "buy_quick_profit_overlay_min_prob",
                quick_profit_overlay_params["buy_quick_profit_overlay_min_prob"],
            ),
            buy_quick_profit_overlay_min_pred_return=scenario.get(
                "buy_quick_profit_overlay_min_pred_return",
                quick_profit_overlay_params["buy_quick_profit_overlay_min_pred_return"],
            ),
            buy_quick_profit_overlay_max_pred_return=scenario.get(
                "buy_quick_profit_overlay_max_pred_return",
                quick_profit_overlay_params["buy_quick_profit_overlay_max_pred_return"],
            ),
            buy_quick_profit_overlay_min_entry_volume_30s=scenario.get(
                "buy_quick_profit_overlay_min_entry_volume_30s",
                quick_profit_overlay_params["buy_quick_profit_overlay_min_entry_volume_30s"],
            ),
            buy_quick_profit_overlay_min_entry_price_volatility=scenario.get(
                "buy_quick_profit_overlay_min_entry_price_volatility",
                quick_profit_overlay_params["buy_quick_profit_overlay_min_entry_price_volatility"],
            ),
            buy_quick_profit_overlay_min_interval_regularity=scenario.get(
                "buy_quick_profit_overlay_min_interval_regularity",
                quick_profit_overlay_params["buy_quick_profit_overlay_min_interval_regularity"],
            ),
            buy_quick_profit_overlay_max_age_seconds=scenario.get(
                "buy_quick_profit_overlay_max_age_seconds",
                quick_profit_overlay_params["buy_quick_profit_overlay_max_age_seconds"],
            ),
            buy_quick_profit_overlay_take_profit_pct=scenario.get(
                "buy_quick_profit_overlay_take_profit_pct",
                quick_profit_overlay_params["buy_quick_profit_overlay_take_profit_pct"],
            ),
            buy_quick_profit_overlay_max_hold_seconds=scenario.get(
                "buy_quick_profit_overlay_max_hold_seconds",
                quick_profit_overlay_params["buy_quick_profit_overlay_max_hold_seconds"],
            ),
            buy_quick_profit_overlay_min_total_buys=scenario.get(
                "buy_quick_profit_overlay_min_total_buys",
                quick_profit_overlay_params["buy_quick_profit_overlay_min_total_buys"],
            ),
            buy_quick_profit_overlay_min_flow_event_count_30s=scenario.get(
                "buy_quick_profit_overlay_min_flow_event_count_30s",
                quick_profit_overlay_params["buy_quick_profit_overlay_min_flow_event_count_30s"],
            ),
            buy_quick_profit_overlay_max_buy_sell_overlap_ratio_60s=scenario.get(
                "buy_quick_profit_overlay_max_buy_sell_overlap_ratio_60s",
                quick_profit_overlay_params["buy_quick_profit_overlay_max_buy_sell_overlap_ratio_60s"],
            ),
            buy_quick_profit_overlay_max_recent_seller_reentry_ratio_30s=scenario.get(
                "buy_quick_profit_overlay_max_recent_seller_reentry_ratio_30s",
                quick_profit_overlay_params["buy_quick_profit_overlay_max_recent_seller_reentry_ratio_30s"],
            ),
            buy_quick_profit_overlay_confirmation_delay_seconds=scenario.get(
                "buy_quick_profit_overlay_confirmation_delay_seconds",
                quick_profit_overlay_params["buy_quick_profit_overlay_confirmation_delay_seconds"],
            ),
            buy_quick_profit_overlay_max_confirmation_drawdown_pct=scenario.get(
                "buy_quick_profit_overlay_max_confirmation_drawdown_pct",
                quick_profit_overlay_params["buy_quick_profit_overlay_max_confirmation_drawdown_pct"],
            ),
            buy_quick_profit_overlay_max_confirmation_chase_pct=scenario.get(
                "buy_quick_profit_overlay_max_confirmation_chase_pct",
                quick_profit_overlay_params["buy_quick_profit_overlay_max_confirmation_chase_pct"],
            ),
            buy_shadow_meta_gate_min_prob=scenario.get(
                "buy_shadow_meta_gate_min_prob",
                shadow_meta_gate_params["buy_shadow_meta_gate_min_prob"],
            ),
            buy_shadow_meta_gate_max_entry_score=scenario.get(
                "buy_shadow_meta_gate_max_entry_score",
                shadow_meta_gate_params["buy_shadow_meta_gate_max_entry_score"],
            ),
            buy_shadow_meta_gate_min_entry_volume_30s=scenario.get(
                "buy_shadow_meta_gate_min_entry_volume_30s",
                shadow_meta_gate_params["buy_shadow_meta_gate_min_entry_volume_30s"],
            ),
            buy_shadow_meta_gate_min_entry_price_volatility=scenario.get(
                "buy_shadow_meta_gate_min_entry_price_volatility",
                shadow_meta_gate_params["buy_shadow_meta_gate_min_entry_price_volatility"],
            ),
            buy_shadow_meta_gate_max_age_seconds=scenario.get(
                "buy_shadow_meta_gate_max_age_seconds",
                shadow_meta_gate_params["buy_shadow_meta_gate_max_age_seconds"],
            ),
            buy_shadow_meta_gate_min_score=scenario.get(
                "buy_shadow_meta_gate_min_score",
                shadow_meta_gate_params["buy_shadow_meta_gate_min_score"],
            ),
            buy_path_state_meta_gate_min_score=scenario.get(
                "buy_path_state_meta_gate_min_score",
                path_state_meta_gate_params["buy_path_state_meta_gate_min_score"],
            ),
            buy_action_policy_router_min_confidence=scenario.get(
                "buy_action_policy_router_min_confidence",
                action_policy_router_params["buy_action_policy_router_min_confidence"],
            ),
            buy_action_policy_router_min_prob=scenario.get(
                "buy_action_policy_router_min_prob",
                action_policy_router_params["buy_action_policy_router_min_prob"],
            ),
            buy_action_policy_router_max_pred_return=scenario.get(
                "buy_action_policy_router_max_pred_return",
                action_policy_router_params["buy_action_policy_router_max_pred_return"],
            ),
            buy_action_policy_router_skip_passthrough=scenario.get(
                "buy_action_policy_router_skip_passthrough",
                action_policy_router_params["buy_action_policy_router_skip_passthrough"],
            ),
            buy_action_policy_continue_hold_activation_pct=scenario.get(
                "buy_action_policy_continue_hold_activation_pct",
                action_policy_continue_hold_params["buy_action_policy_continue_hold_activation_pct"],
            ),
            buy_action_policy_continue_hold_take_profit_pct=scenario.get(
                "buy_action_policy_continue_hold_take_profit_pct",
                action_policy_continue_hold_params["buy_action_policy_continue_hold_take_profit_pct"],
            ),
            buy_action_policy_continue_hold_release_pct=scenario.get(
                "buy_action_policy_continue_hold_release_pct",
                action_policy_continue_hold_params["buy_action_policy_continue_hold_release_pct"],
            ),
            buy_flow_activation_min_prob=scenario.get(
                "buy_flow_activation_min_prob",
                flow_activation_params["buy_flow_activation_min_prob"],
            ),
            buy_flow_activation_min_pred_return=scenario.get(
                "buy_flow_activation_min_pred_return",
                flow_activation_params["buy_flow_activation_min_pred_return"],
            ),
            buy_flow_activation_max_age_seconds=scenario.get(
                "buy_flow_activation_max_age_seconds",
                flow_activation_params["buy_flow_activation_max_age_seconds"],
            ),
            buy_flow_activation_lookback_seconds=scenario.get(
                "buy_flow_activation_lookback_seconds",
                flow_activation_params["buy_flow_activation_lookback_seconds"],
            ),
            buy_flow_activation_min_volume_ramp_ratio=scenario.get(
                "buy_flow_activation_min_volume_ramp_ratio",
                flow_activation_params["buy_flow_activation_min_volume_ramp_ratio"],
            ),
            buy_flow_activation_min_volume_ramp_delta=scenario.get(
                "buy_flow_activation_min_volume_ramp_delta",
                flow_activation_params["buy_flow_activation_min_volume_ramp_delta"],
            ),
            buy_flow_activation_min_pred_return_delta=scenario.get(
                "buy_flow_activation_min_pred_return_delta",
                flow_activation_params["buy_flow_activation_min_pred_return_delta"],
            ),
            buy_flow_activation_min_price_volatility_delta=scenario.get(
                "buy_flow_activation_min_price_volatility_delta",
                flow_activation_params["buy_flow_activation_min_price_volatility_delta"],
            ),
            buy_flow_activation_min_current_volume_30s=scenario.get(
                "buy_flow_activation_min_current_volume_30s",
                flow_activation_params["buy_flow_activation_min_current_volume_30s"],
            ),
            buy_dead_flow_exit_min_hold_seconds=scenario.get(
                "buy_dead_flow_exit_min_hold_seconds",
                flow_activation_params["buy_dead_flow_exit_min_hold_seconds"],
            ),
            buy_dead_flow_exit_max_mfe_pct=scenario.get(
                "buy_dead_flow_exit_max_mfe_pct",
                flow_activation_params["buy_dead_flow_exit_max_mfe_pct"],
            ),
            **{
                key: scenario.get(key, value)
                for key, value in flow_abstention_params.items()
            },
            profit_lock_take_profit_pct=scenario.get(
                "profit_lock_take_profit_pct",
                profit_lock_params["profit_lock_take_profit_pct"],
            ),
            profit_lock_max_hold_seconds=scenario.get(
                "profit_lock_max_hold_seconds",
                profit_lock_params["profit_lock_max_hold_seconds"],
            ),
            buy_late_pump_veto_min_age_seconds=scenario.get(
                "buy_late_pump_veto_min_age_seconds",
                late_pump_veto_params["buy_late_pump_veto_min_age_seconds"],
            ),
            buy_late_pump_veto_extension_window_seconds=scenario.get(
                "buy_late_pump_veto_extension_window_seconds",
                late_pump_veto_params["buy_late_pump_veto_extension_window_seconds"],
            ),
            buy_late_pump_veto_min_price_extension_pct=scenario.get(
                "buy_late_pump_veto_min_price_extension_pct",
                late_pump_veto_params["buy_late_pump_veto_min_price_extension_pct"],
            ),
            buy_late_pump_veto_min_drawdown_from_peak_pct=scenario.get(
                "buy_late_pump_veto_min_drawdown_from_peak_pct",
                late_pump_veto_params["buy_late_pump_veto_min_drawdown_from_peak_pct"],
            ),
            buy_late_pump_veto_min_entry_volume_30s=scenario.get(
                "buy_late_pump_veto_min_entry_volume_30s",
                late_pump_veto_params["buy_late_pump_veto_min_entry_volume_30s"],
            ),
            buy_late_pump_veto_min_entry_price_volatility=scenario.get(
                "buy_late_pump_veto_min_entry_price_volatility",
                late_pump_veto_params["buy_late_pump_veto_min_entry_price_volatility"],
            ),
            **{
                key: scenario.get(key, value)
                for key, value in entry_slippage_risk_veto_params.items()
            },
            **{
                key: scenario.get(key, value)
                for key, value in dead_bounce_veto_params.items()
            },
        )
        stress_replays.append({"name": scenario["name"], **scenario_replay})
    if stress_replays:
        result["stress_replay"] = stress_replays

    walk_forward_segments = []
    for segment_index, segment_episodes in _split_episodes_for_walk_forward(
        episodes,
        int(config.get("walk_forward_segments", 0)),
    ):
        segment_probabilities = [
            buy_probabilities_by_episode_id.get(id(episode), {})
            for episode in segment_episodes
        ]
        segment_scores = [
            entry_scores_by_episode_id.get(id(episode), {})
            for episode in segment_episodes
        ]
        segment_shadow_scores = [
            shadow_scores_by_episode_id.get(id(episode), {})
            for episode in segment_episodes
        ]
        segment_low_volume_rescue_scores = [
            low_volume_rescue_scores_by_episode_id.get(id(episode), {})
            for episode in segment_episodes
        ]
        segment_path_state_scores = [
            path_state_scores_by_episode_id.get(id(episode), {})
            for episode in segment_episodes
        ]
        segment_action_policy_routes = [
            action_policy_routes_by_episode_id.get(id(episode), {})
            for episode in segment_episodes
        ]
        segment_replay = _run_eval_replay(
            segment_episodes,
            buy_model,
            threshold,
            sell_policy,
            feature_names=feature_names,
            ignored_feature_names=ignored_feature_names,
            stop_loss=float(config.get("stop_loss", -0.50)),
            position_fraction=position_fraction,
            include_trade_log=False,
            trailing_start_pct=config.get("trailing_start_pct"),
            trailing_stop_pct=config.get("trailing_stop_pct"),
            rug_sell_pressure=config.get("rug_sell_pressure"),
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            one_entry_per_token=one_entry_per_token,
            max_trades_per_token=max_trades_per_token,
            max_entry_age_seconds=max_entry_age_seconds,
            max_hold_seconds=max_hold_seconds,
            min_policy_hold_seconds=min_policy_hold_seconds,
            max_position_fraction=max_position_fraction,
            allow_partial_exits=allow_partial_exits,
            buy_probabilities_by_episode=segment_probabilities,
            entry_scores_by_episode=segment_scores,
            shadow_scores_by_episode=segment_shadow_scores,
            low_volume_rescue_scores_by_episode=segment_low_volume_rescue_scores,
            path_state_scores_by_episode=segment_path_state_scores,
            action_policy_routes_by_episode=segment_action_policy_routes,
            entry_delay_seconds=entry_delay_seconds,
            exit_delay_seconds=exit_delay_seconds,
            max_open_positions=max_open_positions,
            initial_equity_bnb=initial_equity_bnb,
            fixed_stake_bnb=fixed_stake_bnb,
            entry_fixed_cost_bnb=entry_fixed_cost_bnb,
            exit_fixed_cost_bnb=exit_fixed_cost_bnb,
            entry_max_fill_wait_seconds=entry_max_fill_wait_seconds,
            exit_max_fill_wait_seconds=exit_max_fill_wait_seconds,
            entry_price_protection_pct=entry_price_protection_pct,
            entry_execution_failure_rate=entry_execution_failure_rate,
            exit_execution_failure_rate=exit_execution_failure_rate,
            max_pending_entries=max_pending_entries,
            entry_ranking_mode=entry_ranking_mode,
            min_entry_score=min_entry_score,
            min_entry_volume_30s=min_entry_volume_30s,
            min_entry_price_volatility=min_entry_price_volatility,
            **near_threshold_params,
            **primary_score_rescue_params,
            **low_volume_rescue_params,
            **quick_profit_overlay_params,
            **shadow_meta_gate_params,
            **path_state_meta_gate_params,
            **action_policy_router_params,
            **action_policy_continue_hold_params,
            **flow_activation_params,
            **flow_abstention_params,
            **profit_lock_params,
            **late_pump_veto_params,
            **entry_slippage_risk_veto_params,
            **dead_bounce_veto_params,
        )
        walk_forward_segments.append(
            {
                "segment_index": int(segment_index),
                "episode_count": int(len(segment_episodes)),
                **segment_replay,
            }
        )
    if walk_forward_segments:
        result["walk_forward"] = walk_forward_segments
        result["walk_forward_segment_count"] = int(len(walk_forward_segments))
        result["walk_forward_worst_net_return_pct"] = float(
            min(segment.get("net_return_pct", 0.0) for segment in walk_forward_segments)
        )
        result["walk_forward_worst_max_drawdown_pct"] = float(
            min(segment.get("max_drawdown_pct", 0.0) for segment in walk_forward_segments)
        )
        result["walk_forward_min_win_rate"] = float(
            min(segment.get("win_rate", 0.0) for segment in walk_forward_segments)
        )
        result["walk_forward_drawdown_within_preferred_limit"] = bool(
            result["walk_forward_worst_max_drawdown_pct"] >= preferred_max_drawdown_pct
        )
        rolling_min_win_rate = float(
            config.get("rolling_validation_min_win_rate", config.get("risk_tune_min_win_rate", 0.0)) or 0.0
        )
        rolling_min_net_return_pct = float(config.get("rolling_validation_min_net_return_pct", 0.0) or 0.0)
        rolling_max_drawdown_pct = float(
            config.get("rolling_validation_max_drawdown_pct", preferred_max_drawdown_pct)
        )
        rolling_segments = []
        for segment in walk_forward_segments:
            segment_passed = bool(
                float(segment.get("net_return_pct", 0.0)) >= rolling_min_net_return_pct
                and float(segment.get("max_drawdown_pct", 0.0)) >= rolling_max_drawdown_pct
                and float(segment.get("win_rate", 0.0)) >= rolling_min_win_rate
            )
            rolling_segments.append({
                "segment_index": int(segment.get("segment_index", 0)),
                "episode_count": int(segment.get("episode_count", 0)),
                "total_trades": int(segment.get("total_trades", 0)),
                "net_return_pct": float(segment.get("net_return_pct", 0.0)),
                "max_drawdown_pct": float(segment.get("max_drawdown_pct", 0.0)),
                "win_rate": float(segment.get("win_rate", 0.0)),
                "passed": segment_passed,
            })
        result["rolling_validation"] = {
            "segment_count": int(len(rolling_segments)),
            "min_net_return_threshold_pct": rolling_min_net_return_pct,
            "max_drawdown_threshold_pct": rolling_max_drawdown_pct,
            "min_win_rate_threshold": rolling_min_win_rate,
            "worst_net_return_pct": result["walk_forward_worst_net_return_pct"],
            "worst_max_drawdown_pct": result["walk_forward_worst_max_drawdown_pct"],
            "min_win_rate": result["walk_forward_min_win_rate"],
            "passed": bool(all(segment["passed"] for segment in rolling_segments)),
            "segments": rolling_segments,
        }
    return result


def _trade_profit_concentration(trade_log):
    profits = [
        float(row.get("stake_bnb", 0.0) or 0.0) * float(row.get("return_pct", 0.0) or 0.0) / 100.0
        for row in trade_log or []
    ]
    positive = sorted((value for value in profits if value > 0.0), reverse=True)
    total_positive = sum(positive)
    if total_positive <= 0.0:
        return {
            "positive_profit_bnb": 0.0,
            "top_1_profit_share": 0.0,
            "top_5_profit_share": 0.0,
            "top_10_profit_share": 0.0,
        }
    return {
        "positive_profit_bnb": float(total_positive),
        "top_1_profit_share": float(sum(positive[:1]) / total_positive),
        "top_5_profit_share": float(sum(positive[:5]) / total_positive),
        "top_10_profit_share": float(sum(positive[:10]) / total_positive),
    }


def _summarize_trade_log_by_exit_reason(trade_log):
    buckets = {}
    for row in trade_log or []:
        reason = str(row.get("exit_reason", "UNKNOWN") or "UNKNOWN")
        buckets.setdefault(reason, []).append(row)

    summary = {}
    for reason, rows in sorted(buckets.items()):
        returns = np.asarray([float(row.get("return_pct", 0.0) or 0.0) for row in rows], dtype=float)
        holds = []
        for row in rows:
            if "entry_time" not in row or "exit_time" not in row:
                continue
            try:
                holds.append(float(row.get("exit_time", 0) or 0) - float(row.get("entry_time", 0) or 0))
            except Exception:
                continue
        hold_arr = np.asarray(holds, dtype=float)
        summary[reason] = {
            "count": int(len(rows)),
            "mean_return_pct": float(np.mean(returns)) if returns.size else 0.0,
            "median_return_pct": float(np.median(returns)) if returns.size else 0.0,
            "min_return_pct": float(np.min(returns)) if returns.size else 0.0,
            "max_return_pct": float(np.max(returns)) if returns.size else 0.0,
            "mean_hold_seconds": float(np.mean(hold_arr)) if hold_arr.size else 0.0,
            "median_hold_seconds": float(np.median(hold_arr)) if hold_arr.size else 0.0,
        }

    return summary


LIVE_RUNTIME_PARAM_KEYS = (
    "buy_threshold",
    "stop_loss",
    "position_fraction",
    "max_position_fraction",
    "initial_equity_bnb",
    "fixed_stake_bnb",
    "fee_bps",
    "slippage_bps",
    "max_entry_age_seconds",
    "min_entry_unique_buyers",
    "min_entry_buy_count",
    "min_entry_volume_30s",
    "min_entry_price_volatility",
    "max_hold_seconds",
    "min_policy_hold_seconds",
    "allow_partial_exits",
    "entry_delay_seconds",
    "exit_delay_seconds",
    "max_open_positions",
    "entry_ranking_mode",
    "min_entry_score",
    "buy_near_threshold_min_prob",
    "buy_near_min_pred_return",
    "buy_near_min_entry_volume_30s",
    "buy_near_min_entry_price_volatility",
    "buy_near_min_age_seconds",
    "buy_primary_score_rescue_min_prob",
    "buy_primary_score_rescue_min_pred_return",
    "buy_primary_score_rescue_min_entry_volume_30s",
    "buy_primary_score_rescue_min_entry_price_volatility",
    "buy_primary_score_rescue_min_age_seconds",
    "use_pred_return_filter",
    "entry_max_fill_wait_seconds",
    "exit_max_fill_wait_seconds",
    "entry_price_protection_pct",
    "entry_execution_failure_rate",
    "exit_execution_failure_rate",
    "entry_fixed_cost_bnb",
    "exit_fixed_cost_bnb",
    "max_pending_entries",
    "trailing_start_pct",
    "trailing_stop_pct",
    "rug_sell_pressure",
)


def _selected_runtime_params_from_evaluation(evaluation):
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    runtime_replay = evaluation.get("runtime_replay", {})
    runtime_replay = runtime_replay if isinstance(runtime_replay, dict) else {}
    selected = {}
    for key in LIVE_RUNTIME_PARAM_KEYS:
        if key in evaluation:
            selected[key] = evaluation.get(key)
        elif key in runtime_replay:
            selected[key] = runtime_replay.get(key)
    return selected


def _externalize_trade_log(evaluation, output_dir):
    trade_log = evaluation.pop("trade_log", None)
    if not trade_log:
        return evaluation

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trade_log_path = output_dir / "trade_log.jsonl"
    with trade_log_path.open("w", encoding="utf-8") as handle:
        for row in trade_log:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    worst_trades = sorted(
        trade_log,
        key=lambda row: float(row.get("return_pct", 0.0)),
    )[:20]
    evaluation["trade_log_path"] = str(trade_log_path)
    evaluation["trade_log_count"] = int(len(trade_log))
    evaluation["worst_trades"] = worst_trades
    evaluation["exit_reason_summary"] = _summarize_trade_log_by_exit_reason(trade_log)
    return evaluation


def _train_hybrid_artifacts(config):
    buy_artifact = train_buy_model(config)
    env_bundle = build_sell_env(config, buy_artifact)
    bc_artifact = run_bc_warmstart(config, env_bundle)
    ppo_artifact = run_ppo_finetune(config, env_bundle, bc_artifact)

    entry_value_artifact = None
    if bool(config.get("train_entry_value_model", False)):
        entry_value_artifact = train_entry_value_model(config, buy_artifact)
        buy_artifact["entry_value_model"] = entry_value_artifact

    return buy_artifact, bc_artifact, ppo_artifact, entry_value_artifact


def run_hybrid_training(config):
    if "lifecycle_paths" in config:
        lifecycle_files = _stable_lifecycle_order(config.get("lifecycle_paths") or [])
    else:
        lifecycle_files = _discover_lifecycle_files(config.get("lifecycle_dir", "data/training"))

    validation_split_ratio = float(config.get("validation_split_ratio", 0.0) or 0.0)
    validation_files = []
    validation_raw_tokens = set()
    validation_config = None
    validation_evaluation = None
    validation_samples = []

    if validation_split_ratio > 0.0:
        split_result = _split_lifecycle_files_three_way(
            lifecycle_files,
            train_split_ratio=config.get("train_split_ratio", 0.8),
            validation_split_ratio=validation_split_ratio,
            min_validation_files=config.get("min_validation_files", 1),
            min_eval_files=config.get("min_eval_files", 1),
            enforce_no_overlap=False,
        )
        train_files = split_result["train_files"]
        validation_files = split_result["validation_files"]
        eval_files = split_result["eval_files"]
        train_raw_tokens = split_result["train_raw_tokens"]
        validation_raw_tokens = split_result["validation_raw_tokens"]
        raw_overlap_token_count = split_result["raw_final_overlap_token_count"]
        three_way_split = {
            "enabled": True,
            "train_split_ratio": float(config.get("train_split_ratio", 0.8)),
            "validation_split_ratio": validation_split_ratio,
            "min_validation_files": int(config.get("min_validation_files", 1)),
            "min_eval_files": int(config.get("min_eval_files", 1)),
            "train_file_count": int(len(train_files)),
            "validation_file_count": int(len(validation_files)),
            "eval_file_count": int(len(eval_files)),
            "raw_train_validation_overlap_count": int(split_result["raw_train_validation_overlap_count"]),
            "raw_train_eval_overlap_count": int(split_result["raw_train_eval_overlap_count"]),
            "raw_validation_eval_overlap_count": int(split_result["raw_validation_eval_overlap_count"]),
            "raw_final_overlap_token_count": int(split_result["raw_final_overlap_token_count"]),
        }
    else:
        split_result = _split_lifecycle_files(
            lifecycle_files,
            train_split_ratio=config.get("train_split_ratio", 0.8),
            min_eval_files=config.get("min_eval_files", 1),
            enforce_no_overlap=False,
            return_token_sets=True,
        )
        if len(split_result) == 5:
            train_files, eval_files, raw_overlap_token_count, train_raw_tokens, _eval_raw_tokens = split_result
        else:
            train_files, eval_files, raw_overlap_token_count = split_result
            train_raw_tokens = None
        three_way_split = {
            "enabled": False,
            "train_split_ratio": float(config.get("train_split_ratio", 0.8)),
            "validation_split_ratio": 0.0,
            "train_file_count": int(len(train_files)),
            "validation_file_count": 0,
            "eval_file_count": int(len(eval_files)),
            "raw_final_overlap_token_count": int(raw_overlap_token_count),
        }

    train_config = dict(config)
    train_config["lifecycle_paths"] = train_files
    train_config["train_file_count"] = int(len(train_files))
    train_config["validation_file_count"] = int(len(validation_files))
    train_config["eval_file_count"] = int(len(eval_files))
    train_config["overlap_token_count"] = int(raw_overlap_token_count)
    train_config["raw_overlap_token_count"] = int(raw_overlap_token_count)
    train_config["three_way_split_enabled"] = bool(validation_split_ratio > 0.0)

    if validation_split_ratio > 0.0:
        validation_config = dict(config)
        validation_config["lifecycle_paths"] = validation_files
        validation_config["evaluation_split"] = "validation"
        validation_config["include_trade_log"] = False
        validation_config["train_file_count"] = int(len(train_files))
        validation_config["validation_file_count"] = int(len(validation_files))
        validation_config["eval_file_count"] = int(len(validation_files))
        validation_config["overlap_token_count"] = int(three_way_split["raw_train_validation_overlap_count"])
        validation_config["raw_overlap_token_count"] = int(three_way_split["raw_train_validation_overlap_count"])
        validation_config["excluded_validation_token_count"] = int(three_way_split["raw_train_validation_overlap_count"])

    eval_config = dict(config)
    eval_config["lifecycle_paths"] = eval_files
    eval_config["evaluation_split"] = "final_test" if validation_split_ratio > 0.0 else "eval"
    eval_config["train_file_count"] = int(len(train_files))
    eval_config["validation_file_count"] = int(len(validation_files))
    eval_config["eval_file_count"] = int(len(eval_files))
    eval_config["overlap_token_count"] = int(raw_overlap_token_count)
    eval_config["raw_overlap_token_count"] = int(raw_overlap_token_count)
    eval_config["excluded_eval_token_count"] = 0

    buy_artifact, bc_artifact, ppo_artifact, entry_value_artifact = _train_hybrid_artifacts(train_config)

    if validation_config is not None:
        if "validation_samples" in config:
            validation_samples = list(config.get("validation_samples") or [])
        else:
            validation_load_config = dict(validation_config)
            if train_raw_tokens:
                validation_load_config["exclude_token_addresses"] = train_raw_tokens
            validation_samples = _load_samples(validation_load_config)
        validation_config["eval_samples"] = validation_samples
        validation_overlap_token_count = _sample_overlap_token_count(
            buy_artifact.get("samples", []),
            validation_samples,
        )
        validation_config["overlap_token_count"] = int(validation_overlap_token_count)
        if validation_overlap_token_count > 0:
            raise ValueError(
                f"train/validation sample leakage detected: overlap_token_count={validation_overlap_token_count}; "
                "adjust lifecycle partitions or explicit validation samples before training"
            )
        buy_artifact["calibration_samples"] = validation_samples
        risk_tuning = _tune_buy_threshold_by_replay(validation_config, buy_artifact, ppo_artifact)
    else:
        risk_tuning = _tune_buy_threshold_by_replay(train_config, buy_artifact, ppo_artifact)
    if risk_tuning is not None:
        tuned_threshold = float(risk_tuning.get("threshold", buy_artifact.get("threshold", 1.0)))
        buy_artifact["threshold"] = tuned_threshold
        buy_artifact["risk_tuning"] = risk_tuning
        threshold_path = buy_artifact.get("threshold_path")
        if threshold_path:
            Path(threshold_path).write_text(
                json.dumps({"threshold": tuned_threshold}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    if validation_config is not None:
        validation_evaluation = run_ab_evaluation(validation_config, buy_artifact, ppo_artifact, bc_artifact)
    if "eval_samples" not in eval_config:
        eval_load_config = dict(eval_config)
        if validation_config is not None:
            excluded_tokens = set(train_raw_tokens or set()).union(validation_raw_tokens or set())
            if excluded_tokens:
                eval_load_config["exclude_token_addresses"] = excluded_tokens
            eval_config["excluded_eval_token_count"] = int(raw_overlap_token_count)
        elif raw_overlap_token_count > 0:
            if train_raw_tokens is None:
                train_raw_tokens = _collect_raw_token_addresses(train_files)
            eval_load_config["exclude_token_addresses"] = train_raw_tokens
            eval_config["excluded_eval_token_count"] = int(raw_overlap_token_count)
        eval_config["eval_samples"] = _load_samples(eval_load_config)
    elif validation_config is not None:
        eval_config["excluded_eval_token_count"] = int(raw_overlap_token_count)
    previous_samples = list(buy_artifact.get("samples", []))
    if validation_config is not None:
        previous_samples.extend(validation_samples)
    sample_overlap_token_count = _sample_overlap_token_count(
        previous_samples,
        eval_config.get("eval_samples", []),
    )
    eval_config["overlap_token_count"] = int(sample_overlap_token_count)
    if sample_overlap_token_count > 0:
        raise ValueError(
            f"train/eval sample leakage detected: overlap_token_count={sample_overlap_token_count}; "
            "adjust lifecycle partitions or explicit eval samples before training"
        )
    output_dir = Path(config.get("output_dir", "data/models"))
    output_dir.mkdir(parents=True, exist_ok=True)
    evaluation = run_ab_evaluation(eval_config, buy_artifact, ppo_artifact, bc_artifact)
    evaluation = _externalize_trade_log(evaluation, output_dir)
    selected_runtime_params = evaluation.get("selected_runtime_params")
    if not isinstance(selected_runtime_params, dict):
        selected_runtime_params = _selected_runtime_params_from_evaluation(evaluation)
    selected_buy_threshold = buy_artifact.get("threshold")
    selected_threshold_source = buy_artifact.get("threshold_source")
    selected_risk_tuning = buy_artifact.get("risk_tuning")

    production_fit = {
        "enabled": bool(config.get("fit_artifacts_on_all_data", False)),
        "artifact_scope": "holdout_train_split",
        "lifecycle_file_count": int(len(train_files)),
        "selection_evaluation_scope": "same_artifacts",
    }
    report_artifact_config = train_config
    report_lifecycle_files = train_files
    if bool(config.get("fit_artifacts_on_all_data", False)):
        all_data_config = dict(config)
        all_data_config["lifecycle_paths"] = lifecycle_files
        all_data_config["train_file_count"] = int(len(lifecycle_files))
        all_data_config["validation_file_count"] = 0
        all_data_config["eval_file_count"] = 0
        all_data_config["overlap_token_count"] = 0
        all_data_config["raw_overlap_token_count"] = 0
        all_data_config["three_way_split_enabled"] = False
        all_data_config["production_fit_all_data"] = True
        all_data_calibration_threshold = None
        buy_artifact, bc_artifact, ppo_artifact, entry_value_artifact = _train_hybrid_artifacts(all_data_config)
        all_data_calibration_threshold = buy_artifact.get("threshold")
        if selected_buy_threshold is not None:
            buy_artifact["threshold"] = float(selected_buy_threshold)
            if selected_threshold_source is not None:
                buy_artifact["threshold_source"] = selected_threshold_source
        if selected_risk_tuning is not None:
            buy_artifact["risk_tuning"] = selected_risk_tuning
            threshold_path = buy_artifact.get("threshold_path")
            if threshold_path:
                Path(threshold_path).write_text(
                    json.dumps({"threshold": float(selected_buy_threshold)}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        production_fit = {
            "enabled": True,
            "artifact_scope": "all_lifecycle_files",
            "lifecycle_file_count": int(len(lifecycle_files)),
            "selection_evaluation_scope": "holdout_split",
            "selection_train_file_count": int(len(train_files)),
            "selection_validation_file_count": int(len(validation_files)),
            "selection_eval_file_count": int(len(eval_files)),
            "selected_threshold": None if selected_buy_threshold is None else float(selected_buy_threshold),
            "all_data_calibration_threshold": all_data_calibration_threshold,
        }
        report_artifact_config = all_data_config
        report_lifecycle_files = lifecycle_files

    try:
        sample_cache_path = _sample_cache_path(report_artifact_config, report_lifecycle_files)
    except FileNotFoundError:
        sample_cache_path = None
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command_args": list(config.get("command_args") or []),
        "git": git_metadata(),
        "model_checksums": _artifact_checksums(output_dir),
        "include_flow_features": bool(report_artifact_config.get("include_flow_features", False)),
        "sample_count": int(len(buy_artifact.get("samples") or [])),
        "sample_cache_path": None if sample_cache_path is None else str(sample_cache_path),
        "lifecycle_paths": [str(path) for path in lifecycle_files],
        "artifacts": {
            "buy_model": {
                "model_path": buy_artifact.get("model_path"),
                "threshold": buy_artifact.get("threshold"),
                "threshold_path": buy_artifact.get("threshold_path"),
                "feature_schema_path": buy_artifact.get("feature_schema_path"),
                "feature_names": buy_artifact.get("feature_names"),
                "dropped_features": buy_artifact.get("dropped_features"),
                "target_label_column": buy_artifact.get("target_label_column"),
                "target_threshold_value": buy_artifact.get("target_threshold_value"),
                "threshold_source": buy_artifact.get("threshold_source"),
                "sample_weighting": buy_artifact.get("sample_weighting"),
                "calibration": buy_artifact.get("calibration"),
                "risk_tuning": buy_artifact.get("risk_tuning"),
                "entry_value_model": None if entry_value_artifact is None else {
                    "model_path": entry_value_artifact.get("model_path"),
                    "feature_schema_path": entry_value_artifact.get("feature_schema_path"),
                    "feature_names": entry_value_artifact.get("feature_names"),
                    "dropped_features": entry_value_artifact.get("dropped_features"),
                    "target_label_column": entry_value_artifact.get("target_label_column"),
                    "sample_count": entry_value_artifact.get("sample_count"),
                },
            },
            "entry_value_model": None if entry_value_artifact is None else {
                "model_path": entry_value_artifact.get("model_path"),
                "feature_schema_path": entry_value_artifact.get("feature_schema_path"),
                "feature_names": entry_value_artifact.get("feature_names"),
                "dropped_features": entry_value_artifact.get("dropped_features"),
                "target_label_column": entry_value_artifact.get("target_label_column"),
                "sample_count": entry_value_artifact.get("sample_count"),
            },
            "sell_policy": {
                "policy_path": ppo_artifact.get("policy_path"),
                "total_timesteps": ppo_artifact.get("total_timesteps"),
            },
            "bc_warmstart": bc_artifact,
        },
        "three_way_split": three_way_split,
        "production_fit": production_fit,
        "selected_runtime_params": selected_runtime_params,
        "evaluation": evaluation,
    }
    if validation_evaluation is not None:
        result["validation_evaluation"] = validation_evaluation

    manifest_path = output_dir / "hybrid_manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

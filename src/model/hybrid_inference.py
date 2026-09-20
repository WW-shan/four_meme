from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def normalize_feature_names(feature_names, *, error_message: str = "feature_names must be a list when provided"):
    if feature_names is None:
        return None
    if isinstance(feature_names, list):
        return [str(name) for name in feature_names]
    raise ValueError(error_message)


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


def _ordered_feature_row(features_dict, feature_names, ignored_feature_names, *, non_mapping_message):
    if not isinstance(features_dict, Mapping):
        raise ValueError(non_mapping_message)

    provided_keys = set(features_dict.keys())
    expected_keys = set(feature_names)
    ignored_keys = set(normalize_ignored_feature_names(ignored_feature_names))

    missing = sorted(expected_keys - provided_keys)
    extra = sorted(provided_keys - expected_keys - ignored_keys)

    if missing:
        raise ValueError(f"Missing expected features: {', '.join(missing)}")
    if extra:
        raise ValueError(f"Unexpected extra features: {', '.join(extra)}")

    return {name: features_dict[name] for name in feature_names}


def build_feature_frame(features_dict, feature_names=None, ignored_feature_names=None):
    feature_names = normalize_feature_names(feature_names)
    if feature_names is None:
        return pd.DataFrame([features_dict])

    ordered_features = _ordered_feature_row(
        features_dict,
        feature_names,
        ignored_feature_names,
        non_mapping_message="features_dict must be a mapping when feature schema is enforced",
    )
    return pd.DataFrame([ordered_features], columns=feature_names)


def build_feature_frame_many(feature_rows, feature_names=None, ignored_feature_names=None):
    feature_names = normalize_feature_names(feature_names)
    rows = list(feature_rows or [])
    if feature_names is None:
        return pd.DataFrame(rows)

    ordered_rows = [
        _ordered_feature_row(
            row,
            feature_names,
            ignored_feature_names,
            non_mapping_message="feature rows must be mappings when feature schema is enforced",
        )
        for row in rows
    ]
    return pd.DataFrame(ordered_rows, columns=feature_names)


def load_feature_schema_from_file(schema_path):
    schema_path = Path(schema_path)
    try:
        payload = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"failed to read feature schema from {schema_path}: {exc}") from exc
    return {
        "feature_names": normalize_feature_names(
            payload.get("feature_names"),
            error_message="feature_schema.json field 'feature_names' must be a list when provided",
        ),
        "ignored_feature_names": normalize_ignored_feature_names(payload.get("dropped_features")),
    }


def load_feature_names_from_schema(schema_path):
    return load_feature_schema_from_file(schema_path)["feature_names"]


def coerce_action(action) -> int:
    try:
        return int(action)
    except Exception:
        import numpy as np
        return int(np.asarray(action).reshape(-1)[0])


class HybridModel:
    def __init__(
        self,
        buy_model,
        buy_threshold: float,
        sell_policy=None,
        entry_value_model=None,
        feature_names=None,
        ignored_feature_names=None,
    ):
        self.buy_model = buy_model
        self.buy_threshold = float(buy_threshold)
        self.sell_policy = sell_policy
        self.entry_value_model = entry_value_model
        self.feature_names = normalize_feature_names(feature_names)
        self.ignored_feature_names = normalize_ignored_feature_names(ignored_feature_names)

    def predict_buy(self, features_dict: dict) -> tuple:
        X = build_feature_frame(features_dict, self.feature_names, self.ignored_feature_names)
        proba = self.buy_model.predict_proba(X)
        if hasattr(proba, '__len__') and len(proba) > 0:
            row = proba[0]
            prob = float(row[1]) if len(row) > 1 else float(row[0])
        else:
            prob = float(proba)
        return prob, prob >= self.buy_threshold

    def predict_entry_value(self, features_dict: dict):
        if self.entry_value_model is None:
            return None
        X = build_feature_frame(features_dict, self.feature_names, self.ignored_feature_names)
        prediction = self.entry_value_model.predict(X)
        if hasattr(prediction, "__len__") and len(prediction) > 0:
            return float(prediction[0])
        return float(prediction)

    def predict_return(self, features_dict: dict):
        return self.predict_entry_value(features_dict)

    def predict_sell(self, obs) -> int:
        if self.sell_policy is None:
            return -1
        import numpy as np
        obs_arr = np.asarray(obs, dtype=np.float32).reshape(1, -1)
        action, _ = self.sell_policy.predict(obs_arr, deterministic=True)
        return coerce_action(action)

    @classmethod
    def load(cls, model_dir) -> "HybridModel":
        model_dir = Path(model_dir)

        buy_model = _load_catboost_model(str(model_dir / "buy_model.cbm"))

        threshold_path = model_dir / "buy_threshold.json"
        if threshold_path.exists():
            with open(threshold_path, "r", encoding="utf-8") as f:
                threshold = float(json.load(f).get("threshold", 0.5))
        else:
            threshold = 0.5

        feature_names = None
        ignored_feature_names = []
        schema_path = model_dir / "feature_schema.json"
        if schema_path.exists():
            schema = load_feature_schema_from_file(schema_path)
            feature_names = schema["feature_names"]
            ignored_feature_names = schema["ignored_feature_names"]

        sell_policy = None
        policy_path = model_dir / "sell_policy.zip"
        if policy_path.exists():
            try:
                sell_policy = _load_sb3_policy(str(policy_path))
            except Exception as exc:
                # Not fatal, but it silently changes how positions exit: the bot falls back to
                # rule-based sells instead of the learned policy. Say so explicitly.
                logger.warning(
                    "failed to load sell policy from %s: %s; falling back to rule-based exits",
                    policy_path,
                    exc,
                )
                sell_policy = None

        entry_value_model = None
        entry_value_path = model_dir / "entry_value_model.cbm"
        if entry_value_path.exists():
            entry_value_model = _load_catboost_regressor(str(entry_value_path))

        return cls(
            buy_model=buy_model,
            buy_threshold=threshold,
            sell_policy=sell_policy,
            entry_value_model=entry_value_model,
            feature_names=feature_names,
            ignored_feature_names=ignored_feature_names,
        )


def _load_catboost_model(path):
    from catboost import CatBoostClassifier
    model = CatBoostClassifier()
    model.load_model(path)
    return model


def _load_catboost_regressor(path):
    from catboost import CatBoostRegressor
    model = CatBoostRegressor()
    model.load_model(path)
    return model


def _load_sb3_policy(path):
    from stable_baselines3 import PPO
    return PPO.load(path)

"""Model inputs and target.

The raw CSV uses names with spaces (``fixed acidity``). Everything downstream — the
model, the API, the monitoring reports — uses the snake_case names in
:data:`FEATURE_COLUMNS`.
"""

from __future__ import annotations

import pandas as pd

from common.config import TargetParams
from data.columns import FEATURE_COLUMNS
from data.schema import DatasetSchema

__all__ = ["FEATURE_COLUMNS", "build_dataset", "build_features", "build_target", "snake_case"]


def snake_case(name: str) -> str:
    return "_".join(name.strip().lower().split())


def build_target(df: pd.DataFrame, target: TargetParams) -> pd.Series:
    """``1`` when the raw quality score is at least the threshold, else ``0``."""
    labels = (df[target.source_column] >= target.threshold).astype("int64")
    return labels.rename(target.name)


def build_features(df: pd.DataFrame, schema: DatasetSchema) -> pd.DataFrame:
    """Select the schema's feature columns (never the raw target) under snake_case names."""
    features = df[schema.feature_names].rename(columns=snake_case)
    missing = [c for c in FEATURE_COLUMNS if c not in features.columns]
    if missing:
        raise ValueError(f"schema does not provide the model features: {missing}")
    return features.loc[:, list(FEATURE_COLUMNS)].astype("float64")


def build_dataset(df: pd.DataFrame, schema: DatasetSchema, target: TargetParams) -> pd.DataFrame:
    """Feature columns plus the binary target column, ready to split."""
    return build_features(df, schema).assign(**{target.name: build_target(df, target)})

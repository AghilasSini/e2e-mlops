"""Model definition and fitting."""

from __future__ import annotations

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from common.config import ModelParams
from data.features import FEATURE_COLUMNS


def build_model(params: ModelParams, random_state: int) -> Pipeline:
    """Scaled logistic regression with an elastic-net penalty (``C = 1 / alpha``)."""
    if params.alpha <= 0:
        raise ValueError("alpha must be > 0")
    if not 0.0 <= params.l1_ratio <= 1.0:
        raise ValueError("l1_ratio must be within [0, 1]")
    classifier = LogisticRegression(
        penalty="elasticnet",
        solver="saga",
        C=1.0 / params.alpha,
        l1_ratio=params.l1_ratio,
        max_iter=params.max_iter,
        random_state=random_state,
    )
    return Pipeline([("scale", StandardScaler()), ("classifier", classifier)])


def fit_model(
    train: pd.DataFrame, target_name: str, params: ModelParams, random_state: int
) -> Pipeline:
    model = build_model(params, random_state)
    model.fit(train.loc[:, list(FEATURE_COLUMNS)], train[target_name])
    return model

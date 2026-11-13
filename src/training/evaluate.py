"""Held-out evaluation of a fitted classifier."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)

from data.features import FEATURE_COLUMNS

#: Metrics logged for every run, all computed on the held-out test set.
METRIC_NAMES = ("roc_auc", "f1", "accuracy", "precision", "recall", "log_loss", "rmse")


def probability_rmse(truth: Sequence[int], probabilities: Sequence[float]) -> float:
    """Root-mean-square error between P(good) and the 0/1 label (sqrt of the Brier score)."""
    y = np.asarray(truth, dtype=float)
    p = np.asarray(probabilities, dtype=float)
    return float(math.sqrt(np.mean((p - y) ** 2)))


def evaluate(model: Any, test: pd.DataFrame, target_name: str) -> dict[str, float | int]:
    """The seven :data:`METRIC_NAMES` on the test set, plus its size ``n_test``."""
    features = test.loc[:, list(FEATURE_COLUMNS)]
    truth = test[target_name].to_numpy()
    probabilities = model.predict_proba(features)[:, 1]
    predictions = (probabilities >= 0.5).astype(int)
    return {
        "roc_auc": float(roc_auc_score(truth, probabilities)),
        "f1": float(f1_score(truth, predictions, zero_division=0)),
        "accuracy": float(accuracy_score(truth, predictions)),
        "precision": float(precision_score(truth, predictions, zero_division=0)),
        "recall": float(recall_score(truth, predictions, zero_division=0)),
        "log_loss": float(log_loss(truth, probabilities, labels=[0, 1])),
        "rmse": probability_rmse(truth, probabilities),
        "n_test": int(len(test)),
    }


def confusion(model: Any, test: pd.DataFrame, target_name: str) -> dict[str, Any]:
    """Confusion matrix at threshold 0.5, rows = truth, columns = prediction."""
    probabilities = model.predict_proba(test.loc[:, list(FEATURE_COLUMNS)])[:, 1]
    predictions = (probabilities >= 0.5).astype(int)
    matrix = confusion_matrix(test[target_name], predictions, labels=[0, 1])
    (tn, fp), (fn, tp) = matrix.tolist()
    return {
        "labels": ["not_good", "good"],
        "matrix": matrix.tolist(),
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "true_positive": tp,
    }


def write_json(content: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_metrics(metrics: dict[str, float | int], path: Path) -> None:
    write_json(metrics, path)

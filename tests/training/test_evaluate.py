from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from data.features import FEATURE_COLUMNS
from training.evaluate import METRIC_NAMES, confusion, evaluate, probability_rmse


class FixedModel:
    """Returns predetermined P(good) values, so metrics can be checked by hand."""

    def __init__(self, probabilities: list[float]) -> None:
        self.p = np.asarray(probabilities)

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        assert list(features.columns) == list(FEATURE_COLUMNS)
        return np.column_stack([1 - self.p, self.p])


def _frame(labels: list[int]) -> pd.DataFrame:
    data = {name: [1.0] * len(labels) for name in FEATURE_COLUMNS}
    return pd.DataFrame({**data, "is_good": labels})


def test_rmse_hand_computed() -> None:
    # errors 0.1, 0.2, 0.4, 0.3 -> squares sum 0.30 -> mean 0.075 -> sqrt 0.27386
    assert probability_rmse([0, 1, 1, 0], [0.1, 0.8, 0.6, 0.3]) == pytest.approx(math.sqrt(0.075))


def test_rmse_perfect_and_worst() -> None:
    assert probability_rmse([0, 1], [0.0, 1.0]) == 0.0
    assert probability_rmse([0, 1], [1.0, 0.0]) == 1.0


def test_all_seven_metrics_are_finite_and_hand_checked() -> None:
    labels = [0, 1, 1, 0, 1]
    model = FixedModel([0.1, 0.8, 0.4, 0.6, 0.9])  # predictions 0,1,0,1,1
    metrics = evaluate(model, _frame(labels), "is_good")

    assert set(metrics) == {*METRIC_NAMES, "n_test"}
    assert all(math.isfinite(metrics[name]) for name in METRIC_NAMES)
    assert metrics["n_test"] == 5
    assert metrics["accuracy"] == pytest.approx(3 / 5)
    assert metrics["precision"] == pytest.approx(2 / 3)  # tp=2, fp=1
    assert metrics["recall"] == pytest.approx(2 / 3)  # tp=2, fn=1
    assert metrics["f1"] == pytest.approx(2 / 3)
    assert metrics["roc_auc"] == pytest.approx(5 / 6)
    assert metrics["rmse"] == pytest.approx(probability_rmse(labels, [0.1, 0.8, 0.4, 0.6, 0.9]))
    assert metrics["log_loss"] > 0


def test_confusion_matrix_layout() -> None:
    result = confusion(FixedModel([0.1, 0.8, 0.4, 0.6, 0.9]), _frame([0, 1, 1, 0, 1]), "is_good")
    assert result["matrix"] == [[1, 1], [1, 2]]
    assert (result["true_negative"], result["false_positive"]) == (1, 1)
    assert (result["false_negative"], result["true_positive"]) == (1, 2)
    assert result["labels"] == ["not_good", "good"]


def test_no_positive_predictions_does_not_crash() -> None:
    metrics = evaluate(FixedModel([0.1, 0.2, 0.3]), _frame([0, 1, 0]), "is_good")
    assert metrics["precision"] == 0.0 and metrics["f1"] == 0.0
    assert all(math.isfinite(metrics[name]) for name in METRIC_NAMES)

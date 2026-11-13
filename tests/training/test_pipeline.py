from __future__ import annotations

import json
import runpy
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from common.config import ModelParams, load_settings
from data.features import FEATURE_COLUMNS, build_dataset
from data.schema import load_schema
from data.validation import load_raw
from training.pipeline import StepError, main, model_version, run
from training.train import build_model


def _settings(app_root: Path, **env: str):
    return load_settings(root=app_root, env=env)


# Scenario: Successful run (+ Metrics file content)
def test_successful_run_writes_outputs(app_root: Path) -> None:
    result = run(_settings(app_root), env={"MODEL_VERSION": "v-test"})

    assert result.model_path.is_file()
    assert result.version_path.read_text().strip() == "v-test"
    metrics = json.loads(result.metrics_path.read_text())
    for key in ("roc_auc", "f1", "accuracy"):
        assert isinstance(metrics[key], float)
        assert 0.0 <= metrics[key] <= 1.0
    assert metrics["n_test"] == 320
    assert metrics["roc_auc"] > 0.75


def test_saved_model_predicts_probabilities(app_root: Path) -> None:
    result = run(_settings(app_root), env={"MODEL_VERSION": "v"})
    model = joblib.load(result.model_path)
    sample = np.array([[7.4, 0.7, 0.0, 1.9, 0.076, 11.0, 34.0, 0.9978, 3.51, 0.56, 9.4]])
    proba = model.predict_proba(pd.DataFrame(sample, columns=list(FEATURE_COLUMNS)))
    assert proba.shape == (1, 2)
    assert np.isclose(proba.sum(), 1.0)


# Scenario: Invalid data aborts training (wine-data-processing) / Step failure
def test_invalid_data_writes_no_artifacts(app_root: Path) -> None:
    csv = app_root / "data" / "raw" / "winequality-red.csv"
    csv.write_text(csv.read_text().replace("chlorides", "clorides", 1))
    settings = _settings(app_root)

    with pytest.raises(StepError) as exc:
        run(settings, env={})
    assert exc.value.step == "validate"
    assert not settings.output_dir.exists()


def test_main_returns_nonzero_and_logs_step(app_root: Path, caplog) -> None:
    csv = app_root / "data" / "raw" / "winequality-red.csv"
    csv.write_text(csv.read_text().replace("alcohol", "ethanol", 1))
    code = main(env={"APP_ROOT": str(app_root)})
    assert code == 1
    assert "validate" in caplog.text
    assert not (app_root / "outputs").exists()


def test_main_returns_zero_on_success(app_root: Path) -> None:
    assert main(env={"APP_ROOT": str(app_root), "MODEL_VERSION": "ok"}) == 0
    assert (app_root / "outputs" / "model.joblib").is_file()


def test_main_reports_configuration_errors(tmp_path: Path) -> None:
    assert main(env={"APP_ROOT": str(tmp_path)}) == 1


def test_missing_dataset_fails_at_load_step(app_root: Path) -> None:
    with pytest.raises(StepError) as exc:
        run(_settings(app_root, DATA_PATH=str(app_root / "absent.csv")), env={})
    assert exc.value.step == "load-data"


# Scenario: Hyper-parameter change
def test_hyper_parameters_come_from_params_file(app_root: Path) -> None:
    params = app_root / "params.yaml"
    params.write_text(params.read_text().replace("alpha: 0.5", "alpha: 2.0"))
    result = run(_settings(app_root), env={"MODEL_VERSION": "v"})
    classifier = joblib.load(result.model_path).named_steps["classifier"]
    assert classifier.C == pytest.approx(0.5)
    assert classifier.l1_ratio == 0.7


# Scenario: Rerun determinism
def test_two_runs_give_identical_predictions(app_root: Path, tmp_path: Path) -> None:
    first = run(_settings(app_root, OUTPUT_DIR=str(tmp_path / "a")), env={"MODEL_VERSION": "a"})
    second = run(_settings(app_root, OUTPUT_DIR=str(tmp_path / "b")), env={"MODEL_VERSION": "b"})
    assert first.metrics == second.metrics
    settings = _settings(app_root)
    data = build_dataset(
        load_raw(settings.data_path), load_schema(settings.schema_path), settings.params.target
    )
    features = pd.DataFrame(data.loc[:, list(FEATURE_COLUMNS)])
    a = joblib.load(first.model_path).predict_proba(features)
    b = joblib.load(second.model_path).predict_proba(features)
    assert np.array_equal(a, b)


def test_model_version_format() -> None:
    now = datetime(2026, 11, 9, 15, 4, 5, tzinfo=timezone.utc)
    assert model_version(Path("."), {"GIT_SHA": "abc1234"}, now) == "20261109T150405Z-abc1234"
    assert model_version(Path("."), {"MODEL_VERSION": "pinned"}, now) == "pinned"


def test_model_version_without_git(tmp_path: Path) -> None:
    assert model_version(tmp_path, {}).endswith("-nogit")


@pytest.mark.parametrize(("alpha", "l1_ratio"), [(0.0, 0.5), (-1.0, 0.5), (1.0, 1.5), (1.0, -0.1)])
def test_invalid_hyper_parameters_are_rejected(alpha: float, l1_ratio: float) -> None:
    with pytest.raises(ValueError):
        build_model(ModelParams(alpha=alpha, l1_ratio=l1_ratio, max_iter=100), random_state=0)


def test_module_entrypoint_exits_with_status(app_root: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_ROOT", str(app_root))
    monkeypatch.setenv("MODEL_VERSION", "entry")
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("training", run_name="__main__")
    assert exc.value.code == 0

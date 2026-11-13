"""specs/experiment-tracking: one test per scenario, against a temporary SQLite store."""

from __future__ import annotations

import json
import math
from pathlib import Path

import mlflow
import pandas as pd
import pytest
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

from common.config import load_settings
from data.features import FEATURE_COLUMNS
from training.evaluate import METRIC_NAMES
from training.pipeline import main, run
from training.tracking import RunRecord, file_sha256

ARTIFACTS = {
    "metrics.json",
    "confusion_matrix.json",
    "schema.yaml",
    "data/reference.csv",
    "data/test.csv",
}


def _all_artifacts(client: MlflowClient, run_id: str, path: str | None = None) -> set[str]:
    found = set()
    for item in client.list_artifacts(run_id, path):
        found |= _all_artifacts(client, run_id, item.path) if item.is_dir else {item.path}
    return found


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A tracking 'server' (SQLite store) configured through MLFLOW_TRACKING_URI."""
    return f"sqlite:///{tmp_path_factory.mktemp('tracking') / 'mlflow.db'}"


@pytest.fixture(scope="module")
def tracked(server: str, tmp_path_factory: pytest.TempPathFactory, repo_root: Path):
    out = tmp_path_factory.mktemp("out")
    env = {
        "MLFLOW_TRACKING_URI": server,
        "OUTPUT_DIR": str(out),
        "GIT_SHA": "abc1234",
        "IMAGE_TAG": "abc1234",
        "MODEL_VERSION": "test-run",
    }
    result = run(load_settings(root=repo_root, env=env), env=env)
    return result, MlflowClient(tracking_uri=server, registry_uri=server)


# Scenario: Configured server
def test_run_recorded_on_configured_server(tracked, server: str) -> None:
    result, client = tracked
    experiment = client.get_experiment_by_name("wine-quality")
    assert experiment is not None
    run_info = client.get_run(result.run.run_id).info
    assert run_info.experiment_id == experiment.experiment_id
    assert run_info.status == "FINISHED"
    assert result.run.tracking_uri == server


# Scenario: No server configured
def test_local_store_without_server(app_root: Path, caplog) -> None:
    result = run(load_settings(root=app_root, env={}), env={"MODEL_VERSION": "local"})
    store = app_root / "outputs" / "mlflow.db"
    assert store.is_file()
    assert result.run.tracking_uri == f"sqlite:///{store}"
    assert str(store) in caplog.text and "MLFLOW_TRACKING_URI is not set" in caplog.text
    assert (app_root / "outputs" / "mlartifacts").is_dir()  # artifacts stay in outputs/


# Scenario: Server down
def test_unreachable_server_fails_before_fitting(app_root: Path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_TIMEOUT", "2")
    code = main(env={"APP_ROOT": str(app_root), "MLFLOW_TRACKING_URI": "http://127.0.0.1:9"})
    assert code == 1
    assert "tracking-preflight" in caplog.text
    assert "http://127.0.0.1:9" in caplog.text
    outputs = app_root / "outputs"
    assert not (outputs / "model.joblib").exists()  # failed before fit
    assert not (outputs / "run.json").exists()  # nothing registered


# Scenario: Lineage lookup
def test_lineage_params(tracked, repo_root: Path) -> None:
    result, client = tracked
    params = client.get_run(result.run.run_id).data.params
    assert params["data_sha256"] == file_sha256(repo_root / "data/raw/winequality-red.csv")
    assert params["random_state"] == "42"
    assert params["git_sha"] == "abc1234"
    assert params["image_tag"] == "abc1234"
    assert (params["n_train"], params["n_test"]) == ("1279", "320")
    for key in ("alpha", "l1_ratio", "target_threshold", "test_size"):
        assert key in params
    tags = client.get_model_version("wine-quality", result.run.model_version).tags
    assert tags["data_sha256"] == params["data_sha256"]
    assert tags["git_sha"] == "abc1234"


# Scenario: Metric set
def test_seven_finite_metrics(tracked) -> None:
    result, client = tracked
    metrics = client.get_run(result.run.run_id).data.metrics
    assert set(metrics) == set(METRIC_NAMES)
    assert all(math.isfinite(v) for v in metrics.values())
    assert metrics["roc_auc"] == pytest.approx(result.metrics["roc_auc"])


# Scenario: Reference data available
def test_artifacts_and_reference_data(tracked, tmp_path: Path) -> None:
    result, client = tracked
    assert _all_artifacts(client, result.run.run_id) == ARTIFACTS
    columns = [*FEATURE_COLUMNS, "is_good"]
    reference = pd.read_csv(
        client.download_artifacts(result.run.run_id, "data/reference.csv", str(tmp_path))
    )
    test = pd.read_csv(client.download_artifacts(result.run.run_id, "data/test.csv", str(tmp_path)))
    assert list(reference.columns) == columns and len(reference) == 1279
    assert list(test.columns) == columns and len(test) == 320
    matrix = json.loads(
        Path(
            client.download_artifacts(result.run.run_id, "confusion_matrix.json", str(tmp_path))
        ).read_text()
    )
    assert sum(map(sum, matrix["matrix"])) == 320


# Scenario: New version
def test_registered_version_without_champion(tracked) -> None:
    result, client = tracked
    version = client.get_model_version("wine-quality", result.run.model_version)
    assert version.run_id == result.run.run_id
    assert version.aliases == []
    with pytest.raises(MlflowException):
        client.get_model_version_by_alias("wine-quality", "champion")


def test_model_has_signature_and_input_example(tracked, server: str) -> None:
    result, _ = tracked
    mlflow.set_tracking_uri(server)  # MLflow URIs are process-global; other tests move them
    mlflow.set_registry_uri(server)
    info = mlflow.models.get_model_info(result.run.model_uri)
    assert info.signature is not None
    assert [c.name for c in info.signature.inputs] == list(FEATURE_COLUMNS)
    assert info.saved_input_example_info is not None
    model = mlflow.sklearn.load_model(result.run.model_uri)
    assert model.predict_proba(pd.DataFrame([[1.0] * 11], columns=list(FEATURE_COLUMNS))).shape == (
        1,
        2,
    )


def test_each_run_adds_one_version(tracked, server: str, repo_root: Path, tmp_path: Path) -> None:
    _, client = tracked
    before = len(client.search_model_versions("name='wine-quality'"))
    env = {"MLFLOW_TRACKING_URI": server, "OUTPUT_DIR": str(tmp_path), "MODEL_VERSION": "again"}
    second = run(load_settings(root=repo_root, env=env), env=env)
    assert len(client.search_model_versions("name='wine-quality'")) == before + 1
    assert int(second.run.model_version) == before + 1


def test_run_record_file(tracked) -> None:
    result, _ = tracked
    assert RunRecord.read(result.run_path) == result.run
    assert json.loads(result.run_path.read_text())["model_uri"] == (
        f"models:/wine-quality/{result.run.model_version}"
    )

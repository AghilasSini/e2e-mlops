"""specs/prediction-api "Serve the registry champion", against SQLite registries."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mlflow import MlflowClient

from common.config import load_settings
from models.registry_manager import RegistryManager
from serving.app import create_app
from serving.model import ModelStore
from training.pipeline import run

CHAMPION_URI = "models:/wine-quality@champion"
SAMPLE = {
    "fixed_acidity": 7.4,
    "volatile_acidity": 0.7,
    "citric_acid": 0.0,
    "residual_sugar": 1.9,
    "chlorides": 0.076,
    "free_sulfur_dioxide": 11.0,
    "total_sulfur_dioxide": 34.0,
    "density": 0.9978,
    "ph": 3.51,
    "sulphates": 0.56,
    "alcohol": 9.4,
}


@pytest.fixture(scope="module")
def unpromoted_db(tmp_path_factory: pytest.TempPathFactory, repo_root: Path) -> Path:
    """Registry holding version 1 of wine-quality, without any alias."""
    db = tmp_path_factory.mktemp("reg") / "mlflow.db"
    env = {"MLFLOW_TRACKING_URI": f"sqlite:///{db}", "OUTPUT_DIR": str(db.parent / "out")}
    run(load_settings(root=repo_root, env=env), env=env)
    return db


def _copy(db: Path, tmp_path: Path) -> str:
    copy = tmp_path / "mlflow.db"
    shutil.copy(db, copy)
    return f"sqlite:///{copy}"


def _promote(uri: str, repo_root: Path) -> None:
    promotion = load_settings(root=repo_root, env={}).params.promotion
    RegistryManager(
        MlflowClient(tracking_uri=uri, registry_uri=uri), "wine-quality", promotion
    ).evaluate("1")


@pytest.fixture()
def champion_registry(unpromoted_db: Path, tmp_path: Path, repo_root: Path, monkeypatch) -> str:
    uri = _copy(unpromoted_db, tmp_path)
    _promote(uri, repo_root)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)
    return uri


# Scenario: Champion served
def test_champion_served(champion_registry: str) -> None:
    with TestClient(create_app(CHAMPION_URI)) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["model_version"] == "wine-quality/1"
        body = client.post("/predict", json=SAMPLE).json()
        assert body["model_version"] == "wine-quality/1"
        assert body["label"] in ("good", "not_good")


# Scenario: No champion yet
def test_no_champion_yet(unpromoted_db: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MLFLOW_TRACKING_URI", _copy(unpromoted_db, tmp_path))
    with TestClient(create_app(CHAMPION_URI)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert client.post("/predict", json=SAMPLE).status_code == 503


def test_empty_registry_is_not_ready(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'empty.db'}")
    store = ModelStore(CHAMPION_URI)
    assert store.load() is False and not store.ready


def test_default_model_uri_is_the_champion(
    champion_registry: str, repo_root: Path, monkeypatch
) -> None:
    monkeypatch.delenv("MODEL_URI", raising=False)
    monkeypatch.chdir(repo_root)  # config/config.yaml -> serving.model_uri
    with TestClient(create_app()) as client:
        assert client.get("/health/ready").json()["model_version"] == "wine-quality/1"


def test_explicit_version_uri(champion_registry: str) -> None:
    store = ModelStore("models:/wine-quality/1")
    assert store.load() and store.version == "wine-quality/1"


def test_unknown_version_is_not_ready(champion_registry: str) -> None:
    assert ModelStore("models:/wine-quality/42").load() is False


def test_loaded_once_from_registry(champion_registry: str) -> None:
    with TestClient(create_app(CHAMPION_URI)) as client:
        for _ in range(100):
            assert client.post("/predict", json=SAMPLE).status_code == 200
        assert client.app.state.store.load_count == 1


def test_malformed_registry_uri_keeps_service_alive(champion_registry: str) -> None:
    with pytest.raises(ValueError, match="unsupported registry URI"):
        ModelStore("models:/a/b/c").load()
    with TestClient(create_app("models:/a/b/c")) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503


def test_local_development_store(repo_root: Path, tmp_path: Path, monkeypatch) -> None:
    """No MLFLOW_TRACKING_URI: train, promote and serve all use <OUTPUT_DIR>/mlflow.db."""
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    env = {"OUTPUT_DIR": str(tmp_path / "out")}
    run(load_settings(root=repo_root, env=env), env=env)
    _promote(f"sqlite:///{tmp_path / 'out' / 'mlflow.db'}", repo_root)
    store = ModelStore(CHAMPION_URI, env={**env, "APP_ROOT": str(repo_root)})
    assert store.load() and store.version == "wine-quality/1"


# Scenario: Pinned version (Epic 3: champion rollout pins models:/<name>/<N>)
def test_pinned_version_while_another_is_champion(champion_registry: str, monkeypatch) -> None:
    client = MlflowClient(tracking_uri=champion_registry, registry_uri=champion_registry)
    first = client.get_model_version("wine-quality", "1")
    for _ in range(3):  # versions 2, 3 and 4, same weights as version 1
        client.create_model_version("wine-quality", first.source, run_id=first.run_id)
    client.set_registered_model_alias("wine-quality", "champion", "4")
    assert str(client.get_model_version_by_alias("wine-quality", "champion").version) == "4"

    monkeypatch.setenv("MODEL_URI", "models:/wine-quality/3")
    with TestClient(create_app(model_name="wine-quality")) as api:
        assert api.get("/health/ready").json()["model_version"] == "wine-quality/3"
        assert api.post("/predict", json=SAMPLE).json()["model_version"] == "wine-quality/3"
        oip = {
            "inputs": [
                {"name": k, "shape": [1], "datatype": "FP64", "data": [v]}
                for k, v in SAMPLE.items()
            ]
        }
        infer = api.post("/v2/models/wine-quality/infer", json=oip).json()
        assert infer["model_version"] == "wine-quality/3"

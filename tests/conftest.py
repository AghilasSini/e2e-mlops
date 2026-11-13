"""Shared fixtures: the repository root and a throw-away copy of its configuration."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
# MLflow prints an "agent hint" on import; keep test output clean.
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
SEED_CSV = REPO_ROOT / "data" / "raw" / "winequality-red.csv"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture()
def app_root(tmp_path: Path) -> Path:
    """A temporary application root holding the real config files and seed data."""
    (tmp_path / "config").mkdir()
    shutil.copy(REPO_ROOT / "config" / "config.yaml", tmp_path / "config" / "config.yaml")
    shutil.copy(REPO_ROOT / "params.yaml", tmp_path / "params.yaml")
    shutil.copy(REPO_ROOT / "schema.yaml", tmp_path / "schema.yaml")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    shutil.copy(SEED_CSV, tmp_path / "data" / "raw" / SEED_CSV.name)
    return tmp_path


@pytest.fixture(scope="session")
def trained_model_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Train the real pipeline once per session into a temporary output directory."""
    from common.config import load_settings
    from training.pipeline import run

    out = tmp_path_factory.mktemp("model")
    settings = load_settings(root=REPO_ROOT, env={"OUTPUT_DIR": str(out)})
    run(settings, env={"MODEL_VERSION": "test-model-1"})
    return out


@pytest.fixture(autouse=True)
def _isolate_mlflow_uris(monkeypatch: pytest.MonkeyPatch) -> None:
    """MLflow's set_tracking_uri/set_registry_uri also write these environment variables,
    which later tests and child processes would inherit. Start each test without them;
    monkeypatch removes whatever a test sets."""
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.delenv("MLFLOW_REGISTRY_URI", raising=False)

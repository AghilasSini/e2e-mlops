"""models.serving_sync: resolve the champion alias to the exact version to serve."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mlflow import MlflowClient

from models.serving_sync import main, resolve


@pytest.fixture()
def env(tmp_path: Path, repo_root: Path) -> dict[str, str]:
    uri = f"sqlite:///{tmp_path / 'registry.db'}"
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    client.create_registered_model("wine-quality")
    for _ in range(3):  # metadata only: resolving never loads the model
        client.create_model_version("wine-quality", source=str(tmp_path / "model"))
    return {"MLFLOW_TRACKING_URI": uri, "APP_ROOT": str(repo_root)}


def _set_champion(env: dict[str, str], version: str) -> None:
    uri = env["MLFLOW_TRACKING_URI"]
    MlflowClient(tracking_uri=uri, registry_uri=uri).set_registered_model_alias(
        "wine-quality", "champion", version
    )


def test_resolve_with_champion(env: dict[str, str]) -> None:
    _set_champion(env, "2")
    champion = resolve(env)
    assert champion is not None
    assert champion["champion_version"] == "2"
    assert champion["model_name"] == "wine-quality"
    assert champion["resolved_at"].endswith("Z")


def test_resolve_without_champion(env: dict[str, str]) -> None:
    assert resolve(env) is None


def test_cli_prints_one_json_line(env: dict[str, str], capsys: pytest.CaptureFixture) -> None:
    _set_champion(env, "3")
    assert main(["resolve"], env=env) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["champion_version"] == "3"


def test_cli_exits_1_without_champion(env: dict[str, str], capsys: pytest.CaptureFixture) -> None:
    assert main(["resolve"], env=env) == 1
    assert capsys.readouterr().out == ""


def test_cli_exits_2_when_registry_unreachable(
    repo_root: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # MLflow reads its HTTP retry settings from the process environment.
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_TIMEOUT", "2")
    env = {"MLFLOW_TRACKING_URI": "http://127.0.0.1:9", "APP_ROOT": str(repo_root)}
    assert main(["resolve"], env=env) == 2
    assert capsys.readouterr().out == ""

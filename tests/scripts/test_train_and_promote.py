from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from mlflow import MlflowClient

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "train-and-promote.sh"


def _run(
    repo_root: Path, tmp_path: Path, store: str, app_root: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}",
        "APP_ROOT": str(app_root or repo_root),
        "DATA_PATH": str(repo_root / "data" / "raw" / "winequality-red.csv"),
        "OUTPUT_DIR": str(tmp_path / "out"),
        "MLFLOW_TRACKING_URI": store,
        "MLFLOW_DISABLE_AGENT_HINT": "1",
    }
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def _decision(stdout: str) -> dict[str, Any]:
    """The promotion decision: the JSON line that has a `promoted` key."""
    lines = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
    return next(d for d in lines if "promoted" in d)


def _champion(stdout: str) -> dict[str, Any]:
    """Contract with scripts/serve-champion.sh: the last stdout line is the champion."""
    champion = json.loads(stdout.strip().splitlines()[-1])
    assert {"champion_version", "model_name", "resolved_at"} <= set(champion)
    return champion


def test_first_run_trains_registers_and_promotes(repo_root: Path, tmp_path: Path) -> None:
    store = f"sqlite:///{tmp_path / 'mlflow.db'}"
    result = _run(repo_root, tmp_path, store)
    assert result.returncode == 0, result.stderr[-2000:]
    decision = _decision(result.stdout)
    assert decision["promoted"] is True and decision["candidate_version"] == "1"
    champion = _champion(result.stdout)
    assert champion["champion_version"] == "1" and champion["model_name"] == "wine-quality"
    client = MlflowClient(tracking_uri=store, registry_uri=store)
    assert str(client.get_model_version_by_alias("wine-quality", "champion").version) == "1"


def test_second_identical_run_is_rejected_but_exits_zero(repo_root: Path, tmp_path: Path) -> None:
    store = f"sqlite:///{tmp_path / 'mlflow.db'}"
    first = _run(repo_root, tmp_path, store)
    assert first.returncode == 0, first.stderr[-2000:]
    first_decision = _decision(first.stdout)
    second = _run(repo_root, tmp_path, store)
    assert second.returncode == 0, second.stderr[-2000:]
    decision = _decision(second.stdout)
    assert decision["promoted"] is False, (first_decision, decision)
    assert _champion(second.stdout)["champion_version"] == "1"  # still served
    assert decision["candidate_version"] == "2"
    client = MlflowClient(tracking_uri=store, registry_uri=store)
    assert str(client.get_model_version_by_alias("wine-quality", "champion").version) == "1"


def test_training_failure_propagates(repo_root: Path, tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}",
            "APP_ROOT": str(repo_root),
            "OUTPUT_DIR": str(tmp_path / "out"),
            "DATA_PATH": str(tmp_path / "missing.csv"),
            "MLFLOW_TRACKING_URI": f"sqlite:///{tmp_path / 'mlflow.db'}",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "evaluating registered version" not in result.stdout


def test_rejected_first_candidate_leaves_nothing_to_serve(repo_root: Path, tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    (app_root / "config").mkdir(parents=True)
    shutil.copy(repo_root / "config" / "config.yaml", app_root / "config" / "config.yaml")
    shutil.copy(repo_root / "schema.yaml", app_root / "schema.yaml")
    params = (repo_root / "params.yaml").read_text()
    assert "min_roc_auc: 0.78" in params
    (app_root / "params.yaml").write_text(params.replace("min_roc_auc: 0.78", "min_roc_auc: 0.99"))
    result = _run(repo_root, tmp_path, f"sqlite:///{tmp_path / 'mlflow.db'}", app_root)
    assert result.returncode == 0, result.stderr[-2000:]
    assert _decision(result.stdout)["promoted"] is False
    # The decision has its own (null) champion_version: only `resolved_at` marks the
    # champion line, and it must be absent.
    assert "resolved_at" not in result.stdout
    assert "no champion yet: nothing to serve" in result.stderr

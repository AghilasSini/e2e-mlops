"""specs/model-promotion: one test per scenario.

A registry is built once per module with four versions trained on purpose:

=====  ==========================  =================================
v1     weak   (alpha=200, l1=1)    roc_auc ~0.78: above the test gate
v2     good   (alpha=0.5, l1=0.7)  roc_auc ~0.82
v3     good   (same as v2)         identical scores: cannot beat v2
v4     bad    (alpha=1000, l1=1)   roc_auc 0.50: below the gate
=====  ==========================  =================================

Each test works on its own copy of the registry database (aliases and tags are
written there), so tests are independent. Thresholds are set here, not taken from
params.yaml, so these tests don't depend on the calibration in task 3.3.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from mlflow import MlflowClient

from common.config import PromotionParams, load_settings
from models.registry_manager import RegistryError, RegistryManager, main
from training.pipeline import run

GATE = PromotionParams(min_roc_auc=0.70, min_f1=0.60, min_improvement=0.005)
VARIANTS = {"1": (200, 1.0), "2": (0.5, 0.7), "3": (0.5, 0.7), "4": (1000, 1.0)}


@pytest.fixture(scope="module")
def built_registry(tmp_path_factory: pytest.TempPathFactory, repo_root: Path) -> Path:
    root = tmp_path_factory.mktemp("root")
    (root / "config").mkdir()
    shutil.copy(repo_root / "config" / "config.yaml", root / "config" / "config.yaml")
    shutil.copy(repo_root / "schema.yaml", root / "schema.yaml")
    db = tmp_path_factory.mktemp("registry") / "registry.db"
    params_text = (repo_root / "params.yaml").read_text()
    for version, (alpha, l1_ratio) in VARIANTS.items():
        (root / "params.yaml").write_text(
            params_text.replace("alpha: 0.5", f"alpha: {alpha}").replace(
                "l1_ratio: 0.7", f"l1_ratio: {l1_ratio}"
            )
        )
        env = {
            "MLFLOW_TRACKING_URI": f"sqlite:///{db}",
            "DATA_PATH": str(repo_root / "data/raw/winequality-red.csv"),
            "OUTPUT_DIR": str(root / f"out{version}"),
            "MODEL_VERSION": f"v{version}",
        }
        assert run(load_settings(root=root, env=env), env=env).run.model_version == version
    return db


@pytest.fixture()
def registry(built_registry: Path, tmp_path: Path) -> str:
    copy = tmp_path / "registry.db"
    shutil.copy(built_registry, copy)
    return f"sqlite:///{copy}"


@pytest.fixture()
def manager(registry: str) -> RegistryManager:
    return RegistryManager(
        MlflowClient(tracking_uri=registry, registry_uri=registry), "wine-quality", GATE
    )


def _alias(manager: RegistryManager, alias: str) -> str | None:
    version = manager._by_alias(alias)
    return None if version is None else str(version.version)


def _tags(manager: RegistryManager, version: str) -> dict[str, str]:
    return manager.client.get_model_version("wine-quality", version).tags


# Scenario: First model
def test_first_model_becomes_champion(manager: RegistryManager) -> None:
    decision = manager.evaluate("1")
    assert decision.promoted and "first champion" in decision.reason
    assert _alias(manager, "champion") == "1"
    assert _alias(manager, "previous-champion") is None


# Scenario: Better candidate (+ Alias move)
def test_better_candidate_replaces_champion(manager: RegistryManager) -> None:
    manager.evaluate("1")
    decision = manager.evaluate("2")
    assert decision.promoted
    assert decision.champion_version == "1"
    assert decision.candidate["roc_auc"] >= decision.champion["roc_auc"] + GATE.min_improvement
    assert _alias(manager, "champion") == "2"
    assert _alias(manager, "previous-champion") == "1"


# Scenario: Not better
def test_equal_candidate_is_rejected(manager: RegistryManager) -> None:
    manager.evaluate("2")
    decision = manager.evaluate("3")
    assert not decision.promoted
    assert decision.reason.startswith("did not beat champion v2")
    assert decision.candidate["roc_auc"] == pytest.approx(decision.champion["roc_auc"])
    assert _alias(manager, "champion") == "2"


def test_champion_is_rescored_on_candidate_test_set(manager: RegistryManager) -> None:
    manager.evaluate("1")
    decision = manager.evaluate("2")
    logged = manager.client.get_run(manager._version("1").run_id).data.metrics["roc_auc"]
    # same split here, so the re-score equals v1's logged value
    assert decision.champion["roc_auc"] == pytest.approx(logged)


# Scenario: Below threshold
def test_below_threshold_is_rejected_and_named(manager: RegistryManager) -> None:
    decision = manager.evaluate("4")
    assert not decision.promoted
    assert "min_roc_auc" in decision.reason
    assert _alias(manager, "champion") is None


# Scenario: Rejected candidate (recorded decisions)
def test_rejection_is_recorded_as_tags_and_json(
    manager: RegistryManager, registry, capsys, monkeypatch, repo_root
) -> None:
    manager.evaluate("2")
    monkeypatch.chdir(repo_root)
    env = {"MLFLOW_TRACKING_URI": registry, "APP_ROOT": str(repo_root)}
    assert main(["evaluate", "--version", "3"], env=env) == 0
    output = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert output["promoted"] is False
    assert output["candidate_version"] == "3"
    tags = _tags(manager, "3")
    assert tags["promotion_status"] == "rejected"
    assert tags["promotion_reason"].startswith("did not beat champion")
    assert "candidate_roc_auc" in tags and "champion_roc_auc" in tags
    assert tags["promotion_decided_at"].endswith("Z")


def test_promotion_is_tagged(manager: RegistryManager) -> None:
    manager.evaluate("1")
    assert _tags(manager, "1")["promotion_status"] == "promoted"


# Scenario: Dry run
def test_dry_run_changes_nothing(manager: RegistryManager) -> None:
    manager.evaluate("1")
    tags_before = _tags(manager, "2")
    decision = manager.evaluate("2", dry_run=True)
    assert decision.promoted and decision.dry_run
    assert _alias(manager, "champion") == "1"
    assert _alias(manager, "previous-champion") is None
    assert _tags(manager, "2") == tags_before


def test_already_champion(manager: RegistryManager) -> None:
    manager.evaluate("2")
    decision = manager.evaluate("2")
    assert not decision.promoted and decision.reason == "candidate is already the champion"


# Scenario: Revert a promotion
def test_rollback_restores_previous(manager: RegistryManager) -> None:
    manager.evaluate("1")
    manager.evaluate("2")
    result = manager.rollback()
    assert result["champion_version"] == "1"
    assert result["rolled_back_version"] == "2"
    assert _alias(manager, "champion") == "1"
    assert _alias(manager, "previous-champion") is None
    assert _tags(manager, "2")["promotion_status"] == "rolled_back"
    assert "rolled_back_at" in _tags(manager, "2")
    assert "restored_at" in _tags(manager, "1")


def test_rollback_without_previous_fails(
    manager: RegistryManager, registry, repo_root, caplog
) -> None:
    manager.evaluate("1")
    with pytest.raises(RegistryError, match="nothing to roll back"):
        manager.rollback()
    assert (
        main(["rollback"], env={"MLFLOW_TRACKING_URI": registry, "APP_ROOT": str(repo_root)}) == 1
    )


# Scenario: Unknown version (exit status)
def test_unknown_version_exits_nonzero(registry, repo_root, caplog) -> None:
    code = main(
        ["evaluate", "--version", "99"],
        env={"MLFLOW_TRACKING_URI": registry, "APP_ROOT": str(repo_root)},
    )
    assert code == 1
    assert "unknown model version wine-quality/99" in caplog.text


def test_unreachable_registry_exits_nonzero(repo_root, monkeypatch) -> None:
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_TIMEOUT", "2")
    env = {"MLFLOW_TRACKING_URI": "http://127.0.0.1:9", "APP_ROOT": str(repo_root)}
    assert main(["evaluate", "--version", "1"], env=env) == 1


def test_cli_promotes_and_prints_json(registry, repo_root, capsys) -> None:
    env = {"MLFLOW_TRACKING_URI": registry, "APP_ROOT": str(repo_root)}
    assert main(["evaluate", "--version", "2", "--dry-run"], env=env) == 0
    output = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert output["promoted"] is True and output["dry_run"] is True
    assert set(output["thresholds"]) == {"min_roc_auc", "min_f1", "min_improvement"}


def test_missing_test_set_is_an_error(manager: RegistryManager, monkeypatch) -> None:
    monkeypatch.setattr(
        manager.client, "download_artifacts", lambda *a, **k: (_ for _ in ()).throw(OSError("gone"))
    )
    with pytest.raises(RegistryError, match="test set"):
        manager.evaluate("1")


def test_default_model_passes_the_calibrated_gate(registry: str, repo_root: Path) -> None:
    """Task 3.3: the default hyper-parameters (v2) meet params.yaml's minimums."""
    promotion = load_settings(root=repo_root, env={}).params.promotion
    manager = RegistryManager(
        MlflowClient(tracking_uri=registry, registry_uri=registry), "wine-quality", promotion
    )
    decision = manager.evaluate("2")
    assert decision.promoted, decision.reason
    assert decision.candidate["roc_auc"] >= promotion.min_roc_auc
    assert decision.candidate["f1"] >= promotion.min_f1


def test_degenerate_model_fails_the_calibrated_gate(registry: str, repo_root: Path) -> None:
    promotion = load_settings(root=repo_root, env={}).params.promotion
    manager = RegistryManager(
        MlflowClient(tracking_uri=registry, registry_uri=registry), "wine-quality", promotion
    )
    assert not manager.evaluate("4").promoted


def test_scores_models_from_its_own_registry(
    manager: RegistryManager, repo_root: Path, tmp_path: Path
) -> None:
    """Regression: a stale process-global registry URI must not change what is scored.

    The decoy registry's version 1 is the *good* model; the manager's version 1 is
    the weak one. Without pinning, the manager would score the decoy's model.
    """
    import mlflow

    decoy = f"sqlite:///{tmp_path / 'decoy.db'}"
    env = {"MLFLOW_TRACKING_URI": decoy, "OUTPUT_DIR": str(tmp_path / "out")}
    assert run(load_settings(root=repo_root, env=env), env=env).run.model_version == "1"
    mlflow.set_registry_uri(decoy)  # also exported as MLFLOW_REGISTRY_URI
    mlflow.set_tracking_uri(decoy)
    weak = manager.evaluate("1", dry_run=True).candidate["roc_auc"]
    assert weak == pytest.approx(0.7824, abs=1e-3)  # the weak v1 of the manager's registry

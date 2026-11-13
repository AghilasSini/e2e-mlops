"""scripts/serve-champion.sh and the deploy.sh rollout rules, with fake kubectl and helm.

Covers the model-serving "Champion rollout" and "Pinned version survives code deploys"
scenarios and the cluster-deployment "KServe missing" scenario, without a cluster:
the fakes record their arguments and answer from environment variables.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

FAKE_KUBECTL = r"""#!/usr/bin/env bash
# kubectl stand-in. FAKE_CRD=1: KServe installed. FAKE_JOB_LOG: the Job's log file.
# FAKE_ISVC: name of the release's InferenceService; FAKE_ISVC_STATE: "gen observed Ready".
set -euo pipefail
echo "kubectl $*" >> "$FAKE_STATE/calls.log"
args=()
while [[ $# -gt 0 ]]; do
  case $1 in -n) shift 2 ;; *) args+=("$1"); shift ;; esac
done
set -- "${args[@]}"
case "$1 $2" in
  "get crd") [[ ${FAKE_CRD:-0} == 1 ]] ;;
  "logs job/"*) tail -n 1 "$FAKE_JOB_LOG" ;;
  "get inferenceservices")
    [[ -z ${FAKE_ISVC:-} ]] || echo "inferenceservice.serving.kserve.io/$FAKE_ISVC" ;;
  "get inferenceservice.serving.kserve.io/"*)
    case "$*" in
      *deploymentMode*) echo Standard ;;
      *conditions*Ready*) echo "${FAKE_ISVC_STATE:-2 2 True}" ;;
      *) ;;
    esac ;;
  "rollout status") ;;
  "create namespace") echo "kind: Namespace" ;;
  "apply -f") cat >/dev/null ;;
  "get namespace" | "get secret" | "get pods" | "cluster-info "*) ;;
  *) echo "fake kubectl: unsupported: $*" >&2; exit 1 ;;
esac
"""

FAKE_HELM = r"""#!/usr/bin/env bash
# helm stand-in. FAKE_VALUES_JSON: `get values -o json`. FAKE_UPGRADE_RC: upgrade status.
set -euo pipefail
echo "helm $*" >> "$FAKE_STATE/calls.log"
case $1 in
  status) [[ ${FAKE_NO_RELEASE:-0} != 1 ]] ;;
  get)
    case "$*" in
      *"-o json"*) echo "${FAKE_VALUES_JSON:-null}" ;;
      *) echo "${FAKE_VALUES_YAML:-{\}}" ;;
    esac ;;
  upgrade) exit "${FAKE_UPGRADE_RC:-0}" ;;
  rollback) ;;
  *) echo "fake helm: unsupported: $*" >&2; exit 1 ;;
esac
"""

CHAMPION_LINE = {"champion_version": "5", "model_name": "wine-quality", "resolved_at": "x"}
DECISION_LINE = {"promoted": False, "champion_version": None, "candidate_version": "1"}


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("kubectl", FAKE_KUBECTL), ("helm", FAKE_HELM)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_STATE": str(state),
        "FAKE_JOB_LOG": str(tmp_path / "job.log"),
        "HELM": "helm",
        "ROLLOUT_TIMEOUT": "3",
    }


def job_log(env: dict[str, str], *lines: str | dict) -> None:
    text = [json.dumps(line) if isinstance(line, dict) else line for line in lines]
    Path(env["FAKE_JOB_LOG"]).write_text("\n".join(text) + "\n")


def calls(env: dict[str, str]) -> list[str]:
    log = Path(env["FAKE_STATE"], "calls.log")
    return log.read_text().splitlines() if log.exists() else []


def upgrades(env: dict[str, str]) -> list[str]:
    return [c for c in calls(env) if c.startswith("helm upgrade")]


def serve(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPTS / "serve-champion.sh"), "local", *args],
        env=env,
        capture_output=True,
        text=True,
    )


def deploy(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPTS / "deploy.sh"), "local", *args],
        env=env,
        capture_output=True,
        text=True,
    )


# Scenario: New champion rolled out (version read from the training Job's log)
def test_version_from_job_log_is_rolled_out(env: dict[str, str]) -> None:
    job_log(env, "==> evaluating registered version 5", DECISION_LINE, CHAMPION_LINE)
    env["FAKE_VALUES_JSON"] = json.dumps({"inference": {"championVersion": 4}})
    result = serve(env, "--from-job", "train-1")
    assert result.returncode == 0, result.stderr
    [upgrade] = upgrades(env)
    assert "--reuse-values" in upgrade and "--atomic" in upgrade and "--wait" in upgrade
    assert "--set inference.championVersion=5" in upgrade
    assert "serving version 5" in result.stdout


def test_explicit_version(env: dict[str, str]) -> None:
    result = serve(env, "--version", "7")
    assert result.returncode == 0, result.stderr
    assert "--set inference.championVersion=7" in upgrades(env)[0]


# Scenario: Champion already served
def test_already_served_changes_nothing(env: dict[str, str]) -> None:
    job_log(env, CHAMPION_LINE)
    env["FAKE_VALUES_JSON"] = json.dumps({"inference": {"championVersion": 5}})
    result = serve(env, "--from-job", "train-2")
    assert result.returncode == 0, result.stderr
    assert "already served" in result.stdout
    assert upgrades(env) == []
    assert not any("rollout" in c or "rollback" in c for c in calls(env))


# Scenario: No champion
@pytest.mark.parametrize(
    "last_line",
    [
        "==> no champion yet: nothing to serve",
        json.dumps(DECISION_LINE),  # has champion_version (null) but is not the champion
        json.dumps({**DECISION_LINE, "champion_version": "3"}),
    ],
)
def test_no_champion_exits_non_zero(env: dict[str, str], last_line: str) -> None:
    job_log(env, last_line)
    result = serve(env, "--from-job", "train-3")
    assert result.returncode != 0
    assert "nothing to serve" in result.stderr
    assert upgrades(env) == []


@pytest.mark.parametrize("version", ["0", "abc", "1.5", ""])
def test_invalid_version_is_rejected(env: dict[str, str], version: str) -> None:
    result = serve(env, "--version", version)
    assert result.returncode != 0 and upgrades(env) == []


def test_usage_errors(env: dict[str, str]) -> None:
    assert serve(env).returncode == 2
    assert serve(env, "--latest", "x").returncode == 2


def test_failed_upgrade_exits_non_zero(env: dict[str, str]) -> None:
    env["FAKE_UPGRADE_RC"] = "1"
    result = serve(env, "--version", "9")
    assert result.returncode != 0
    assert "did not become ready" in result.stderr


def test_inference_service_never_ready_is_rolled_back(env: dict[str, str]) -> None:
    env.update(FAKE_CRD="1", FAKE_ISVC="wine-quality", FAKE_ISVC_STATE="3 3 False")
    result = serve(env, "--version", "9")
    assert result.returncode != 0
    assert "rolled back" in result.stderr
    assert any(c.startswith("helm rollback") for c in calls(env))


def test_waits_for_the_inference_service(env: dict[str, str]) -> None:
    env.update(FAKE_CRD="1", FAKE_ISVC="wine-quality", FAKE_ISVC_STATE="3 3 True")
    result = serve(env, "--version", "9")
    assert result.returncode == 0, result.stderr
    assert any("rollout status deployment/wine-quality-predictor" in c for c in calls(env))
    assert not any(c.startswith("helm rollback") for c in calls(env))


# Scenario: CI deploy after a promotion (pinned version survives code deploys)
def test_deploy_keeps_the_pinned_version(env: dict[str, str]) -> None:
    env["FAKE_VALUES_JSON"] = json.dumps({"inference": {"championVersion": 5}})
    result = deploy(env, "--set", "inference.mode=deployment")
    assert result.returncode == 0, result.stderr
    [upgrade] = upgrades(env)
    assert "--set inference.championVersion=5" in upgrade


def test_deploy_override_with_champion_version(env: dict[str, str]) -> None:
    env["FAKE_VALUES_JSON"] = json.dumps({"inference": {"championVersion": 5}})
    env["CHAMPION_VERSION"] = "6"
    result = deploy(env, "--set", "inference.mode=deployment")
    assert result.returncode == 0, result.stderr
    assert "--set inference.championVersion=6" in upgrades(env)[0]


def test_deploy_without_pinned_version_uses_the_alias(env: dict[str, str]) -> None:
    result = deploy(env, "--set", "inference.mode=deployment")
    assert result.returncode == 0, result.stderr
    assert "championVersion" not in upgrades(env)[0]


# Scenario: KServe missing
def test_deploy_stops_before_helm_without_kserve(env: dict[str, str]) -> None:
    result = deploy(env, "--set", "inference.mode=kserve")
    assert result.returncode != 0
    assert "scripts/kserve-up.sh local" in result.stderr
    assert "inference.mode=deployment" in result.stderr
    assert not any(c.startswith("helm") for c in calls(env))


def test_deploy_in_kserve_mode_with_kserve_installed(env: dict[str, str]) -> None:
    env.update(FAKE_CRD="1", FAKE_ISVC="wine-quality", FAKE_ISVC_STATE="1 1 True")
    result = deploy(env, "--set", "inference.mode=kserve")
    assert result.returncode == 0, result.stderr
    assert "inference mode: kserve" in result.stdout
    assert len(upgrades(env)) == 1

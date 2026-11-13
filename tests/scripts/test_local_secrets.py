"""scripts/local-secrets.sh against a fake kubectl that stores Secrets as files.

Checks the script's logic without a cluster: which Secrets and keys it creates,
the format of generated values, and that a rerun never rotates existing Secrets.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "local-secrets.sh"

FAKE_KUBECTL = r"""#!/usr/bin/env bash
# Minimal kubectl stand-in: namespaces and generic Secrets stored under $FAKE_KUBE.
set -euo pipefail
echo "$*" >> "$FAKE_KUBE/calls.log"
ns=default
args=()
while [[ $# -gt 0 ]]; do
  case $1 in
    -n) ns=$2; shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
set -- "${args[@]}"
case "$1 $2" in
  "get namespace") [[ -d $FAKE_KUBE/ns/$3 ]] ;;
  "create namespace") mkdir -p "$FAKE_KUBE/ns/$3" ;;
  "get secret") [[ -d $FAKE_KUBE/ns/$ns/secrets/$3 ]] ;;
  "create secret")
    name=$4; dir=$FAKE_KUBE/ns/$ns/secrets/$name; mkdir -p "$dir"; shift 4
    for a in "$@"; do kv=${a#--from-file=}; cp "${kv#*=}" "$dir/${kv%%=*}"; done ;;
  *) echo "unsupported: $*" >&2; exit 1 ;;
esac
"""

EXPECTED = {
    "e2e-mlops-postgres": {"username", "password"},
    "e2e-mlops-s3": {"access-key", "secret-key", "s3.json", "filer-signing-key"},
    "e2e-mlops-mlflow-admin": {"username", "password", "flask-secret-key"},
    "e2e-mlops-mlflow-trainer": {"username", "password"},
    "e2e-mlops-mlflow-serving": {"username", "password"},
    "e2e-mlops-feature-log": {"username", "password"},
}


@pytest.fixture()
def kube(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "kubectl"
    fake.write_text(FAKE_KUBECTL)
    fake.chmod(0o755)
    state = tmp_path / "kube"
    state.mkdir()
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_KUBE": str(state),
        "NAMESPACE": "e2e-mlops",
    }


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def _secrets(env: dict[str, str]) -> dict[str, dict[str, str]]:
    root = Path(env["FAKE_KUBE"], "ns", "e2e-mlops", "secrets")
    return {d.name: {f.name: f.read_text() for f in d.iterdir()} for d in sorted(root.iterdir())}


def test_creates_every_secret_and_key(kube) -> None:
    result = _run(kube)
    assert result.returncode == 0, result.stderr
    secrets = _secrets(kube)
    assert {name: set(keys) for name, keys in secrets.items()} == EXPECTED
    assert result.stdout.count("created") == 6


def test_generated_values_are_strong_and_url_safe(kube) -> None:
    _run(kube)
    secrets = _secrets(kube)
    for name, keys in secrets.items():
        for key, value in keys.items():
            if key in ("username", "s3.json"):
                continue
            assert re.fullmatch(r"[A-Za-z0-9]{32}", value), (name, key)
    assert secrets["e2e-mlops-mlflow-admin"]["username"] == "admin"
    assert secrets["e2e-mlops-feature-log"]["username"] == "feature_logger"
    assert len({s["password"] for s in secrets.values() if "password" in s}) == 5  # all distinct


def test_seaweedfs_config_matches_keys(kube) -> None:
    _run(kube)
    s3 = _secrets(kube)["e2e-mlops-s3"]
    [identity] = json.loads(s3["s3.json"])["identities"]
    assert identity["credentials"] == [
        {"accessKey": s3["access-key"], "secretKey": s3["secret-key"]}
    ]


def test_rerun_does_not_rotate(kube) -> None:
    _run(kube)
    before = _secrets(kube)
    second = _run(kube)
    assert second.returncode == 0
    assert _secrets(kube) == before
    assert second.stdout.count("exists, kept") == 6


def test_values_never_on_the_command_line(kube) -> None:
    _run(kube)
    calls = Path(kube["FAKE_KUBE"], "calls.log").read_text()
    for keys in _secrets(kube).values():
        for key, value in keys.items():
            if key != "username":
                assert value not in calls


DEPLOY = SCRIPT.parent / "deploy.sh"
PERMISSIVE_KUBECTL = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_KUBE/calls.log"
if [[ "$*" == *"--dry-run=client"* ]]; then
  for a in "$@"; do
    [[ $a == --from-file=* ]] || continue
    kv=${a#--from-file=}
    cp "${kv#*=}" "$FAKE_KUBE/${kv%%=*}.applied"
  done
  echo "kind: Secret"
elif [[ "$1 $2" == "apply -f" ]]; then cat > /dev/null
fi
exit 0
"""


@pytest.fixture()
def openstack_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "kubectl").write_text(PERMISSIVE_KUBECTL)
    (bin_dir / "helm").write_text('#!/usr/bin/env bash\necho "helm $*" >> "$FAKE_KUBE/helm.log"\n')
    for tool in ("kubectl", "helm"):
        (bin_dir / tool).chmod(0o755)
    state = tmp_path / "kube"
    state.mkdir()
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("POSTGRES_", "S3_", "MLFLOW_", "CI_", "FEATURE_LOG_", "CHAMPION_"))
    }
    return {
        **env,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_KUBE": str(state),
        "IMAGE_REPOSITORY": "registry.example.org/g/e2e-mlops",
        "IMAGE_TAG": "abc1234",
        "REGISTRY": "registry.example.org",
        "REGISTRY_USER": "deploy",
        "REGISTRY_PASSWORD": "pull-token-value",
    }


def test_openstack_requires_every_ci_secret(openstack_env) -> None:
    result = subprocess.run(
        ["bash", str(DEPLOY), "openstack"], env=openstack_env, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "CI variable POSTGRES_PASSWORD is required" in result.stderr
    assert not Path(openstack_env["FAKE_KUBE"], "helm.log").exists()  # stopped before helm


def test_openstack_applies_secrets_without_exposing_them(openstack_env) -> None:
    values = {
        "POSTGRES_PASSWORD": "pg-password-value-123",
        "S3_ACCESS_KEY": "s3-access-value-123",
        "S3_SECRET_KEY": "s3-secret-value-123",
        "MLFLOW_ADMIN_PASSWORD": "admin-password-value-123",
        "MLFLOW_FLASK_SECRET_KEY": "flask-key-value-123",
        "MLFLOW_TRAINER_PASSWORD": "trainer-password-value-123",
        "MLFLOW_SERVING_PASSWORD": "serving-password-value-123",
        "FEATURE_LOG_PASSWORD": "feature-log-password-value-123",
    }
    result = subprocess.run(
        ["bash", str(DEPLOY), "openstack"],
        env={**openstack_env, **values},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("applied") == 6
    calls = Path(openstack_env["FAKE_KUBE"], "calls.log").read_text()
    assert all(v not in calls for v in values.values())
    applied = Path(openstack_env["FAKE_KUBE"], "flask-secret-key.applied").read_text()
    assert applied == values["MLFLOW_FLASK_SECRET_KEY"]
    assert "--atomic" in Path(openstack_env["FAKE_KUBE"], "helm.log").read_text()


def test_openstack_requires_the_feature_log_password(openstack_env) -> None:
    values = {
        "POSTGRES_PASSWORD": "pg-password-value-123",
        "S3_ACCESS_KEY": "s3-access-value-123",
        "S3_SECRET_KEY": "s3-secret-value-123",
        "MLFLOW_ADMIN_PASSWORD": "admin-password-value-123",
        "MLFLOW_FLASK_SECRET_KEY": "flask-key-value-123",
        "MLFLOW_TRAINER_PASSWORD": "trainer-password-value-123",
        "MLFLOW_SERVING_PASSWORD": "serving-password-value-123",
    }
    result = subprocess.run(
        ["bash", str(DEPLOY), "openstack"],
        env={**openstack_env, **values},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "CI variable FEATURE_LOG_PASSWORD is required" in result.stderr
    assert not Path(openstack_env["FAKE_KUBE"], "helm.log").exists()

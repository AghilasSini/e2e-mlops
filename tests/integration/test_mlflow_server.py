"""specs/tracking-platform against a real MLflow server (basic-auth) on localhost.

Starts scripts/mlflow-server.sh with SQLite stores and a local artifact destination,
runs scripts/mlflow_bootstrap.py, then exercises the service users over HTTP.
The Kubernetes-only parts (PostgreSQL, SeaweedFS, pod restarts) are covered by the
cluster integration check (Epic 2 task 5.4).
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _status(url: str, auth: tuple[str, str] | None = None) -> int:
    request = urllib.request.Request(url)
    if auth:
        import base64

        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    work = tmp_path_factory.mktemp("mlflow-server")
    (work / "art").mkdir()
    port = _free_port()
    creds = {name: secrets.token_urlsafe(16) for name in ("admin", "trainer", "serving", "flask")}
    bin_dir = str(Path(sys.executable).parent)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TMPDIR": str(work),
        "MLFLOW_PORT": str(port),
        "MLFLOW_BACKEND_STORE_URI": f"sqlite:///{work / 'mlflow.db'}",
        "MLFLOW_AUTH_DATABASE_URI": f"sqlite:///{work / 'auth.db'}",
        "MLFLOW_ARTIFACTS_DESTINATION": (work / "art").as_uri(),
        "MLFLOW_AUTH_ADMIN_PASSWORD": creds["admin"],
        "MLFLOW_FLASK_SERVER_SECRET_KEY": creds["flask"],
        "MLFLOW_DISABLE_AGENT_HINT": "1",
    }
    log = (work / "server.log").open("w")
    proc = subprocess.Popen(
        ["bash", str(REPO / "scripts" / "mlflow-server.sh")], env=env, stdout=log, stderr=log
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(90):
            if proc.poll() is not None:
                pytest.fail(f"server exited: {(work / 'server.log').read_text()[-2000:]}")
            try:
                if _status(f"{url}/health") == 200:
                    break
            except OSError:
                pass
            time.sleep(1)
        else:
            pytest.fail("server did not become healthy")
        boot_env = {
            **env,
            "MLFLOW_TRACKING_URI": url,
            "MLFLOW_TRACKING_USERNAME": "admin",
            "MLFLOW_TRACKING_PASSWORD": creds["admin"],
            "TRAINER_PASSWORD": creds["trainer"],
            "SERVING_PASSWORD": creds["serving"],
            "WAIT_SECONDS": "30",
        }
        for _ in range(2):  # the bootstrap must be idempotent
            boot = subprocess.run(
                [sys.executable, str(REPO / "scripts" / "mlflow_bootstrap.py")],
                env=boot_env,
                capture_output=True,
                text=True,
            )
            assert boot.returncode == 0, boot.stderr[-2000:]
        yield {"url": url, "work": str(work), "pid": str(proc.pid), **creds}
    finally:
        proc.terminate()  # uvicorn itself (see test_server_is_one_process)
        proc.wait(timeout=30)
        log.close()


SEARCH = "/api/2.0/mlflow/experiments/search?max_results=1"


# Scenario: Anonymous request
def test_anonymous_request_is_rejected(server) -> None:
    assert _status(server["url"] + SEARCH) == 401
    assert _status(server["url"] + SEARCH, ("trainer", "wrong")) == 401


def test_service_users_can_read(server) -> None:
    assert _status(server["url"] + SEARCH, ("trainer", server["trainer"])) == 200
    assert _status(server["url"] + SEARCH, ("serving", server["serving"])) == 200


# Scenario: Automated client (+ logging through the server, no object-store keys)
def test_trainer_trains_registers_and_promotes(server, tmp_path: Path) -> None:
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}",
        "APP_ROOT": str(REPO),
        "OUTPUT_DIR": str(tmp_path / "out"),
        "MLFLOW_TRACKING_URI": server["url"],
        "MLFLOW_TRACKING_USERNAME": "trainer",
        "MLFLOW_TRACKING_PASSWORD": server["trainer"],
        "MLFLOW_DISABLE_AGENT_HINT": "1",
    }
    env.pop("AWS_ACCESS_KEY_ID", None)
    env.pop("AWS_SECRET_ACCESS_KEY", None)
    result = subprocess.run(
        ["bash", str(REPO / "scripts" / "train-and-promote.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert '"promoted": true' in result.stdout
    stored = {p.name for p in Path(server["work"], "art").rglob("*") if p.is_file()}
    assert {"model.skops", "test.csv", "reference.csv", "metrics.json"} <= stored


def test_serving_user_reads_champion_but_cannot_promote(server, monkeypatch) -> None:
    from mlflow import MlflowClient
    from mlflow.exceptions import MlflowException

    from serving.model import ModelStore

    monkeypatch.setenv("MLFLOW_TRACKING_URI", server["url"])
    monkeypatch.setenv("MLFLOW_TRACKING_USERNAME", "serving")
    monkeypatch.setenv("MLFLOW_TRACKING_PASSWORD", server["serving"])
    monkeypatch.setenv("APP_ROOT", str(REPO))
    store = ModelStore("models:/wine-quality@champion")
    assert store.load() and store.version == "wine-quality/1"
    with pytest.raises(MlflowException) as exc:
        MlflowClient().set_registered_model_alias("wine-quality", "champion", "1")
    assert exc.value.error_code == "PERMISSION_DENIED"


def test_no_secret_in_server_log(server) -> None:
    text = Path(server["work"], "server.log").read_text()
    for name in ("admin", "trainer", "serving", "flask"):
        assert server[name] not in text


def test_server_is_one_process(server) -> None:
    """uvicorn replaced the `mlflow server` launcher: one process, no idle parent.

    The started `bash mlflow-server.sh` exec'd the launcher, which exec'd uvicorn (one
    worker, in-process), so the PID started by the fixture is uvicorn, with no children.
    """
    pid = server["pid"]
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    assert b"uvicorn" in cmdline, cmdline
    children = [
        child
        for task in Path(f"/proc/{pid}/task").iterdir()
        for child in (task / "children").read_text().split()
    ]
    assert children == []

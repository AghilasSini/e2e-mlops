"""scripts/mlflow_server_exec.py: `mlflow server` with uvicorn exec'd, not spawned."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "mlflow_server_exec", Path(__file__).resolve().parents[2] / "scripts" / "mlflow_server_exec.py"
)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)

UVICORN = [sys.executable, "-m", "uvicorn", "--port", "5000", "--factory", "app:create"]


class Exec(Exception):
    """Raised by the fake execvpe: the real one never returns."""

    def __init__(self, file: str, argv: list[str], env: dict[str, str]) -> None:
        super().__init__(file)
        self.file, self.argv, self.env = file, argv, env


def _fake_execvpe(file, args, env):
    raise Exec(file, list(args), dict(env))


def _never_called(*args, **kwargs):
    pytest.fail("the server spawn must not go through the original _exec_cmd")


def test_server_spawn_is_execed_with_mlflow_env(monkeypatch) -> None:
    monkeypatch.setenv("AMBIENT", "kept")
    exec_cmd = launcher.make_exec_cmd(_never_called, execvpe=_fake_execvpe)
    with pytest.raises(Exec) as caught:
        exec_cmd(
            UVICORN,
            extra_env={"_MLFLOW_SGI_NAME": "uvicorn"},
            capture_output=False,
            synchronous=False,
        )
    assert caught.value.file == sys.executable
    assert caught.value.argv == UVICORN
    assert caught.value.env["_MLFLOW_SGI_NAME"] == "uvicorn"
    assert caught.value.env["AMBIENT"] == "kept"


@pytest.mark.parametrize(
    ("cmd", "kwargs"),
    [
        (UVICORN, {"synchronous": True}),  # waits for a result: not the server
        (UVICORN, {}),  # _exec_cmd defaults to synchronous
        (["huey_consumer", "mlflow.server.jobs"], {"synchronous": False}),
        ("python -m uvicorn app", {"synchronous": False}),  # a string, not the spawn
        (UVICORN, {"synchronous": False, "env": {"X": "1"}}),  # explicit env: keep as is
    ],
)
def test_other_commands_are_delegated(cmd, kwargs) -> None:
    calls = []

    def original(cmd, *, extra_env=None, **kw):
        calls.append((cmd, extra_env, kw))
        return "delegated"

    exec_cmd = launcher.make_exec_cmd(original, execvpe=_fake_execvpe)
    assert exec_cmd(cmd, extra_env={"A": "1"}, **kwargs) == "delegated"
    assert calls == [(cmd, {"A": "1"}, kwargs)]


JOBS = "MLFLOW_SERVER_ENABLE_JOB_EXECUTION"


@pytest.mark.parametrize(
    ("value", "enabled"),
    [(None, True), ("false", False), ("0", False), ("true", True), ("True", True), ("1", True)],
)
def test_job_execution_flag_follows_mlflow(monkeypatch, value, enabled) -> None:
    if value is None:
        monkeypatch.delenv(JOBS, raising=False)  # MLflow's default is on
    else:
        monkeypatch.setenv(JOBS, value)
    assert launcher.job_execution_enabled() is enabled


def _run_main(monkeypatch, job_execution: str):
    import mlflow.cli
    import mlflow.server

    original = mlflow.server._exec_cmd
    monkeypatch.setattr(mlflow.server, "_exec_cmd", original)  # restored after the test
    monkeypatch.setenv(JOBS, job_execution)
    seen = {}
    monkeypatch.setattr(mlflow.cli.cli, "main", lambda args, prog_name: seen.update(args=args) or 0)
    assert launcher.main(["server", "--port", "5000"]) == 0
    assert seen["args"] == ["server", "--port", "5000"]
    return original, mlflow.server._exec_cmd


def test_main_patches_the_spawn(monkeypatch) -> None:
    original, patched = _run_main(monkeypatch, "false")
    assert patched is not original


def test_main_leaves_mlflow_alone_when_jobs_are_enabled(monkeypatch) -> None:
    original, patched = _run_main(monkeypatch, "true")
    assert patched is original


def test_contract_with_the_pinned_mlflow(monkeypatch, tmp_path) -> None:
    """mlflow.server._run_server still spawns uvicorn through mlflow.server._exec_cmd.

    If an MLflow upgrade changes this, the launcher would silently fall back to two
    processes (or break): this test fails first.
    """
    import mlflow.server

    monkeypatch.setenv(JOBS, "false")  # as scripts/mlflow-server.sh sets it
    monkeypatch.setattr(
        mlflow.server, "_exec_cmd", launcher.make_exec_cmd(_never_called, execvpe=_fake_execvpe)
    )
    with pytest.raises(Exec) as caught:
        mlflow.server._run_server(
            file_store_path=None,
            registry_store_uri=None,
            default_artifact_root=None,
            serve_artifacts=True,
            artifacts_only=True,
            artifacts_destination=tmp_path.as_uri(),
            host="127.0.0.1",
            port=5000,
            workers=1,
        )
    args, env = caught.value.argv, caught.value.env
    assert args[1:3] == ["-m", "uvicorn"]
    assert args[args.index("--workers") + 1] == "1"  # one worker: uvicorn serves in-process
    assert env[mlflow.server.SERVE_ARTIFACTS_ENV_VAR] == "true"
    assert env[mlflow.server.ARTIFACTS_DESTINATION_ENV_VAR] == tmp_path.as_uri()

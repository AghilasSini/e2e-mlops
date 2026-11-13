"""Run ``mlflow server`` so that uvicorn *replaces* the launcher process.

    python mlflow_server_exec.py server --host 0.0.0.0 --port 5000 --workers 1 ...

``mlflow server`` prepares everything (backend store and its migrations, the basic-auth
admin, security-middleware settings, the environment the workers read), then starts
uvicorn as a child process and only waits for it. That idle parent keeps all of MLflow
imported: about 250 MiB, nearly half the local memory budget of the tracking server,
which was OOMKilled at its 640 MiB limit (docs/epic3-verification.md, Findings).

This launcher runs the same CLI unchanged, except that the final spawn becomes an
``exec``: the uvicorn command and environment MLflow built replace this process. With
one worker uvicorn serves in-process, so the container runs a single Python process,
and uvicorn, now the main process, handles SIGTERM itself.

The background job runner (``MLFLOW_SERVER_ENABLE_JOB_EXECUTION``) is started by the
parent *after* the spawn, so it needs the parent: when it is enabled, nothing is
changed and ``mlflow server`` runs as usual. MLflow enables it when the variable is
unset; scripts/mlflow-server.sh sets it to false.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any


def job_execution_enabled() -> bool:
    """MLflow's own reading of MLFLOW_SERVER_ENABLE_JOB_EXECUTION (default: on)."""
    from mlflow.environment_variables import MLFLOW_SERVER_ENABLE_JOB_EXECUTION

    return bool(MLFLOW_SERVER_ENABLE_JOB_EXECUTION.get())


def is_server_spawn(cmd: Any, synchronous: bool) -> bool:
    """The asynchronous ``python -m uvicorn ...`` spawn of ``mlflow.server._run_server``."""
    return (
        not synchronous
        and isinstance(cmd, list | tuple)
        and len(cmd) >= 3
        and list(map(str, cmd[1:3])) == ["-m", "uvicorn"]
    )


def make_exec_cmd(
    original: Callable[..., Any],
    execvpe: Callable[[str, Sequence[str], Mapping[str, str]], Any] = os.execvpe,
) -> Callable[..., Any]:
    """Wrap ``mlflow.utils.process._exec_cmd``: exec the server spawn, delegate the rest."""

    def exec_cmd(cmd: Any, *, extra_env: Mapping[str, str] | None = None, **kwargs: Any) -> Any:
        if not is_server_spawn(cmd, kwargs.get("synchronous", True)) or "env" in kwargs:
            return original(cmd, extra_env=extra_env, **kwargs)
        args = [str(part) for part in cmd]
        env = {**os.environ, **(extra_env or {})}  # what _exec_cmd gives the child
        sys.stdout.flush()
        sys.stderr.flush()
        return execvpe(args[0], args, env)

    return exec_cmd


def main(argv: list[str] | None = None) -> int:
    from mlflow.cli import cli

    if not job_execution_enabled():
        import mlflow.server

        mlflow.server._exec_cmd = make_exec_cmd(mlflow.server._exec_cmd)
    return cli.main(args=argv, prog_name="mlflow")


if __name__ == "__main__":
    sys.exit(main())

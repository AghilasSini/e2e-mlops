"""Idempotent bootstrap of the MLflow tracking server (run as a Helm hook Job).

Creates, if missing, the experiment and the registered model, then the two service
users with their grants:

* ``trainer``: EDIT on the experiment (create runs, log, register versions) and MANAGE
  on the registered model (set promotion tags and the champion aliases).
* ``serving``: READ on the experiment and the registered model (load the champion).

Existing users get their password reset to the value from the Secret, so rotating a
Secret and re-running the hook rotates the password.

When ``FEATURE_LOG_PASSWORD`` is set it also creates, with the PostgreSQL admin
credentials, the feature log's own role and database (Epic 3): role ``feature_logger``
(password updated on every run) owning database ``feature_log``, which ``PUBLIC`` may
not connect to. It works on releases installed before Epic 3, where the PostgreSQL
init scripts no longer run.

Environment:
    MLFLOW_TRACKING_URI                          server URL (in-cluster Service)
    MLFLOW_TRACKING_USERNAME / _PASSWORD         the admin credentials
    TRAINER_PASSWORD, SERVING_PASSWORD           service-user passwords (from Secrets)
    TRAINER_USERNAME (trainer), SERVING_USERNAME (serving)
    EXPERIMENT_NAME (wine-quality), MODEL_NAME (wine-quality)
    WAIT_SECONDS (300)                           how long to wait for the server
  feature log (optional):
    FEATURE_LOG_PASSWORD                         enables it (from its own Secret)
    FEATURE_LOG_USERNAME (feature_logger), FEATURE_LOG_DATABASE (feature_log)
    PGHOST, PGPORT (5432)                        the PostgreSQL server
    POSTGRES_ADMIN_USER, POSTGRES_ADMIN_PASSWORD superuser credentials
"""

from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping

EDIT, MANAGE, READ = "EDIT", "MANAGE", "READ"


def wait_for_server(uri: str, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            with urllib.request.urlopen(f"{uri.rstrip('/')}/health", timeout=5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"MLflow server {uri} not healthy after {seconds:.0f}s")
        time.sleep(3)


def ensure_user(auth, username: str, password: str) -> str:
    from mlflow.exceptions import MlflowException

    try:
        auth.get_user(username)
    except MlflowException:
        auth.create_user(username, password)
        return "created"
    auth.update_user_password(username, password)
    return "password updated"


def ensure_grant(auth, username: str, resource_type: str, resource_id: str, permission: str) -> str:
    """Grant, or re-grant when a grant already exists (so a changed permission applies)."""
    from mlflow.exceptions import MlflowException

    try:
        auth.grant_user_permission(username, resource_type, resource_id, permission)
        return "granted"
    except MlflowException as exc:
        if exc.error_code != "RESOURCE_ALREADY_EXISTS":
            raise
    auth.revoke_user_permission(username, resource_type, resource_id)
    auth.grant_user_permission(username, resource_type, resource_id, permission)
    return "re-granted"


def connect_admin(env: Mapping[str, str], seconds: float):
    """Autocommit connection to the server's maintenance database, retried until up."""
    import psycopg2

    deadline = time.monotonic() + seconds
    while True:
        try:
            conn = psycopg2.connect(
                host=env["PGHOST"],
                port=int(env.get("PGPORT", "5432")),
                user=env["POSTGRES_ADMIN_USER"],
                password=env["POSTGRES_ADMIN_PASSWORD"],
                dbname=env.get("POSTGRES_ADMIN_DATABASE", "postgres"),
                connect_timeout=5,
            )
            conn.autocommit = True  # CREATE DATABASE cannot run in a transaction
            return conn
        except psycopg2.OperationalError:
            if time.monotonic() > deadline:
                raise SystemExit(f"PostgreSQL {env['PGHOST']} not reachable after {seconds:.0f}s")
            time.sleep(3)


def ensure_feature_log_db(env: Mapping[str, str]) -> list[str]:
    """Role and database of the feature log, idempotently. Returns what was done."""
    from psycopg2 import sql

    role = env.get("FEATURE_LOG_USERNAME", "feature_logger")
    database = env.get("FEATURE_LOG_DATABASE", "feature_log")
    password = env["FEATURE_LOG_PASSWORD"]
    done = []
    conn = connect_admin(env, float(env.get("WAIT_SECONDS", "300")))
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            verb = "ALTER" if cur.fetchone() else "CREATE"
            cur.execute(
                sql.SQL(
                    verb + " ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE " "PASSWORD %s"
                ).format(sql.Identifier(role)),
                (password,),
            )
            done.append(f"role {role}: {'created' if verb == 'CREATE' else 'password updated'}")
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
            if cur.fetchone():
                cur.execute(
                    sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                        sql.Identifier(database), sql.Identifier(role)
                    )
                )
                done.append(f"database {database}: exists, owned by {role}")
            else:
                cur.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(database), sql.Identifier(role)
                    )
                )
                done.append(f"database {database}: created, owned by {role}")
            cur.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database))
            )
    finally:
        conn.close()
    return done


def bootstrap(env: Mapping[str, str]) -> None:
    import mlflow.server
    from mlflow import MlflowClient
    from mlflow.exceptions import MlflowException

    uri = env["MLFLOW_TRACKING_URI"]
    experiment_name = env.get("EXPERIMENT_NAME", "wine-quality")
    model_name = env.get("MODEL_NAME", "wine-quality")
    trainer = env.get("TRAINER_USERNAME", "trainer")
    serving = env.get("SERVING_USERNAME", "serving")

    wait_for_server(uri, float(env.get("WAIT_SECONDS", "300")))
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)

    experiment = client.get_experiment_by_name(experiment_name)
    experiment_id = (
        experiment.experiment_id
        if experiment is not None
        else client.create_experiment(experiment_name)
    )
    print(f"experiment {experiment_name}: id {experiment_id}")
    try:
        client.get_registered_model(model_name)
        print(f"registered model {model_name}: exists")
    except MlflowException:
        client.create_registered_model(model_name)
        print(f"registered model {model_name}: created")

    auth = mlflow.server.get_app_client("basic-auth", tracking_uri=uri)
    print(f"user {trainer}: {ensure_user(auth, trainer, env['TRAINER_PASSWORD'])}")
    print(f"user {serving}: {ensure_user(auth, serving, env['SERVING_PASSWORD'])}")

    grants = [
        (trainer, "experiment", experiment_id, EDIT),
        (trainer, "registered_model", model_name, MANAGE),
        (serving, "experiment", experiment_id, READ),
        (serving, "registered_model", model_name, READ),
    ]
    for username, resource_type, resource_id, permission in grants:
        print(
            f"grant {permission} on {resource_type} {resource_id} to {username}: "
            f"{ensure_grant(auth, username, resource_type, resource_id, permission)}"
        )


def main() -> int:
    bootstrap(os.environ)
    if os.environ.get("FEATURE_LOG_PASSWORD"):
        for line in ensure_feature_log_db(os.environ):
            print(f"feature log {line}")
    else:
        print("feature log: not configured (no FEATURE_LOG_PASSWORD), skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

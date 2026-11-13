#!/usr/bin/env bash
# Entrypoint of the mlflow-server image: MLflow tracking server + model registry with
# basic-auth, PostgreSQL metadata and proxied S3 artifacts.
#
# Required environment (from Kubernetes Secrets, never baked into the image):
#   MLFLOW_BACKEND_STORE_URI     postgresql://user:pass@host:5432/mlflow
#   MLFLOW_AUTH_DATABASE_URI     postgresql://user:pass@host:5432/mlflow_auth
#   MLFLOW_ARTIFACTS_DESTINATION s3://bucket   (S3 keys: AWS_ACCESS_KEY_ID /
#                                AWS_SECRET_ACCESS_KEY, endpoint: MLFLOW_S3_ENDPOINT_URL;
#                                these are the S3 protocol's standard variable names)
#   MLFLOW_AUTH_ADMIN_PASSWORD   initial admin password (MLFLOW_AUTH_ADMIN_USERNAME, default admin)
#   MLFLOW_FLASK_SERVER_SECRET_KEY  static CSRF key of the auth app (same on every replica)
# Optional: MLFLOW_PORT (5000), MLFLOW_WORKERS (1), MLFLOW_SERVER_ALLOWED_HOSTS.
#
# The basic-auth database URI can only be given through an ini file, so it is written
# at start-up to a private temporary file (the root filesystem stays read-only).
set -euo pipefail

: "${MLFLOW_BACKEND_STORE_URI:?MLFLOW_BACKEND_STORE_URI is required}"
: "${MLFLOW_AUTH_DATABASE_URI:?MLFLOW_AUTH_DATABASE_URI is required}"
: "${MLFLOW_ARTIFACTS_DESTINATION:?MLFLOW_ARTIFACTS_DESTINATION is required}"
: "${MLFLOW_AUTH_ADMIN_PASSWORD:?MLFLOW_AUTH_ADMIN_PASSWORD is required}"
: "${MLFLOW_FLASK_SERVER_SECRET_KEY:?MLFLOW_FLASK_SERVER_SECRET_KEY is required}"

# MLflow 3 starts a background job runner (~8 huey worker processes, ~1.4 GB) for GenAI
# jobs this project does not use. Off by default: the server then needs ~300 MB
# (one process, see below).
export MLFLOW_SERVER_ENABLE_JOB_EXECUTION="${MLFLOW_SERVER_ENABLE_JOB_EXECUTION:-false}"

umask 077
MLFLOW_AUTH_CONFIG_PATH=$(mktemp "${TMPDIR:-/tmp}/basic_auth.XXXXXX.ini")
export MLFLOW_AUTH_CONFIG_PATH
cat > "$MLFLOW_AUTH_CONFIG_PATH" <<INI
[mlflow]
default_permission = READ
database_uri = ${MLFLOW_AUTH_DATABASE_URI}
admin_username = ${MLFLOW_AUTH_ADMIN_USERNAME:-admin}
authorization_function = mlflow.server.auth:authenticate_request_basic_auth
INI

# Same `mlflow server`, but uvicorn replaces the launcher instead of running as its
# child: one Python process instead of two, about 250 MiB less (mlflow_server_exec.py).
LAUNCHER="$(dirname "$(readlink -f "$0")")/mlflow_server_exec.py"
[[ -f $LAUNCHER ]] || LAUNCHER=/opt/mlflow/mlflow_server_exec.py

exec python "$LAUNCHER" server \
  --host 0.0.0.0 \
  --port "${MLFLOW_PORT:-5000}" \
  --workers "${MLFLOW_WORKERS:-1}" \
  --app-name basic-auth \
  --serve-artifacts \
  --artifacts-destination "$MLFLOW_ARTIFACTS_DESTINATION" \
  "$@"

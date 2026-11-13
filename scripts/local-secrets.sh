#!/usr/bin/env bash
# Create the Secrets the chart references, once, with random passwords (local cluster).
#
# Usage: scripts/local-secrets.sh        Env: NAMESPACE (default e2e-mlops)
#
# Existing Secrets are kept as they are: re-running never rotates a password (the
# PostgreSQL data and the MLflow user store were initialised with the old ones).
# To rotate, delete the Secret, re-run, then restart the workloads.
# Values go to kubectl through files in a private temp dir, never on the command line.
set -euo pipefail

NAMESPACE=${NAMESPACE:-e2e-mlops}
k() { kubectl -n "$NAMESPACE" "$@"; }
log() { printf '==> %s\n' "$*"; }

# 32 URL-safe characters: valid inside database URIs, >= 12 as MLflow requires.
# Reads a fixed 96 random bytes (no `tr </dev/urandom | head`, which dies of SIGPIPE
# under `set -o pipefail`); base64 of 96 bytes leaves > 100 alphanumerics.
random() {
  local s
  s=$(head -c 96 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')
  printf '%s' "${s:0:32}"
}

WORK=$(mktemp -d)
chmod 700 "$WORK"
trap 'rm -rf "$WORK"' EXIT

# ensure_secret <name> <key>=<value>...
ensure_secret() {
  local name=$1; shift
  if k get secret "$name" >/dev/null 2>&1; then
    log "secret $name exists, kept"
    return
  fi
  local dir="$WORK/$name" args=() pair key
  mkdir -p "$dir"
  for pair in "$@"; do
    key=${pair%%=*}
    printf '%s' "${pair#*=}" > "$dir/$key"
    args+=("--from-file=$key=$dir/$key")
  done
  k create secret generic "$name" "${args[@]}" >/dev/null
  log "secret $name created"
}

kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$NAMESPACE" >/dev/null

ACCESS_KEY=$(random)
SECRET_KEY=$(random)
S3_CONFIG=$(printf '{"identities":[{"name":"mlflow","credentials":[{"accessKey":"%s","secretKey":"%s"}],"actions":["Admin","Read","Write","List","Tagging"]}]}' "$ACCESS_KEY" "$SECRET_KEY")

ensure_secret e2e-mlops-postgres username=mlflow "password=$(random)"
ensure_secret e2e-mlops-s3 "access-key=$ACCESS_KEY" "secret-key=$SECRET_KEY" \
  "s3.json=$S3_CONFIG" "filer-signing-key=$(random)"
ensure_secret e2e-mlops-mlflow-admin username=admin "password=$(random)" "flask-secret-key=$(random)"
ensure_secret e2e-mlops-mlflow-trainer username=trainer "password=$(random)"
ensure_secret e2e-mlops-mlflow-serving username=serving "password=$(random)"
# Feature-log database user (Epic 3; created by the tracking bootstrap hook).
ensure_secret e2e-mlops-feature-log username=feature_logger "password=$(random)"

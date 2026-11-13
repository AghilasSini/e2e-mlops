#!/usr/bin/env bash
# Deploy the Helm chart to a cluster.
#
# Usage: scripts/deploy.sh <local|openstack> [extra helm args...]
#
# Uses the current kubeconfig (CI: a file-type variable exported as KUBECONFIG).
# Env:
#   NAMESPACE         default e2e-mlops
#   RELEASE           default e2e-mlops
#   IMAGE_REPOSITORY  default localhost/e2e-mlops (local; localhost:5000/e2e-mlops on a
#                     k3s node, the registry scripts/local-up.sh k3s pushes to) /
#                     $CI_REGISTRY_IMAGE (openstack)
#   IMAGE_TAG         default local (local) / $CI_COMMIT_SHORT_SHA (openstack)
#   REGISTRY, REGISTRY_USER, REGISTRY_PASSWORD
#                     create the `gitlab-registry` pull secret (default: $CI_REGISTRY,
#                     $CI_DEPLOY_USER, $CI_DEPLOY_PASSWORD); required for openstack
#   HELM_TIMEOUT      default 5m
#   HELM              helm binary (default: helm)
#   CHAMPION_VERSION  pin this registry version (default: keep the version the release
#                     already pins, see scripts/serve-champion.sh; none on a first install)
#
# Platform Secrets (see values.yaml `secrets:`):
#   local      created once with random values by scripts/local-secrets.sh (called here)
#   openstack  created/updated from CI variables: POSTGRES_PASSWORD, S3_ACCESS_KEY,
#              S3_SECRET_KEY, MLFLOW_ADMIN_PASSWORD, MLFLOW_FLASK_SECRET_KEY,
#              MLFLOW_TRAINER_PASSWORD, MLFLOW_SERVING_PASSWORD, FEATURE_LOG_PASSWORD
#              (all URL-safe, MLflow passwords >= 12 characters)
#
# The first install of a release bootstraps itself: inference is installed with zero
# replicas, one training run produces a model, then the normal upgrade waits for
# readiness. Every deploy uses --atomic, so a release that does not become ready is
# rolled back and the previous one keeps serving.
#
# Inference mode (`inference.mode`, from the values file or --set): `kserve` deploys an
# InferenceService, which needs KServe (scripts/kserve-up.sh) and is checked before
# Helm runs; `deployment` keeps the plain Deployment. Helm does not wait for custom
# resources, so in kserve mode scripts/wait-inference.sh waits for the
# InferenceService and the release is rolled back if it never becomes ready.
set -euo pipefail

usage() { echo "usage: $0 <local|openstack> [extra helm args...]" >&2; exit 2; }
[[ $# -ge 1 ]] || usage
TARGET=$1
shift
EXTRA_ARGS=("$@")
[[ $TARGET == local || $TARGET == openstack ]] || usage

SCRIPTS=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHART="$SCRIPTS/../deploy/helm/e2e-mlops"
HELM=${HELM:-helm}
NAMESPACE=${NAMESPACE:-e2e-mlops}
RELEASE=${RELEASE:-e2e-mlops}
TIMEOUT=${HELM_TIMEOUT:-5m}
REGISTRY=${REGISTRY:-${CI_REGISTRY:-}}
REGISTRY_USER=${REGISTRY_USER:-${CI_DEPLOY_USER:-}}
REGISTRY_PASSWORD=${REGISTRY_PASSWORD:-${CI_DEPLOY_PASSWORD:-}}
IMAGE_ARGS=()

if [[ $TARGET == local ]]; then
  DEFAULT_REPOSITORY=localhost/e2e-mlops
  # k3s pulls our images from the local registry (no sudo import), see local-up.sh.
  if kubectl get nodes -o jsonpath='{.items[*].status.nodeInfo.kubeletVersion}' 2>/dev/null | grep -q '+k3s'; then
    DEFAULT_REPOSITORY=localhost:5000/e2e-mlops
  fi
  REPOSITORY=${IMAGE_REPOSITORY:-$DEFAULT_REPOSITORY}
  TAG=${IMAGE_TAG:-local}
  # The `local` tag is reused by every rebuild: from the local registry, pull it on each
  # pod start, so a restarted pod runs the image local-up.sh last pushed (a cheap check
  # against localhost). Minikube's `image load` replaces the node's copy instead.
  if [[ $REPOSITORY == localhost:5000/* ]]; then IMAGE_ARGS=(--set image.pullPolicy=Always); fi
else
  REPOSITORY=${IMAGE_REPOSITORY:-${CI_REGISTRY_IMAGE:-}}
  TAG=${IMAGE_TAG:-${CI_COMMIT_SHORT_SHA:-}}
  [[ -n $REPOSITORY && -n $TAG ]] || { echo "error: IMAGE_REPOSITORY and IMAGE_TAG are required for openstack" >&2; exit 1; }
  [[ -n $REGISTRY && -n $REGISTRY_USER && -n $REGISTRY_PASSWORD ]] || { echo "error: registry credentials are required for openstack" >&2; exit 1; }
fi

log() { printf '==> %s\n' "$*"; }

# Inference mode, without calling Helm: the values file, then any `inference.mode=` arg.
MODE=$(awk '
  /^inference:/ { inside = 1; next }
  inside && /^[^[:space:]#]/ { inside = 0 }
  inside && /^  mode:/ { print $2 }' "$CHART/values-$TARGET.yaml")
for arg in "${EXTRA_ARGS[@]}"; do
  if [[ $arg == *inference.mode=* ]]; then MODE=${arg##*inference.mode=}; MODE=${MODE%%,*}; fi
done
MODE=${MODE:-deployment}
if [[ $MODE == kserve ]] && ! kubectl get crd inferenceservices.serving.kserve.io >/dev/null 2>&1; then
  echo "error: inference.mode=kserve but KServe is not installed on this cluster
  (no CustomResourceDefinition inferenceservices.serving.kserve.io).
  Install it once:      scripts/kserve-up.sh $TARGET
  Or use the fallback:  scripts/deploy.sh $TARGET --set inference.mode=deployment" >&2
  exit 1
fi
log "inference mode: $MODE"

log "namespace $NAMESPACE"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

PULL_SECRET_ARGS=()
if [[ -n $REGISTRY && -n $REGISTRY_USER && -n $REGISTRY_PASSWORD ]]; then
  log "pull secret gitlab-registry for $REGISTRY"
  PULL_SECRET_ARGS=(--set "imagePullSecrets[0].name=gitlab-registry")
  kubectl -n "$NAMESPACE" create secret docker-registry gitlab-registry \
    --docker-server="$REGISTRY" --docker-username="$REGISTRY_USER" \
    --docker-password="$REGISTRY_PASSWORD" --dry-run=client -o yaml \
    | kubectl apply -f - >/dev/null
fi

# apply_secret <name> <key>=<ENV_VAR>... : create or update from CI variables (via files)
apply_secret() {
  local name=$1; shift
  local dir pair key var args=()
  dir=$(mktemp -d); chmod 700 "$dir"
  for pair in "$@"; do
    key=${pair%%=*}; var=${pair#*=}
    [[ -n ${!var:-} ]] || { rm -rf "$dir"; echo "error: CI variable $var is required" >&2; exit 1; }
    printf '%s' "${!var}" > "$dir/$key"
    args+=("--from-file=$key=$dir/$key")
  done
  kubectl -n "$NAMESPACE" create secret generic "$name" "${args[@]}" --dry-run=client -o yaml \
    | kubectl apply -f - >/dev/null
  rm -rf "$dir"
  log "secret $name applied"
}

if [[ $TARGET == local ]]; then
  NAMESPACE=$NAMESPACE "$SCRIPTS/local-secrets.sh"
else
  POSTGRES_USER=${POSTGRES_USER:-mlflow} MLFLOW_ADMIN_USER=${MLFLOW_ADMIN_USER:-admin}
  MLFLOW_TRAINER_USER=${MLFLOW_TRAINER_USER:-trainer} MLFLOW_SERVING_USER=${MLFLOW_SERVING_USER:-serving}
  FEATURE_LOG_USER=${FEATURE_LOG_USER:-feature_logger}
  export POSTGRES_USER MLFLOW_ADMIN_USER MLFLOW_TRAINER_USER MLFLOW_SERVING_USER FEATURE_LOG_USER
  apply_secret e2e-mlops-postgres username=POSTGRES_USER password=POSTGRES_PASSWORD
  apply_secret e2e-mlops-s3 access-key=S3_ACCESS_KEY secret-key=S3_SECRET_KEY
  apply_secret e2e-mlops-mlflow-admin username=MLFLOW_ADMIN_USER password=MLFLOW_ADMIN_PASSWORD \
    flask-secret-key=MLFLOW_FLASK_SECRET_KEY
  apply_secret e2e-mlops-mlflow-trainer username=MLFLOW_TRAINER_USER password=MLFLOW_TRAINER_PASSWORD
  apply_secret e2e-mlops-mlflow-serving username=MLFLOW_SERVING_USER password=MLFLOW_SERVING_PASSWORD
  apply_secret e2e-mlops-feature-log username=FEATURE_LOG_USER password=FEATURE_LOG_PASSWORD
fi

# The champion version the release pins (set by scripts/serve-champion.sh), read just
# before each Helm call so a code deploy never undoes a rollout; CHAMPION_VERSION wins.
pinned_version() {
  local values
  [[ -n ${CHAMPION_VERSION:-} ]] && { printf '%s' "$CHAMPION_VERSION"; return; }
  values=$("$HELM" get values "$RELEASE" -n "$NAMESPACE" -o json 2>/dev/null) || return 0
  local pinned='"championVersion": *"?([0-9]+)'
  if [[ $values =~ $pinned ]]; then printf '%s' "${BASH_REMATCH[1]}"; fi
}

helm_deploy() {
  local version pin_args=()
  version=$(pinned_version)
  if [[ -n $version ]]; then
    log "keeping champion version $version"
    pin_args=(--set "inference.championVersion=$version")
  fi
  "$HELM" upgrade --install "$RELEASE" "$CHART" \
    --namespace "$NAMESPACE" \
    -f "$CHART/values-$TARGET.yaml" \
    --set image.repository="$REPOSITORY" \
    --set-string image.tag="$TAG" \
    "${IMAGE_ARGS[@]}" "${PULL_SECRET_ARGS[@]}" "${pin_args[@]}" "${EXTRA_ARGS[@]}" \
    --atomic --wait --timeout "$TIMEOUT" "$@"
}

needs_bootstrap() {
  # A release that does not exist yet, or one left in the bootstrap state.
  ! "$HELM" status "$RELEASE" -n "$NAMESPACE" >/dev/null 2>&1 \
    || "$HELM" get values "$RELEASE" -n "$NAMESPACE" -o yaml | grep -q 'replicas: 0'
}

if needs_bootstrap; then
  log "bootstrap: install without inference replicas, then train a first model"
  helm_deploy --set inference.replicas=0
  NAMESPACE=$NAMESPACE RELEASE=$RELEASE "$SCRIPTS/run-training.sh" "$TARGET"
fi

log "deploy $REPOSITORY/{trainer,inference}:$TAG to $TARGET ($NAMESPACE/$RELEASE)"
helm_deploy
if ! NAMESPACE=$NAMESPACE RELEASE=$RELEASE "$SCRIPTS/wait-inference.sh"; then
  "$HELM" rollback "$RELEASE" -n "$NAMESPACE" --wait --timeout "$TIMEOUT" || true
  echo "error: the InferenceService did not become ready; release rolled back" >&2
  exit 1
fi
kubectl -n "$NAMESPACE" get pods -l app.kubernetes.io/instance="$RELEASE"

#!/usr/bin/env bash
# Install the serving add-ons once per cluster: cert-manager, then KServe (CRDs and
# controller), then size the logger sidecar that KServe injects into predictor pods.
#
# Usage: scripts/kserve-up.sh <local|openstack>
#
#   local      KServe Standard mode (RawDeployment before 0.15): no Knative, no mesh.
#   openstack  KServe Knative mode (Serverless before 0.15). Needs Knative Serving and
#              Kourier, installed by the cluster owner; checked first, stops otherwise.
#
# Versions and chart checksums: deploy/kserve/versions.env. Values (resources, mode):
# deploy/kserve/values-{cert-manager,kserve}-<target>.yaml. Re-running is safe: every
# step is an upgrade or an idempotent patch.
# Env:
#   HELM            helm binary (default: helm)
#   CERT_MANAGER    set to `skip` when the cluster already runs cert-manager
#   ADDON_TIMEOUT   Helm wait timeout (default 5m)
set -euo pipefail

usage() { echo "usage: $0 <local|openstack>" >&2; exit 2; }
[[ $# -eq 1 ]] || usage
TARGET=$1
[[ $TARGET == local || $TARGET == openstack ]] || usage

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ADDONS="$REPO_ROOT/deploy/kserve"
HELM=${HELM:-helm}
TIMEOUT=${ADDON_TIMEOUT:-5m}
# shellcheck source=SCRIPTDIR/../deploy/kserve/versions.env
source "$ADDONS/versions.env"

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v "$HELM" >/dev/null || die "helm not found (https://helm.sh/docs/intro/install/)"
command -v kubectl >/dev/null || die "kubectl not found"
command -v sha256sum >/dev/null || die "sha256sum not found"
kubectl cluster-info >/dev/null 2>&1 || die "cluster not reachable with the current kubeconfig"
log "cluster: $(kubectl config current-context)"

# 1. OpenStack: Knative Serving + Kourier must already be there.
if [[ $TARGET == openstack ]]; then
  if ! kubectl get crd services.serving.knative.dev >/dev/null 2>&1 \
    || ! kubectl -n kourier-system get service kourier-internal >/dev/null 2>&1; then
    die "Knative Serving with Kourier is required for KServe's Knative (serverless) mode.
  Install them first (cluster owner):
    https://knative.dev/docs/install/yaml-install/serving/install-serving-with-yaml/
    (serving-crds.yaml, serving-core.yaml, then net-kourier and
     kubectl patch configmap/config-network -n knative-serving --type merge \\
       -p '{\"data\":{\"ingress-class\":\"kourier.ingress.networking.knative.dev\"}}')
  Or deploy without KServe: scripts/deploy.sh openstack --set inference.mode=deployment"
  fi
  log "Knative Serving and Kourier found"
fi

# 2. Download the pinned charts and check them.
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fetch_chart() { # <url> <sha256> -> path
  local url=$1 sha=$2 out
  out="$WORK/$(basename "$url")"
  curl -fsSL --retry 3 -o "$out" "$url" || die "download failed: $url"
  echo "$sha  $out" | sha256sum -c --quiet - >&2 || die "checksum mismatch: $url"
  printf '%s' "$out"
}

helm_install() { # <release> <namespace> <chart> [args...]
  local release=$1 ns=$2 chart=$3; shift 3
  log "helm upgrade --install $release ($ns)"
  "$HELM" upgrade --install "$release" "$chart" --namespace "$ns" --create-namespace \
    --wait --timeout "$TIMEOUT" "$@"
}

# 3. cert-manager (KServe's webhook certificate).
if [[ ${CERT_MANAGER:-} == skip ]]; then
  log "cert-manager: skipped (CERT_MANAGER=skip)"
else
  chart=$(fetch_chart "$CERT_MANAGER_CHART_URL" "$CERT_MANAGER_CHART_SHA256")
  helm_install cert-manager cert-manager "$chart" -f "$ADDONS/values-cert-manager-$TARGET.yaml"
fi

# 4. KServe: CRDs, then the controller.
chart=$(fetch_chart "$KSERVE_CRD_CHART_URL" "$KSERVE_CRD_CHART_SHA256")
helm_install kserve-crd kserve "$chart"
chart=$(fetch_chart "$KSERVE_RESOURCES_CHART_URL" "$KSERVE_RESOURCES_CHART_SHA256")
helm_install kserve kserve "$chart" -f "$ADDONS/values-kserve-$TARGET.yaml"

# 5. Logger sidecar resources. KServe's chart hardcodes them (100m/100Mi requests,
# 1 CPU/1Gi limits). The injected container is sized from the `agent` key; the
# `logger` key's resources are validated too, so both are set.
sidecar() { # <field> : read loggerSidecar.<field> from the KServe values file
  awk -v key="$1" '
    /^loggerSidecar:/ { inside = 1; next }
    inside && /^[^[:space:]#]/ { inside = 0 }
    inside && $1 == key ":" { print $2 }' "$ADDONS/values-kserve-$TARGET.yaml"
}
CPU_REQ=$(sidecar cpuRequest); MEM_REQ=$(sidecar memoryRequest)
CPU_LIM=$(sidecar cpuLimit); MEM_LIM=$(sidecar memoryLimit)
[[ -n $CPU_REQ && -n $MEM_REQ && -n $CPU_LIM && -n $MEM_LIM ]] \
  || die "loggerSidecar resources missing in values-kserve-$TARGET.yaml"
AGENT_IMAGE="docker.io/kserve/agent:$KSERVE_VERSION"
RESOURCES=$(printf '"memoryRequest": "%s", "memoryLimit": "%s", "cpuRequest": "%s", "cpuLimit": "%s"' \
  "$MEM_REQ" "$MEM_LIM" "$CPU_REQ" "$CPU_LIM")
AGENT_JSON="{\"image\": \"$AGENT_IMAGE\", $RESOURCES}"
LOGGER_JSON="{\"image\": \"$AGENT_IMAGE\", $RESOURCES, \"defaultUrl\": \"http://default-broker\"}"
PATCH=$(printf '{"data": {"agent": "%s", "logger": "%s"}}' "${AGENT_JSON//\"/\\\"}" "${LOGGER_JSON//\"/\\\"}")
kubectl -n kserve patch configmap inferenceservice-config --type merge -p "$PATCH" >/dev/null
log "logger sidecar: requests $CPU_REQ/$MEM_REQ, limits $CPU_LIM/$MEM_LIM (inferenceservice-config keys agent, logger)"

# 6. Ready?
kubectl -n kserve rollout status deployment/kserve-controller-manager --timeout="$TIMEOUT"
kubectl get crd inferenceservices.serving.kserve.io >/dev/null
log "KServe $KSERVE_VERSION ready ($TARGET). Pods:"
for ns in cert-manager kserve; do kubectl get pods -n "$ns" 2>/dev/null || true; done

#!/usr/bin/env bash
# Wait until the release's InferenceService serves its current spec.
#
# Usage: scripts/wait-inference.sh        Env: NAMESPACE, RELEASE, ROLLOUT_TIMEOUT (s, 300)
#
# `helm upgrade --wait` does not wait for custom resources, so after an upgrade in
# KServe mode this waits until KServe has observed the new generation and reports
# Ready. In Standard mode it also waits for the predictor Deployment's rollout, because
# the old pods keep the InferenceService Ready while the new ones start.
# Exits 0 at once when the release has no InferenceService (Deployment mode, where
# Helm's own --wait already covered readiness). Exits 1 on timeout or failure.
set -euo pipefail

NAMESPACE=${NAMESPACE:-e2e-mlops}
RELEASE=${RELEASE:-e2e-mlops}
TIMEOUT=${ROLLOUT_TIMEOUT:-300}
k() { kubectl -n "$NAMESPACE" "$@"; }
log() { printf '==> %s\n' "$*"; }

kubectl get crd inferenceservices.serving.kserve.io >/dev/null 2>&1 || exit 0
ISVC=$(k get inferenceservices -l "app.kubernetes.io/instance=$RELEASE" -o name | head -n1)
[[ -n $ISVC ]] || exit 0

deadline=$((SECONDS + TIMEOUT))
log "waiting for $ISVC (up to ${TIMEOUT}s)"
while :; do
  state=$(k get "$ISVC" -o jsonpath='{.metadata.generation} {.status.observedGeneration} {.status.conditions[?(@.type=="Ready")].status}')
  read -r generation observed ready <<<"$state"
  [[ $generation == "${observed:-}" && ${ready:-} == True ]] && break
  if (( SECONDS >= deadline )); then
    echo "error: $ISVC not ready after ${TIMEOUT}s (generation $generation, observed ${observed:-none}, Ready=${ready:-unknown})" >&2
    k get "$ISVC" -o jsonpath='{range .status.conditions[*]}  {.type}={.status} {.message}{"\n"}{end}' >&2 || true
    exit 1
  fi
  sleep 3
done

mode=$(k get "$ISVC" -o jsonpath='{.status.deploymentMode}')
if [[ $mode == Standard || $mode == RawDeployment ]]; then
  remaining=$((deadline - SECONDS)); (( remaining > 0 )) || remaining=1
  k rollout status "deployment/${ISVC#*/}-predictor" --timeout="${remaining}s"
fi
log "${ISVC#*/} ready"

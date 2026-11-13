#!/usr/bin/env bash
# Champion rollout: pin the serving release to one exact registry version.
#
# Usage: scripts/serve-champion.sh <local|openstack> (--version N | --from-job JOB)
#
#   --version N     pin version N (hand promotion, or after `registry_manager rollback`,
#                   which prints the restored version)
#   --from-job JOB  read the version from the last log line of training Job JOB
#                   (`train-and-promote` ends with `python -m models.serving_sync resolve`)
#
# Compares N with the release's `inference.championVersion`. When they are equal it
# prints "already served" and changes nothing. Otherwise it runs
# `helm upgrade --reuse-values --set inference.championVersion=N --atomic --wait`, so the
# predictor loads `models:/<name>/N`, then waits for the InferenceService
# (scripts/wait-inference.sh) and rolls the release back if it never becomes ready.
# Revert a rollout with `helm rollback <release>`.
# Exit status: 0 served (or already served), 1 no champion / not ready, 2 usage.
# Env: NAMESPACE (default e2e-mlops), RELEASE (default e2e-mlops), HELM (default helm),
#      HELM_TIMEOUT (default 5m), ROLLOUT_TIMEOUT seconds (default 300)
set -euo pipefail

usage() { echo "usage: $0 <local|openstack> (--version N | --from-job JOB)" >&2; exit 2; }
[[ $# -eq 3 ]] || usage
TARGET=$1
[[ $TARGET == local || $TARGET == openstack ]] || usage

SCRIPTS=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHART="$SCRIPTS/../deploy/helm/e2e-mlops"
HELM=${HELM:-helm}
NAMESPACE=${NAMESPACE:-e2e-mlops}
RELEASE=${RELEASE:-e2e-mlops}
TIMEOUT=${HELM_TIMEOUT:-5m}
export NAMESPACE RELEASE
log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# 1. Which version?
case $2 in
  --version) VERSION=$3 ;;
  --from-job)
    # The champion line is the Job's last log line, e.g.
    # {"champion_version": "5", "model_name": "wine-quality", "resolved_at": "..."}
    # (the promotion decision also has a champion_version key, but no resolved_at).
    line=$(kubectl -n "$NAMESPACE" logs "job/$3" --tail=1)
    VERSION=""
    champion='"champion_version": *"([0-9]+)"'
    if [[ $line == *'"resolved_at"'* && $line =~ $champion ]]; then
      VERSION=${BASH_REMATCH[1]}
    fi
    [[ -n $VERSION ]] || die "no champion in the last log line of job/$3: nothing to serve
  (no version holds the champion alias yet; the running service is unchanged)" ;;
  *) usage ;;
esac
[[ $VERSION =~ ^[1-9][0-9]*$ ]] || die "invalid version '$VERSION' (expected a registry version number)"

# 2. Already served?
values=$("$HELM" get values "$RELEASE" -n "$NAMESPACE" -o json)
current=""
pinned='"championVersion": *"?([0-9]+)'
if [[ $values =~ $pinned ]]; then current=${BASH_REMATCH[1]}; fi
if [[ $current == "$VERSION" ]]; then
  log "version $VERSION already served by $RELEASE: nothing to do"
  exit 0
fi

# 3. Roll out.
log "rolling out champion version $VERSION (was ${current:-the alias}) to $RELEASE ($TARGET)"
"$HELM" upgrade "$RELEASE" "$CHART" --namespace "$NAMESPACE" --reuse-values \
  --set "inference.championVersion=$VERSION" --atomic --wait --timeout "$TIMEOUT" \
  || die "release did not become ready; Helm rolled it back to the previous version"
if ! "$SCRIPTS/wait-inference.sh"; then
  "$HELM" rollback "$RELEASE" -n "$NAMESPACE" --wait --timeout "$TIMEOUT" || true
  die "version $VERSION did not become ready; release rolled back to ${current:-the previous version}"
fi
log "serving version $VERSION"

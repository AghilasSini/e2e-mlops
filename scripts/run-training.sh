#!/usr/bin/env bash
# Start one training run from the chart's (suspended) training CronJob, wait for it,
# then roll out the champion it resolved (scripts/serve-champion.sh --from-job): the
# release is pinned to that exact version, or left alone when it is already served.
#
# Usage: scripts/run-training.sh <local|openstack>
# Env:   NAMESPACE (default e2e-mlops), RELEASE (default e2e-mlops),
#        TRAIN_TIMEOUT seconds (default 900); serve-champion.sh's HELM, HELM_TIMEOUT,
#        ROLLOUT_TIMEOUT
# Exits non-zero when training fails, or when there is no champion to serve.
set -euo pipefail

usage() { echo "usage: $0 <local|openstack>" >&2; exit 2; }
[[ $# -eq 1 ]] || usage
[[ $1 == local || $1 == openstack ]] || usage
TARGET=$1
SCRIPTS=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

NAMESPACE=${NAMESPACE:-e2e-mlops}
RELEASE=${RELEASE:-e2e-mlops}
TIMEOUT=${TRAIN_TIMEOUT:-900}
SELECTOR="app.kubernetes.io/instance=$RELEASE"
k() { kubectl -n "$NAMESPACE" "$@"; }
log() { printf '==> %s\n' "$*"; }

CRONJOB=$(k get cronjob -l "$SELECTOR,app.kubernetes.io/component=training" -o name | head -n1)
[[ -n $CRONJOB ]] || { echo "error: no training CronJob for release $RELEASE in $NAMESPACE" >&2; exit 1; }

JOB="${CRONJOB#cronjob.batch/}-$(date -u +%Y%m%d%H%M%S)"
log "starting job/$JOB from $CRONJOB"
k create job "$JOB" --from="$CRONJOB" >/dev/null

deadline=$((SECONDS + TIMEOUT))
status=""
while (( SECONDS < deadline )); do
  succeeded=$(k get job "$JOB" -o jsonpath='{.status.succeeded}')
  failed=$(k get job "$JOB" -o jsonpath='{.status.conditions[?(@.type=="Failed")].status}')
  if [[ $succeeded == 1 ]]; then status=ok; break; fi
  if [[ $failed == True ]]; then status=failed; break; fi
  sleep 5
done

k logs "job/$JOB" --tail=20 || true
case $status in
  ok) log "training job $JOB succeeded" ;;
  failed) echo "error: training job $JOB failed" >&2; exit 1 ;;
  *) echo "error: training job $JOB did not finish within ${TIMEOUT}s" >&2; exit 1 ;;
esac

NAMESPACE=$NAMESPACE RELEASE=$RELEASE "$SCRIPTS/serve-champion.sh" "$TARGET" --from-job "$JOB"

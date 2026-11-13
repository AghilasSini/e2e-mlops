#!/usr/bin/env bash
# Prepare a local single-node cluster (k3s or Minikube) on a modest machine:
# check it is reachable, build the trainer, inference, mlflow-server and feature-logger
# images with podman or docker, and make them available to the cluster (tag `local`):
#
#   k3s       push them to a local registry on 127.0.0.1:5000 (a `registry` container
#             run by your own podman/docker, no sudo). k3s pulls from it over plain
#             HTTP, which containerd allows for localhost, and pulls the third-party
#             images (PostgreSQL, SeaweedFS, KServe, cert-manager) from their public
#             registries itself. scripts/deploy.sh uses this registry on a k3s node.
#   minikube  pull the pinned third-party images too and load everything into the
#             node (`minikube image load`), so the node needs no registry access.
#
# Install the serving add-ons once with scripts/kserve-up.sh local.
#
# Usage: scripts/local-up.sh <k3s|minikube> [--start]
#   --start   start Minikube with the budgeted size if it is not running
#             (k3s is a system service: `sudo systemctl start k3s`).
# Env:   IMAGE_REPOSITORY (default localhost:5000/e2e-mlops on k3s,
#        localhost/e2e-mlops on Minikube), IMAGE_TAG (default local)
set -euo pipefail

usage() { echo "usage: $0 <k3s|minikube> [--start]" >&2; exit 2; }
[[ $# -ge 1 ]] || usage
CLUSTER=$1
START=${2:-}
[[ $CLUSTER == k3s || $CLUSTER == minikube ]] || usage

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REGISTRY_NAME=e2e-mlops-registry
REGISTRY_IMAGE=docker.io/library/registry:2.8.3
REGISTRY_ADDR=localhost:5000
if [[ $CLUSTER == k3s ]]; then
  REPOSITORY=${IMAGE_REPOSITORY:-$REGISTRY_ADDR/e2e-mlops}
else
  REPOSITORY=${IMAGE_REPOSITORY:-localhost/e2e-mlops}
fi
TAG=${IMAGE_TAG:-local}
TARGETS=(trainer inference mlflow-server feature-logger)
# Third-party platform images, read from the chart so versions stay in one place.
VALUES="$REPO_ROOT/deploy/helm/e2e-mlops/values.yaml"
mapfile -t PLATFORM_IMAGES < <(grep -E '^\s+image: docker\.io/' "$VALUES" | awk '{print $2}')
# Serving add-ons (KServe controller and agent, cert-manager), pinned in deploy/kserve/.
mapfile -t ADDON_IMAGES < <(grep -Ev '^\s*(#|$)' "$REPO_ROOT/deploy/kserve/images.txt")

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# 1. Cluster reachable?
if [[ $CLUSTER == minikube ]]; then
  command -v minikube >/dev/null || die "minikube not found"
  if ! minikube status >/dev/null 2>&1; then
    [[ $START == --start ]] || die "minikube is not running (re-run with --start)"
    log "starting minikube (2 CPUs, 4 GB RAM, 20 GB disk)"
    minikube start --cpus 2 --memory 4096 --disk-size 20g
  fi
else
  command -v k3s >/dev/null || die "k3s not found (https://k3s.io)"
fi
command -v kubectl >/dev/null || die "kubectl not found"
kubectl cluster-info >/dev/null 2>&1 || die "cluster not reachable with the current kubeconfig.
  k3s: mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config && sudo chown \"\$USER\" ~/.kube/config"
log "cluster reachable: $(kubectl config current-context)"

if command -v podman >/dev/null; then BUILDER=podman
elif command -v docker >/dev/null; then BUILDER=docker
else die "neither podman nor docker found"; fi
GIT_SHA=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)

# 2. k3s: local registry, bound to the loopback interface only.
if [[ $CLUSTER == k3s ]]; then
  if [[ $("$BUILDER" inspect -f '{{.State.Running}}' "$REGISTRY_NAME" 2>/dev/null) != true ]]; then
    if "$BUILDER" inspect "$REGISTRY_NAME" >/dev/null 2>&1; then
      log "starting the local registry $REGISTRY_NAME"
      "$BUILDER" start "$REGISTRY_NAME" >/dev/null
    else
      log "creating the local registry $REGISTRY_NAME on $REGISTRY_ADDR"
      "$BUILDER" run -d --name "$REGISTRY_NAME" --restart=always \
        -p 127.0.0.1:5000:5000 -v "$REGISTRY_NAME:/var/lib/registry" "$REGISTRY_IMAGE" >/dev/null
    fi
  fi
  for _ in $(seq 1 15); do
    curl -fsS "http://$REGISTRY_ADDR/v2/" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS "http://$REGISTRY_ADDR/v2/" >/dev/null || die "local registry not answering on $REGISTRY_ADDR"
fi

# 3. Build our images; k3s: push them, Minikube: save them for loading.
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
for target in "${TARGETS[@]}"; do
  image="$REPOSITORY/$target:$TAG"
  log "building $image with $BUILDER"
  "$BUILDER" build --target "$target" \
    --build-arg GIT_SHA="$GIT_SHA" --build-arg IMAGE_TAG="$TAG" \
    -t "$image" "$REPO_ROOT"
  if [[ $CLUSTER == k3s ]]; then
    log "pushing $image"
    if [[ $BUILDER == podman ]]; then
      podman push --tls-verify=false "$image"
    else
      docker push "$image"   # docker treats localhost registries as insecure
    fi
  else
    "$BUILDER" save -o "$WORK/$target.tar" "$image"
  fi
done

if [[ $CLUSTER == k3s ]]; then
  log "done. Next: scripts/kserve-up.sh local (once per cluster), then scripts/deploy.sh local"
  exit 0
fi

# 4. Minikube: third-party images too, then load everything into the node.
for image in "${PLATFORM_IMAGES[@]}" "${ADDON_IMAGES[@]}"; do
  log "pulling $image"
  "$BUILDER" pull "$image"
  "$BUILDER" save -o "$WORK/$(basename "${image%%:*}").tar" "$image"
done
for archive in "$WORK"/*.tar; do
  log "loading $(basename "$archive" .tar) into $CLUSTER"
  minikube image load "$archive"
done

log "done. Next: scripts/kserve-up.sh local (once per cluster), then scripts/deploy.sh local"

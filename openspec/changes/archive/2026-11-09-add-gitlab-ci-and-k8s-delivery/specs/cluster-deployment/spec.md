# Spec Delta

## Purpose

Describes how the system is packaged and run on Kubernetes, identically on a local single-node cluster (k3s or Minikube) on a modest personal machine and on OpenStack-managed clusters, with environment differences confined to configuration values.

## ADDED Requirements

### Requirement: Single chart, per-environment values
All components SHALL be deployable from one Helm chart. Differences between the local profile (k3s or Minikube) and OpenStack (image source, storage class, ingress, replicas, resources) SHALL be expressed only in per-environment values files. The same local values SHALL work on both k3s and Minikube.

#### Scenario: Local install on k3s
- **WHEN** an operator runs the documented local deploy script against a fresh single-node k3s cluster
- **THEN** the inference service becomes ready and answers a prediction request through port-forward

#### Scenario: Local install on Minikube
- **WHEN** the same script and values are used against a Minikube cluster started with 2 CPUs and 4 GB
- **THEN** the inference service becomes ready and answers a prediction request

#### Scenario: Same templates
- **WHEN** the chart is rendered with the local and OpenStack values files
- **THEN** both renderings come from the same templates and pass schema validation

### Requirement: Local resource budget
With the local values and every component enabled, the chart's workloads SHALL stay within these totals:
- CPU requests ≤ 1 CPU;
- memory requests ≤ 2 GiB;
- memory limits ≤ 3 GiB;
- persistent volumes ≤ 5 GiB;
- one replica per workload.

This lets the stack run with its cluster on a machine with 4 CPU cores and 8 GB RAM.

#### Scenario: Budget check
- **WHEN** the chart is rendered with the local values and all components enabled
- **THEN** the summed requests, limits and volume sizes are within the budget, and the check fails naming the totals otherwise

### Requirement: Optional components
Each component SHALL be switchable off through values, so that a subset can run on a smaller machine. Switching a component off SHALL remove its workloads, volumes and Services from the rendered manifests.

#### Scenario: Disable a component
- **WHEN** the chart is rendered with `training.enabled=false`
- **THEN** no training Job is rendered and the rest of the release still validates

### Requirement: Local images without a registry
The local profile SHALL be able to run images built on the developer's machine and loaded into the local cluster, with no container registry and no network access to GitLab.

#### Scenario: Offline local deploy
- **WHEN** the operator builds and loads the images with the local setup script, then deploys with the local values
- **THEN** all pods start from the loaded images without pulling from any registry

### Requirement: Training as a Kubernetes Job
The chart SHALL be able to run training as a Kubernetes Job using the `trainer` image. The Job SHALL write its outputs to storage that the inference service can read.

#### Scenario: Train then serve
- **WHEN** the training Job completes successfully and the inference Deployment is restarted
- **THEN** `/health/ready` returns 200 and `model_version` identifies the new model

### Requirement: Hardened workloads
Every workload SHALL run as a non-root user with a read-only root filesystem where the workload permits. Each SHALL declare CPU and memory requests and limits. The inference service SHALL declare liveness and readiness probes bound to its health endpoints.

#### Scenario: Pod security
- **WHEN** the rendered manifests are inspected
- **THEN** every container sets `runAsNonRoot: true` and declares resource requests and limits

### Requirement: Secrets from Kubernetes Secrets
Credentials needed at runtime (registry pull secret, later tracking and storage credentials) SHALL be supplied through Kubernetes Secrets referenced by name. They SHALL NOT be baked into images, chart values committed to the repository, or plain environment literals.

#### Scenario: Pull secret
- **WHEN** the chart is deployed to a cluster pulling from a private GitLab registry
- **THEN** pods pull their images using an `imagePullSecret` created by the deploy script from CI variables

### Requirement: Rollback
A failed deployment SHALL be reversible to the previous release with a single command, and the deploy job SHALL fail if the new release doesn't become ready within the timeout.

#### Scenario: Bad release
- **WHEN** a release's pods fail their readiness probe until the deploy timeout
- **THEN** the deploy job fails and the previous release keeps serving

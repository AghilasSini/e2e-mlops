# cluster-deployment Specification

## Purpose

Describes how the system is packaged and run on Kubernetes, identically on a local single-node cluster (k3s or Minikube) on a modest personal machine and on OpenStack-managed clusters, with environment differences confined to configuration values.

## Requirements

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

### Requirement: Local images without a remote registry
The local profile SHALL run the images built on the developer's machine without pushing them to a remote registry, without registry credentials and without access to GitLab. They SHALL reach the cluster either loaded into the node or from a local registry listening only on the loopback interface, with no root privileges beyond the cluster's own installation.

#### Scenario: Local deploy from local images
- **WHEN** the operator runs the local setup script, then deploys with the local values
- **THEN** the project's pods start from the images built on this machine, with no registry credentials and no access to GitLab

#### Scenario: Rebuilt image after a restart
- **WHEN** the operator rebuilds an image with the local setup script and the pod running it restarts
- **THEN** the pod runs the rebuilt image

#### Scenario: Offline local deploy
- **WHEN** the operator builds and loads the images with the local setup script on Minikube, then deploys with the local values
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

### Requirement: Tracking platform in the chart
The Helm chart SHALL deploy the tracking server, its PostgreSQL database and an S3-compatible object store, each toggleable. On OpenStack the chart SHALL be able to use an external S3-compatible endpoint instead of the in-chart object store, configured through values only.

#### Scenario: External object store
- **WHEN** the chart is rendered with the OpenStack values pointing to an external S3 endpoint
- **THEN** no object-store workload is rendered and the tracking server is configured with that endpoint

### Requirement: Tracking platform within the local budget
With the local values, the tracking server, database and object store SHALL each run as a single replica. With them enabled alongside the Epic 1 workloads, the rendered release SHALL still meet the local resource budget.

#### Scenario: Full local stack
- **WHEN** the chart is rendered with the local values and tracking, database, object store, inference and training all enabled
- **THEN** the budget check passes

### Requirement: Registry-based model hand-off
The training Job SHALL publish models only through the tracking server and registry. The inference service SHALL obtain models only from the registry. No volume SHALL be shared between training and inference.

#### Scenario: Train, promote, serve
- **WHEN** the training Job registers a version, promotion makes it champion, and the inference Deployment is restarted
- **THEN** the inference service serves that version, and the rendered manifests contain no volume shared by both workloads

### Requirement: Inference workload kind by values
The chart SHALL render inference either as a KServe InferenceService or as a plain Deployment, selected by one value, with the same image, configuration and Secrets in both cases. When the KServe mode is selected and KServe is not installed, the deploy script SHALL stop before deploying, with an error that explains how to install it or switch modes.

#### Scenario: Deployment fallback
- **WHEN** the chart is rendered with the Deployment mode
- **THEN** no InferenceService is rendered and the Epic 2 inference Deployment is rendered instead

#### Scenario: KServe missing
- **WHEN** `scripts/deploy.sh local` runs in KServe mode on a cluster without the InferenceService CRD
- **THEN** it exits non-zero before calling Helm, and the message names `scripts/kserve-up.sh` and the Deployment fallback

### Requirement: Feature logger within the local budget
With the local values and every component enabled, including the feature logger and the predictor's logger sidecar, the release SHALL still meet the local resource budget.

#### Scenario: Full local stack with logging
- **WHEN** the budget check renders the local values in KServe mode, counting the logger sidecar's configured resources
- **THEN** the totals are within the budget

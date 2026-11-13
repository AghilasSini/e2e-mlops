# Spec Delta

## ADDED Requirements

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

## RENAMED Requirements

- FROM: `### Requirement: Local images without a registry`
- TO: `### Requirement: Local images without a remote registry`

## MODIFIED Requirements

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

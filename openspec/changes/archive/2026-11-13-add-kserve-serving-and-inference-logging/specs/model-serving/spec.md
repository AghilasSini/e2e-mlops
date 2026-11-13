# Spec Delta

## Purpose

Serves the registry champion through a KServe InferenceService, with deployment modes per environment, autoscaling, and an explicit rollout when the champion changes.

## ADDED Requirements

### Requirement: Champion served by a KServe InferenceService
When the KServe serving mode is selected, the system SHALL serve predictions through a KServe `InferenceService` whose predictor runs the project's inference image. The predictor SHALL expose the existing prediction API and the Open Inference Protocol v2 endpoints.

#### Scenario: Serving through KServe
- **WHEN** the chart is deployed with the KServe mode on a cluster where KServe is installed
- **THEN** an InferenceService named `wine-quality` becomes Ready and answers both `POST /predict` and `POST /v2/models/wine-quality/infer`

### Requirement: Deployment mode per environment
The local profile SHALL run the InferenceService in KServe RawDeployment mode, which needs no Knative or service mesh. The OpenStack profile SHALL run it in Serverless mode. Since KServe 0.15 these modes are named `Standard` and `Knative`; the annotation SHALL use the current names. The mode SHALL be selected through values only.

#### Scenario: Local mode
- **WHEN** the chart is rendered with the local values
- **THEN** the InferenceService carries the `Standard` (RawDeployment) mode annotation and no Knative resources are required

#### Scenario: OpenStack mode
- **WHEN** the chart is rendered with the OpenStack values
- **THEN** the InferenceService carries the `Knative` (Serverless) mode annotation

### Requirement: Autoscaling and scale-to-zero
The predictor SHALL declare minimum and maximum replica counts and an autoscaling target. On OpenStack, the minimum SHALL be 0, so an idle service scales to zero and starts again on the next request. Locally, the minimum and maximum SHALL both be 1.

#### Scenario: Idle service on OpenStack
- **WHEN** the OpenStack service receives no requests for longer than the scale-down window
- **THEN** it runs zero predictor pods, and the next request is answered after a cold start

#### Scenario: Local replicas
- **WHEN** the chart is rendered with the local values
- **THEN** the predictor's minimum and maximum replicas are both 1

### Requirement: Champion rollout
A champion rollout command SHALL resolve the registry `champion` alias to its exact version and update the serving release so that the predictor loads that exact version (`models:/wine-quality/<N>`). The command SHALL wait until the service is ready and SHALL fail with a non-zero exit status if it does not become ready.

#### Scenario: New champion rolled out
- **WHEN** version 5 becomes champion and the champion rollout command runs
- **THEN** the serving release pins version 5 and predictions carry `model_version` `wine-quality/5`

#### Scenario: No champion
- **WHEN** the rollout command runs while no version holds the `champion` alias
- **THEN** it exits non-zero with an error, and the running service is unchanged

#### Scenario: Champion already served
- **WHEN** the rollout command runs and the served version already equals the champion
- **THEN** it reports that nothing changed and exits 0 without restarting pods

### Requirement: Pinned version survives code deploys
A code deployment SHALL keep the champion version currently pinned in the release, unless it is explicitly overridden.

#### Scenario: CI deploy after a promotion
- **WHEN** version 5 is pinned and a new image is deployed by the CI pipeline
- **THEN** the redeployed service still serves `wine-quality/5`

### Requirement: Serving rollback
Reverting a champion rollout SHALL be possible with the release rollback command. After the rollback, the predictor serves the previously pinned version.

#### Scenario: Revert a bad rollout
- **WHEN** version 5 was rolled out over version 4 and the release is rolled back
- **THEN** predictions carry `model_version` `wine-quality/4`

### Requirement: Reference manifest
The repository SHALL contain `deploy/kserve/inferenceservice.yaml`, the InferenceService rendered with the local values. CI SHALL fail when this file differs from what the chart renders.

#### Scenario: Chart changed without updating the reference
- **WHEN** the InferenceService template changes and the reference manifest is not regenerated
- **THEN** the CI check fails and names the file

### Requirement: Local serving add-ons footprint
The serving add-ons installed for the local profile (KServe controller and cert-manager) SHALL use versions pinned in the repository and explicit resources. Their total memory requests SHALL NOT exceed 512 MiB. The per-pod logger sidecar SHALL be limited to 64 MiB requested and 128 MiB limit.

#### Scenario: Add-on values check
- **WHEN** the local add-on values files are inspected by the test suite
- **THEN** every add-on container declares requests and limits, and the summed memory requests are at most 512 MiB

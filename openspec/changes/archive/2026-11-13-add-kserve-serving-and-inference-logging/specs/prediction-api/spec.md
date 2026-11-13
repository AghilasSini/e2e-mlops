# Spec Delta

## MODIFIED Requirements

### Requirement: Serve the registry champion
When `MODEL_URI` designates a registry alias (default `models:/wine-quality@champion`) or an exact registry version (`models:/wine-quality/<N>`), the service SHALL resolve it at startup, load that version, and report it in `model_version` as `<registered-model>/<version>`. A promotion SHALL take effect when the champion rollout pins the new version, or, with an alias, when the service restarts.

#### Scenario: Champion served
- **WHEN** version 4 is champion and the service starts
- **THEN** `/health/ready` returns 200 and `/predict` responses carry `model_version` `wine-quality/4`

#### Scenario: No champion yet
- **WHEN** the registry has no `champion` alias at startup
- **THEN** `/health/ready` returns 503 and `/predict` returns 503

#### Scenario: Pinned version
- **WHEN** the service starts with `MODEL_URI` `models:/wine-quality/3` while version 4 is champion
- **THEN** responses carry `model_version` `wine-quality/3`

## ADDED Requirements

### Requirement: Open Inference Protocol v2
The service SHALL implement the Open Inference Protocol v2 for the model `wine-quality`:
- server and model health and metadata endpoints;
- `POST /v2/models/wine-quality/infer`, which takes the 11 features as named FP64 inputs of equal length and returns INT64 `prediction` and FP64 `probability` outputs.

The same validation as `/predict` SHALL apply, with errors returned as HTTP 400 and an `error` message naming the offending inputs.

#### Scenario: Valid inference request
- **WHEN** a client posts the 11 features as named inputs, each with 2 values
- **THEN** the response is 200 with outputs `prediction` and `probability`, each with 2 values, and the model version

#### Scenario: Missing input
- **WHEN** the request omits the `alcohol` input
- **THEN** the response is 400 with an `error` naming `alcohol`

#### Scenario: Readiness
- **WHEN** no model is loaded
- **THEN** `GET /v2/health/ready` and `GET /v2/models/wine-quality/ready` do not return 200

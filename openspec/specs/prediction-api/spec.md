# prediction-api Specification

## Purpose

Exposes the trained wine-quality model over HTTP with a validated request/response contract and health endpoints, suitable for running behind Kubernetes probes.

## Requirements

### Requirement: Prediction endpoint contract
`POST /predict` SHALL accept a JSON object with the 11 physicochemical features as numbers, using snake_case names. It SHALL return HTTP 200 with `prediction` (0 or 1), `label` (`good` or `not_good`), `probability` (probability of `good`, in [0, 1]) and `model_version`.

#### Scenario: Valid request
- **WHEN** a client posts the 11 features of a known wine sample
- **THEN** the response is 200 with `prediction`, `label`, `probability` and `model_version`

#### Scenario: Batch request
- **WHEN** a client posts `{"instances": [...]}` containing several feature objects
- **THEN** the response contains one prediction per instance, in the same order

### Requirement: Input validation
The service SHALL reject a request with a missing, unknown or non-numeric feature, or a negative feature value. It SHALL return HTTP 422 with a body naming each invalid field, and SHALL NOT call the model.

#### Scenario: Missing and malformed fields
- **WHEN** a client posts `{"alcohol": "abc"}`
- **THEN** the response is 422 and lists `alcohol` as invalid and the 10 other features as missing

#### Scenario: Unknown field
- **WHEN** a client posts the 11 features plus `quality`
- **THEN** the response is 422 naming `quality` as not permitted

### Requirement: Errors are not reported as success
Unexpected server errors SHALL return HTTP 5xx with a generic message. Internal details SHALL NOT be returned to the client. The error SHALL be logged server-side.

#### Scenario: Model failure
- **WHEN** the model raises during prediction
- **THEN** the response is 500 and the server log contains the exception

### Requirement: Health endpoints
The service SHALL expose `GET /health/live`, which returns 200 while the process runs, and `GET /health/ready`, which returns 200 only once a model is loaded and 503 otherwise.

#### Scenario: No model available
- **WHEN** the service starts and no model exists at the configured location
- **THEN** `/health/live` returns 200, `/health/ready` returns 503, and `/predict` returns 503

### Requirement: Model loaded once from configured location
The service SHALL load the model once at startup from a location given by configuration (`MODEL_URI`), not on every request.

#### Scenario: Repeated predictions
- **WHEN** 100 predictions are requested
- **THEN** the model is loaded from storage at most once

### Requirement: No training or command execution over HTTP
The service SHALL NOT expose any endpoint that triggers training or runs shell commands.

#### Scenario: Former training route
- **WHEN** a client requests `GET /train`
- **THEN** the response is 404

### Requirement: Browser form
`GET /` SHALL serve an HTML form for the 11 features. Submitting it SHALL show the predicted label and probability, using the same validation as `/predict`.

#### Scenario: Form submission
- **WHEN** a user submits the form with valid values
- **THEN** the result page shows `good` or `not good` with its probability

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

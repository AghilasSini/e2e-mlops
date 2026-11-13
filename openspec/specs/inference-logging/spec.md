# inference-logging Specification

## Purpose

Records every production prediction request and response in a PostgreSQL feature log, so that ground-truth pairing (Epic 4) and drift monitoring (Epic 5) work from real traffic.

## Requirements

### Requirement: Every prediction is logged
For every request served by the predictor, the system SHALL store one log entry. It SHALL hold the raw request, the raw response, the served model version, the InferenceService name and the time the request was received.

#### Scenario: Single prediction logged
- **WHEN** a client posts one wine to `/predict` and receives a prediction
- **THEN** within 10 seconds the feature log holds one entry with that request, its response and `model_version` `wine-quality/<N>`

### Requirement: Per-instance feature rows
For each predicted wine, the feature log SHALL store one row with the 11 features under their snake_case names, the prediction, the probability of `good`, the model version and the time received. Both the JSON API and the Open Inference Protocol request formats SHALL be supported.

#### Scenario: Batch request
- **WHEN** a client posts 3 instances and receives 3 predictions
- **THEN** the feature log holds 3 feature rows that share the same log entry, in request order, each with its prediction and probability

#### Scenario: Open Inference Protocol request
- **WHEN** a client calls `/v2/models/wine-quality/infer` with 2 rows
- **THEN** 2 feature rows are stored with the same columns as for the JSON API

### Requirement: Request and response pairing
A request event and its response event SHALL be paired by their shared event identifier, in whichever order they arrive. Delivering the same event again SHALL NOT create duplicate rows.

#### Scenario: Response before request
- **WHEN** the response event arrives before the request event
- **THEN** the stored entry ends up with both the features and the predictions

#### Scenario: Redelivery
- **WHEN** the same event is delivered twice
- **THEN** the feature log holds the same rows as after the first delivery

### Requirement: Logging never affects predictions
Logging SHALL be asynchronous with respect to serving. If the feature logger or its database is down or slow, prediction responses SHALL still be returned with unchanged content and status.

#### Scenario: Feature logger down
- **WHEN** the feature logger is not running and a client requests a prediction
- **THEN** the client receives the normal 200 response

### Requirement: Unparseable payloads are kept
A logged payload the logger cannot interpret, such as a rejected request or a non-inference call, SHALL be stored as raw content marked unparsed. It SHALL NOT produce feature rows, and it SHALL NOT make the logger fail.

#### Scenario: Validation error logged
- **WHEN** a client posts `{"alcohol": "abc"}` and receives 422
- **THEN** the log entry stores the raw request payload marked unparsed, and no feature row is created (KServe's agent sends no response event for an error answer)

### Requirement: Least-privilege storage
The feature log SHALL live in its own database, accessed by its own database user whose credentials come from a Kubernetes Secret. The logger endpoint SHALL be reachable only inside the cluster.

#### Scenario: Separate credentials
- **WHEN** the rendered manifests are inspected
- **THEN** the feature logger reads its database password from its own Secret, and no tracking or object-store credential is mounted into it

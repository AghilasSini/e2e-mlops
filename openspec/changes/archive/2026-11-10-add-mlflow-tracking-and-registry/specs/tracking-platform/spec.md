# Spec Delta

## Purpose

Provides a self-hosted, persistent and access-controlled MLflow tracking server and model registry that training, promotion, serving and later orchestration share.

## ADDED Requirements

### Requirement: Self-hosted tracking and registry service
The system SHALL run an MLflow tracking server with the model registry enabled inside the cluster, on both the local profile (k3s or Minikube) and OpenStack. It SHALL use only open-source components and no hosted service.

#### Scenario: In-cluster availability
- **WHEN** the chart is deployed with tracking enabled
- **THEN** the tracking service is reachable from pods in the namespace at a stable in-cluster URL and answers its health endpoint

### Requirement: Durable metadata and artifacts
Run metadata and registry state SHALL be stored in a PostgreSQL database on a persistent volume. Run and model artifacts SHALL be stored in an S3-compatible object store. Both SHALL survive restarts of the tracking server, database and object-store pods.

#### Scenario: Pod restart
- **WHEN** a model version is registered and then the tracking server, database and object-store pods are deleted and recreated
- **THEN** the run, its metrics, its artifacts and the model version with its aliases are still retrievable

### Requirement: Authenticated access
The tracking server SHALL reject unauthenticated requests. Credentials for administrators and for automated clients (training, promotion, serving) SHALL be distinct and supplied through Kubernetes Secrets. The server SHALL NOT be exposed outside the cluster without authentication and TLS.

#### Scenario: Anonymous request
- **WHEN** a client calls the tracking REST API without credentials
- **THEN** the server responds 401 and no data is returned

#### Scenario: Automated client
- **WHEN** the training Job runs with the training credentials Secret mounted
- **THEN** it can create runs and register model versions

### Requirement: Clients never need object-store credentials to log
Training and promotion clients SHALL upload and download artifacts through the tracking server. They SHALL NOT need direct object-store credentials.

#### Scenario: Logging from training
- **WHEN** the training Job logs a model while holding only tracking credentials
- **THEN** the artifacts are stored in the object store and the Job never receives object-store keys

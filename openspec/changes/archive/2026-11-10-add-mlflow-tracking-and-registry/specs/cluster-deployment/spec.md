# Spec Delta

## ADDED Requirements

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

# Spec Delta

## ADDED Requirements

### Requirement: Serve the registry champion
When `MODEL_URI` designates a registry alias (default `models:/wine-quality@champion`), the service SHALL resolve the alias at startup, load that version, and report it in `model_version` as `<registered-model>/<version>`. A promotion SHALL take effect when the service restarts.

#### Scenario: Champion served
- **WHEN** version 4 is champion and the service starts
- **THEN** `/health/ready` returns 200 and `/predict` responses carry `model_version` `wine-quality/4`

#### Scenario: No champion yet
- **WHEN** the registry has no `champion` alias at startup
- **THEN** `/health/ready` returns 503 and `/predict` returns 503

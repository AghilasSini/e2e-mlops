# Spec Delta

## Purpose

Trains the wine-quality classifier from versioned configuration and produces a model artifact plus evaluation metrics, as a single command usable locally, in a container and as a Kubernetes Job.

## ADDED Requirements

### Requirement: Configuration-driven training
Training SHALL read its data location, target threshold, split settings and hyper-parameters (`alpha`, `l1_ratio`) from versioned configuration files. Environment variables SHALL be able to override output and data paths. Credentials SHALL NOT be stored in configuration files.

#### Scenario: Hyper-parameter change
- **WHEN** `alpha` is changed in the parameters file and training is rerun
- **THEN** the trained model uses the new value, with no code change

### Requirement: Single training entrypoint
The system SHALL expose one command that runs validation, splitting, fitting and evaluation in order and exits 0 only if all steps succeed.

#### Scenario: Successful run
- **WHEN** the training command runs on the valid seed dataset
- **THEN** it exits 0 and writes a model artifact and a metrics file to the output directory

#### Scenario: Step failure
- **WHEN** any step fails
- **THEN** the command exits non-zero and logs which step failed

### Requirement: Evaluation metrics
Training SHALL evaluate the model on the held-out test set and write a JSON metrics file containing at least ROC-AUC, F1, accuracy and the test-set size.

#### Scenario: Metrics file content
- **WHEN** training completes
- **THEN** the metrics file contains numeric `roc_auc`, `f1`, `accuracy` in [0, 1] and an integer `n_test`

### Requirement: Deterministic training
Two training runs with the same data, configuration and seed SHALL produce models that give identical predictions on the test set.

#### Scenario: Rerun determinism
- **WHEN** training runs twice with identical inputs
- **THEN** the two models' test-set predictions and metrics are identical

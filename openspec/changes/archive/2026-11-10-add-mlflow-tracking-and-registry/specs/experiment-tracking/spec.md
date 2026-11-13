# Spec Delta

## Purpose

Defines what every training run records in the tracking server so that any registered model can be traced back to its data, code, parameters and evaluation.

## ADDED Requirements

### Requirement: Tracking destination from environment
Training SHALL send runs to the tracking server named by `MLFLOW_TRACKING_URI`, under the configured experiment name. The tracking address and credentials SHALL NOT be written in code, configuration files or notebooks.

#### Scenario: Configured server
- **WHEN** training runs with `MLFLOW_TRACKING_URI` set to the cluster tracking service
- **THEN** a new run appears in the configured experiment on that server

#### Scenario: No server configured
- **WHEN** training runs locally without `MLFLOW_TRACKING_URI`
- **THEN** the run is recorded in a local store inside the git-ignored output directory, and a warning names that location

### Requirement: Tracking failures are not silent
If a tracking server is configured but unreachable or rejects the credentials, training SHALL exit non-zero. It SHALL NOT continue without recording the run.

#### Scenario: Server down
- **WHEN** training starts while the configured tracking server is unreachable
- **THEN** the process exits non-zero with an error naming the tracking URI, and no model is registered

### Requirement: Logged parameters and lineage
Each run SHALL log:
- every model hyper-parameter, the target threshold, the test fraction and the random seed;
- the SHA-256 of the input dataset and the row counts of train and test;
- the git commit and the trainer image tag, when available.

#### Scenario: Lineage lookup
- **WHEN** an operator opens the run behind a registered model version
- **THEN** the run shows the dataset hash, the seed and the git commit used to train it

### Requirement: Logged metrics
Each run SHALL log, on the held-out test set: `roc_auc`, `f1`, `accuracy`, `precision`, `recall`, `log_loss` and `rmse`. `rmse` is the root-mean-square error between the predicted probability of `good` and the true label.

#### Scenario: Metric set
- **WHEN** a training run completes
- **THEN** the run has all seven metrics as finite numbers

### Requirement: Logged artifacts
Each run SHALL log these artifacts:
- the metrics JSON;
- the confusion matrix;
- the data schema used for validation;
- a reference sample of the training features and target, for later drift monitoring;
- the held-out test set used for evaluation.

#### Scenario: Reference data available
- **WHEN** a downstream job downloads the artifacts of the champion's run
- **THEN** it obtains the reference sample and the test set with the same columns as the schema

### Requirement: Model registration
Each successful run SHALL log the fitted model with an input signature and an input example. It SHALL register the model as a new version of the configured registered model (default `wine-quality`). Registration alone SHALL NOT make the version serve traffic.

#### Scenario: New version
- **WHEN** a training run succeeds
- **THEN** the registry gains one new version of `wine-quality` linked to that run, and the `champion` alias is unchanged

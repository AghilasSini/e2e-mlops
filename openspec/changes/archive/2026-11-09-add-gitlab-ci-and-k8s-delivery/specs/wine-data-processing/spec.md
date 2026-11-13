# Spec Delta

## Purpose

Turns the raw red-wine dataset into validated, labelled and reproducibly split data that training, serving and monitoring can rely on.

## ADDED Requirements

### Requirement: Schema enforcement
The system SHALL validate the raw dataset against the declared schema before any downstream step uses it. The schema lists 11 float physicochemical features plus the integer `quality` column. Validation SHALL fail if any declared column is missing, an undeclared column is present, a column has the wrong dtype, or a value is null.

#### Scenario: Dataset matches schema
- **WHEN** the seed dataset `winequality-red.csv` is validated
- **THEN** validation succeeds and reports every column as valid

#### Scenario: Missing column
- **WHEN** the dataset lacks a declared column (e.g. `chlorides`)
- **THEN** validation fails with an error that names the missing column

#### Scenario: Unexpected column or dtype
- **WHEN** the dataset has an extra column, or a feature column holds non-numeric values
- **THEN** validation fails with an error naming each offending column

### Requirement: Validation failure stops the pipeline
A failed validation SHALL stop the training run with a non-zero exit status. No split, model or metrics output from that run SHALL be produced.

#### Scenario: Invalid data aborts training
- **WHEN** training is started on a dataset that fails validation
- **THEN** the process exits non-zero and no model artifact is written

### Requirement: Binary quality target
The system SHALL derive the target `is_good` = 1 when `quality` ≥ the configured threshold (default 6), else 0. The raw `quality` column SHALL NOT be used as a model feature.

#### Scenario: Threshold boundary
- **WHEN** rows with quality 5, 6 and 7 are labelled with the default threshold
- **THEN** their targets are 0, 1 and 1 respectively

### Requirement: Reproducible stratified split
The system SHALL split the labelled data into train and test sets using the configured test fraction and random seed, stratified on the target. The same input and configuration SHALL always produce identical splits.

#### Scenario: Identical reruns
- **WHEN** the split runs twice on the same data and configuration
- **THEN** both runs yield byte-identical train and test sets

#### Scenario: Class balance preserved
- **WHEN** the data is split
- **THEN** the positive-class ratio of train and test differs from the full dataset's by at most 2 percentage points

# Proposal

## Why

After Epic 1 (`add-gitlab-ci-and-k8s-delivery`), every training run overwrites a single `model.joblib` on a volume. That leaves us unable to see which parameters produced which model, to compare a new model with the one in service, or to roll back.

The old setup logged to a hosted DagsHub server through a hard-coded URI and a token committed to the repo. Both are incompatible with the self-hosted, open-source constraint, and the token is leaked (audit SEC-03, TRK-*).

This change is Epic 2. It adds a self-hosted MLflow tracking server and model registry, plus programmatic champion promotion. KServe (Epic 3) and the Airflow retraining DAG (Epic 4) build on that promotion step.

## What Changes

- **Self-hosted MLflow platform on Kubernetes:**
  - Components: the MLflow tracking server with the model registry, a PostgreSQL backend store, and an S3-compatible artifact store. All are added to the Epic 1 Helm chart, for the local profile (k3s or Minikube on a modest machine) and OpenStack.
  - Locally, the platform stays inside the Epic 1 resource budget: single replicas, one MLflow worker, a tuned-down PostgreSQL, and small volumes.
  - Access requires authentication.
  - Credentials come from Kubernetes Secrets.
- **Training logs to MLflow.** `src/training/` records:
  - parameters, data hash and git commit;
  - metrics: ROC-AUC, F1, accuracy, precision, recall, RMSE of predicted probabilities, log-loss;
  - artifacts: metrics JSON, confusion matrix, schema, and a reference sample of the training data that Evidently can use in Epic 5;
  - the model with its signature and input example, registered as a new version of `wine-quality`.
- **Programmatic promotion.** New `src/models/registry_manager.py` evaluates a candidate version against absolute thresholds and against the current champion, on the same held-out data.
  - If the candidate passes, it moves the `champion` alias to it and keeps the old one as `previous-champion`.
  - It records the decision as version tags.
  - It supports dry-run and rollback.
  - It uses MLflow **aliases**. MLflow has deprecated the "Production" stage; the alias `champion` takes its role.
- **Serving uses the registry.** The inference service loads `models:/wine-quality@champion` and reports the registry version. The Epic 1 model PVC hand-off is removed.
- **Lean client images.** The trainer and inference images use `mlflow-skinny`, the client-only package. Only the `mlflow-server` image carries the full server.
- **CI.** Builds a third image, `mlflow-server`, and lints the extended chart. CI never trains models: training stays in the runtime pipeline, as the decoupling principle requires.
- **DagsHub is removed.** All references to DagsHub, the hard-coded tracking URI and `dagshub`-specific environment variables are deleted from code, notebooks and docs.

## Capabilities

### New Capabilities

- `tracking-platform`: the self-hosted MLflow tracking server and registry, covering its persistence, authentication, artifact storage and deployment on both clusters.
- `experiment-tracking`: what every training run must record (parameters, metrics, artifacts, lineage, registered model) and how training behaves when tracking is unavailable.
- `model-promotion`: the rules and interface for promoting a registered model version to champion, comparing it with the incumbent, recording decisions and rolling back.

### Modified Capabilities

These capabilities are created by `add-gitlab-ci-and-k8s-delivery`, which must be archived first. The deltas only add requirements.

- `prediction-api`: adds serving of the registry champion and reporting of its registry version.
- `cluster-deployment`: adds the tracking platform components and registry-based model hand-off between the training Job and inference.

## Impact

- **New code:**
  - `src/training/tracking.py`;
  - `src/models/registry_manager.py` with a CLI;
  - `src/models/__init__.py`;
  - tests under `tests/training/` and `tests/models/`.
- **Changed code:**
  - `src/training/` entrypoint, which now logs and registers;
  - `src/serving/model.py`, which resolves the alias and its version;
  - `params.yaml`, which gains promotion thresholds;
  - `config/config.yaml`, which gains the experiment and model names.
- **Chart:** new templates for MLflow, PostgreSQL and object storage, with Secrets referenced by name. The model PVC is removed.
- **Dockerfile:** new `mlflow-server` target.
- **Dependencies:** `mlflow-skinny` in the runtime images; full `mlflow`, `psycopg2-binary` and `boto3` in the server image and the dev environment only. All Apache-2.0 or compatible.
- **Infrastructure:** OpenStack needs a storage class for the PostgreSQL volume, and either an object store with an S3 API (Swift `s3api` or Ceph RGW) or the in-chart S3 server.
- **APIs:** the `/predict` response shape is unchanged. `model_version` now carries the registry version, for example `wine-quality/3`.
- **Audit findings addressed:**
  - TRK-01: hard-coded registry URI.
  - TRK-02: registry not used by serving.
  - TRK-03: no model signature, seed not logged.
  - PIPE-09: no quality bar; promotion thresholds now enforce one.
  - DATA-02: model lineage only; data versioning stays in a later phase.
- **Order:** apply after `add-gitlab-ci-and-k8s-delivery` is archived. Epic 3 (KServe) depends on this change.

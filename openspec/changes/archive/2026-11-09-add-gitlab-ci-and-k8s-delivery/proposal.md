# Proposal

## Why

The audit (`docs/audit/AUDIT.md`) found a codebase that can't be delivered safely:

- Training crashes on the declared Python 3.8, and the dependencies don't install on 3.12.
- Data validation always passes.
- The CI "test" job only runs `echo`.
- Delivery depends on GitHub Actions, AWS ECR and EC2.

The new version must be 100 % open source and self-hosted, running locally on a single-node k3s or Minikube cluster on a modest personal machine (target: 4 CPU cores, 8 GB RAM, 20 GB free disk) and on OpenStack-managed Kubernetes in production. This change is Epic 1 of the upgrade plan: a tested, containerised codebase delivered by a GitLab CI/CD pipeline to Kubernetes. Every later epic (MLflow, KServe, Airflow, Evidently, Prometheus/Grafana) is built on it.

## What Changes

- **BREAKING: package restructure.** `src/mlProject/` and `main.py` are replaced by modular, typed packages under `src/`:
  - `data/`: loading, schema validation, features and target.
  - `training/`: train and evaluate entrypoint.
  - `serving/`: inference API and request/response schemas.
  - `common/`: configuration and logging.

  The `src/models/` and `src/monitoring/` packages are created by later epics.
- **BREAKING: the model becomes a classifier.** It predicts whether a wine is good (`quality ≥ 6`) instead of regressing the 0–10 score. It uses logistic regression with an elastic-net penalty, which keeps the existing `alpha`/`l1_ratio` hyper-parameters. This makes ROC-AUC and F1 (Epic 2) meaningful.
- **Data validation is fixed.** It now fails when columns or dtypes don't match the schema, and a failure stops the pipeline. The `clorides` typo is corrected, and the train/test split is seeded and stratified.
- **BREAKING: the Flask app is replaced by a JSON inference API.** The API validates its inputs, returns clear errors and exposes liveness/readiness endpoints. The HTML form is kept as a thin page that calls the API. The unauthenticated `GET /train` endpoint, which ran `os.system`, is removed.
- **Python 3.11 and declared dependencies.** Python 3.11 replaces 3.8. Dependencies move to `pyproject.toml` with a lock file, and dev/notebook tooling is split from runtime dependencies.
- **Quality gates:**
  - `pytest` suite under `tests/` covering data processing, training and the inference schema/API.
  - `flake8`, `black` and `isort` configuration.
- **Containers.** A multi-stage `Dockerfile` with a `trainer` target and an `inference` target. Both run as non-root on a supported base image, with a `.dockerignore`.
- **BREAKING: GitLab CI/CD.** `.gitlab-ci.yml` runs `test → build → deploy`, pushes images with immutable tags to the self-hosted GitLab Container Registry, and deploys with Helm to OpenStack Kubernetes and, optionally, to the developer's local cluster. `.github/workflows/main.yaml` and all AWS ECR/EC2 deployment are removed.
- **Kubernetes packaging.** A Helm chart under `deploy/helm/` deploys the inference service and a training Job, plus helper scripts for a `local` target (k3s or Minikube) and an `openstack` target.
- **Modest-machine profile.**
  - Local deployment runs on k3s (recommended on Linux: native, no VM) or Minikube (macOS/Windows).
  - Every workload has an explicit resource budget for the local profile, checked by a test.
  - Each component can be switched off.
  - Images can be built on the machine and loaded into the cluster without a registry.
  - The GitLab server itself is never run on the personal machine.
- **Repository hygiene** (audit phase P1):
  - Add a `.gitignore`.
  - Untrack generated files: `artifacts/`, `logs/`, `mlruns/`, `__pycache__`, `*.egg-info`, `model.joblib`.
  - Remove the hard-coded DagsHub tokens from `research/05_model_evaluation.ipynb` and `README.md`.
  - Fix `setup.py`/README metadata by replacing it.
- **Monitoring-runner image deferred.** Story 1.2 also asks for this image. It is postponed to Epic 5, because the monitoring code it would package doesn't exist until then.

## Capabilities

### New Capabilities

- `wine-data-processing`: loading the wine dataset, enforcing its schema, deriving the binary target, and producing a reproducible train/test split.
- `model-training`: training the wine-quality classifier from configuration and producing a model artifact plus evaluation metrics.
- `prediction-api`: the HTTP inference service, covering its request/response contract, input validation, health endpoints and model loading.
- `ci-pipeline`: the GitLab CI/CD contract, covering quality gates, image builds and tags, registry pushes and deployment triggers.
- `cluster-deployment`: how the system is packaged and run on Kubernetes (a local k3s/Minikube cluster on a modest machine, and OpenStack), including the local resource budget, security context, probes and secrets handling.

### Modified Capabilities

None. `openspec/specs/` is empty.

## Impact

- **Code:**
  - Removed: `src/mlProject/**`, `main.py`, `app.py`, `template.py`, `setup.py`, `requirements.txt`, `.github/workflows/main.yaml`.
  - New: `src/{common,data,training,serving}/`, `tests/`, `pyproject.toml`, `uv.lock`, `Dockerfile`, `.dockerignore`, `.gitignore`, `.flake8`, `.gitlab-ci.yml`, `deploy/helm/e2e-mlops/`, `scripts/`.
  - `config/`, `params.yaml` and `schema.yaml` are rewritten.
- **Data:** `data/raw/winequality-red.csv` is kept as the versioned seed dataset; data versioning is left to a later phase. The third-party download URL is dropped.
- **APIs:**
  - `POST /predict` now takes and returns JSON with a class label and probability instead of a float score.
  - `/train` is gone.
- **Infrastructure:**
  - Needs a GitLab instance with the Container Registry enabled. Assumed to be `git.univ-lemans.fr`, to be confirmed.
  - Needs a GitLab Runner able to build images without a privileged Docker daemon.
  - Needs a kubeconfig for each target cluster. Locally, that is k3s or Minikube on a machine with at least 4 CPU cores and 8 GB RAM.
  - AWS, EC2, ECR and GitHub Actions are no longer used.
- **Audit findings addressed:** SEC-03…08, DEP-01…04, PIPE-01…08, HYG-01…05, DATA-03, SERV-01…04, CONT-01…05 and CICD-01…11 (the GitHub-only ones are dropped with the workflow). tasks.md cites them per task.
- **Not addressed:** credential revocation (audit P0) stays an owner action. Removing tokens from files does not remove them from Git history.
- **Order:** this change must be applied and archived before `add-mlflow-tracking-and-registry` (Epic 2).

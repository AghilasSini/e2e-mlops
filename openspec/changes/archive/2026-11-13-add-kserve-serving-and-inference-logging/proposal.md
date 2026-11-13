# Proposal

## Why

After Epics 1–2, the champion model is served by a plain Kubernetes Deployment that loads `models:/wine-quality@champion` once at startup. This has three gaps:

- **Promotion is not deployment.** A new champion only reaches users when someone restarts the pods, and nothing records which version a deployment was meant to serve.
- **No standard serving layer.** There is no autoscaling and no standard inference protocol, and the spec asks for KServe.
- **No record of production traffic.** Nothing captures the requests and predictions, so drift monitoring (Epics 4–5) has no live data to compare against the training reference set.

This change is Epic 3. It serves the champion through a **KServe InferenceService** and logs every prediction to a **PostgreSQL feature log**, within the modest-machine budget.

## What Changes

**KServe InferenceService (Story 3.1)**

- `inference.mode: kserve | deployment` chooses how the service runs. Locally it runs in KServe **RawDeployment** mode (no Knative or Istio). On OpenStack it runs in **Serverless** mode with scale-to-zero.
  - The scale-to-zero trade-off was accepted by the owner on 2026-11-10: scale-to-zero is not available locally.
  - `deployment` keeps the Epic 1–2 Deployment, for clusters without KServe.
- The predictor is **our existing inference image**, which serves its current API plus the **Open Inference Protocol v2** endpoints (`/v2/models/wine-quality/infer`, `/v2/health/*`).
  - This keeps the model's exact MLflow/scikit-learn/skops environment.
  - KServe's MLServer runtime was rejected: no stable release since June 2025, a multi-GB image, and untested with MLflow 3.17 skops models.
- **Promotion becomes a rollout.** A new command, `scripts/serve-champion.sh` (backed by `python -m models.serving_sync`), resolves the `champion` alias to an exact version. It then upgrades the release with `inference.championVersion=N`.
  - The predictor then loads the immutable `models:/wine-quality/N`.
  - KServe rolls out the new pods, and the version is visible in the spec and in Helm's history.
  - It works in both modes. `scripts/run-training.sh` calls it instead of restarting pods. `deploy.sh` keeps the pinned version across code deploys.
- **Autoscaling.**
  - OpenStack: min 0, max 3 replicas, concurrency target, scale-to-zero.
  - Local: one replica (min = max = 1) to stay within budget.
  - Resource requests and limits on every container.
- `deploy/kserve/inferenceservice.yaml` is the rendered reference manifest, which CI keeps in sync with the chart. `deploy/kserve/` also holds the pinned, resource-tuned install values for KServe and cert-manager, installed by `scripts/kserve-up.sh <local|openstack>`.

**Inference payload logging (Story 3.2)**

- KServe's request/response logger sends a CloudEvent for every request and every response to a new **feature-logger** service (new `src/feature_log/` package and `feature-logger` image).
- The service stores them in PostgreSQL, in database `feature_log`, user `feature_logger`, on the existing in-chart PostgreSQL instance:
  - `inference_log`: one row per call (raw request and response, model version, timestamps);
  - `inference_features`: one row per predicted wine (11 features, prediction, probability, model version). This is the table drift monitoring will read.
- **Failure isolation.** Logging never delays or fails a prediction, and malformed events are kept as raw payloads.
- The existing bootstrap hook creates the database and user. The password comes from a new Secret.

**Delivery**

- CI builds the `feature-logger` image.
- `chart-lint` validates the InferenceService against KServe's CRD schema and checks that the reference manifest is in sync.
- The chart test enforces the local budget, including the logger sidecar.

## Capabilities

### New Capabilities

- `model-serving`: how the champion is served through KServe. Covers the InferenceService, the deployment modes per environment, autoscaling and scale-to-zero, champion rollout and rollback, and the add-on footprint.
- `inference-logging`: capturing every prediction request and response into the feature log. Covers the storage schema, request/response pairing, failure isolation and access control.

### Modified Capabilities

- `prediction-api`:
  - MODIFIED *Serve the registry champion*: a promotion now takes effect through a champion rollout (pinned version), not a manual restart.
  - ADDED: Open Inference Protocol v2 endpoints, with the same validation as `/predict`.
- `cluster-deployment`: ADDED: inference runs as a KServe InferenceService or as a Deployment, chosen through values only. The feature logger and its database fit the local budget.

## Impact

**Code**

- New: `src/feature_log/` (receiver, parsing, storage), `src/models/serving_sync.py`, the `serving/` v2 protocol router, and tests under `tests/feature_log/`, `tests/models/` and `tests/serving/`.
- Changed: `serving/model.py`, which reports a pinned version, and `scripts/mlflow_bootstrap.py`, which also creates the feature-log database.

**Chart**

- New templates: `inferenceservice.yaml`, the feature-logger Deployment/Service/NetworkPolicy, and a local NodePort Service for the predictor.
- New values: `inference.mode`, `inference.championVersion`, `inference.autoscaling.*` and `featureLog.*`.
- New Secret: `e2e-mlops-feature-log`.

**Images**

- A new `feature-logger` target (FastAPI + SQLAlchemy + psycopg2, small).
- KServe's own images (controller, agent) are pulled once and imported locally by `local-up.sh`.

**Cluster add-ons**

- **Local:** cert-manager and KServe 0.21, in RawDeployment mode with resources tuned down. Estimated at about 0.4 GiB of memory requests, to be measured.
- **OpenStack:** Serverless mode needs Knative Serving plus a networking layer (Kourier). Installing those is an owner/cluster prerequisite, documented but not automated.

**Budget**

The chart's local memory limits rise from 2,688 to about 3,008 of the 3,072 MiB budget, which leaves almost no headroom. **Epic 4 (Airflow) will therefore need per-epic budget profiles**, a spec change flagged here, not made here.

**Order and dependencies**

- Builds on the archived Epics 1–2.
- Epic 4 pairs the feature log with ground truth.
- Epic 5 reads `inference_features` against the champion's `reference.csv`.

**Out of scope**

- Ground-truth collection, drift computation and dashboards (Epics 4–6).
- Canary or traffic splitting between versions.
- Retention and purging of the feature log.
- Installing Knative on OpenStack.

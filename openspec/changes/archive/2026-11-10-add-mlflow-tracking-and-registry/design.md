# Design

## Context

This design builds on `add-gitlab-ci-and-k8s-delivery` (Epic 1), which provides:

- `src/{common,data,training,serving}`;
- a classifier trained by `python -m training`, which writes `model.joblib` and `metrics.json`;
- a FastAPI service loading `MODEL_URI` once at startup;
- the Helm chart `deploy/helm/e2e-mlops` with a training Job and a model PVC;
- GitLab CI building the `trainer` and `inference` images.

Constraints:

- Self-hosted and open source only.
- A local single-node k3s or Minikube cluster on a modest personal machine, and OpenStack. The local resource budget is in Epic 1 design, decision 11: CPU requests ≤ 1, memory requests ≤ 2 GiB, memory limits ≤ 3 GiB, volumes ≤ 5 GiB.
- The code pipeline (CI) never trains. Training runs as a Kubernetes Job, and later as an Airflow task in Epic 4.
- Epic 3 (KServe) must be able to load the champion from object storage, and Epic 5 (Evidently) needs a reference dataset for the champion.

## Goals / Non-Goals

**Goals:**
- One MLflow server that training, promotion, serving and Airflow share.
- A promotion step that is a separate command, so Airflow can schedule it as its own task.
- Champion artifacts that KServe and Evidently can reach without extra plumbing.

**Non-Goals:**
- KServe deployment, which is Epic 3. Serving here is still the Epic 1 FastAPI service.
- Automatic restart of serving on promotion. Airflow or an operator restarts the Deployment; Epic 3 replaces this with KServe.
- High availability, backups of PostgreSQL and the object store, and single sign-on for the MLflow UI. These are recorded as risks.
- Hyper-parameter search. A single configured run is enough for now.

## Decisions

### 1. MLflow 3.x with aliases, not stages
- **Version:** pin the latest MLflow 3.x release at lock time. Its registry aliases replace the stages deprecated since 2.9.
- **Aliases:** `champion` and `previous-champion`. The spec's "Production/Champion" maps to `champion`.

*Alternative:* MLflow 2.x with `transition_model_version_stage`. That is deprecated and slated for removal.

### 2. Server topology: proxied artifacts
The server runs as:

```
mlflow server --backend-store-uri postgresql://… --artifacts-destination s3://mlflow-artifacts --serve-artifacts --app-name basic-auth
```

Clients log through the server (`mlflow-artifacts:/…`), so training and serving never hold object-store keys. KServe (Epic 3) will read the underlying `s3://mlflow-artifacts/<experiment>/<run>/artifacts/model` path with its own read-only key. That path is deterministic from the run ID.

*Amended during implementation (MLflow 3.17):*
- **Model location.** A logged model lives under `<experiment artifact root>/models/<model_id>/artifacts`, not under the run's artifacts, so it is not derivable from the run ID. The registered version's `source` (`models:/<model_id>`) and `mlflow.get_logged_model(model_id).artifact_location` give the storage path. Epic 3 resolves KServe's `storageUri` from those.
- **Serialization.** Models use MLflow's default **skops** format (`model.skops`, no pickle), with requirements `mlflow`, `scikit-learn` and `skops` pinned in the model's `requirements.txt`.

*Alternative:* clients write to S3 directly (`--default-artifact-root s3://…`). It is simpler, but every client then needs S3 write credentials.

### 3. Authentication: MLflow basic-auth app
- MLflow ships a basic-auth app (`--app-name basic-auth`) with users and per-experiment and per-model permissions, stored in its own database. We keep that store in the same PostgreSQL instance, in a separate database.
- An init Job (a Helm post-install hook) creates the users `trainer` (EDIT on the experiment, register models) and `serving` (READ on the registered model). Their passwords come from pre-created Kubernetes Secrets.
- The admin password comes from a Secret.
- The UI is reached by `kubectl port-forward` on the local cluster, and by an Ingress with TLS and the same basic auth on OpenStack.

*Alternative:* an OAuth2 proxy in front of the UI with Keycloak. It is stronger, but adds a whole identity stack. It can be added later.

*Amended during implementation (MLflow 3.17 auth):*
- **Packages and secrets.** The server needs `mlflow[auth]` (Flask-WTF for CSRF) and a static `MLFLOW_FLASK_SERVER_SECRET_KEY`, which is one more Secret value, identical on every replica.
- **Startup config.** The auth database URI can only be set in an ini file. `scripts/mlflow-server.sh` writes that file, mode 600, to the `/tmp` emptyDir at startup.
- **Allowed hosts.** MLflow rejects Host headers other than localhost or private IPs, so the chart sets `MLFLOW_SERVER_ALLOWED_HOSTS` to the Service names.
- **Permissions API.** 3.17 replaced per-experiment permission calls with `grant_user_permission(user, resource_type, resource_id, permission)`. `scripts/mlflow_bootstrap.py` grants `trainer` EDIT on the experiment and MANAGE on the registered model, and grants `serving` READ on both. `default_permission` stays READ for any authenticated user.

### 4. Object store: in-chart SeaweedFS locally, external S3 on OpenStack
- **Local (k3s or Minikube):** SeaweedFS (Apache-2.0) as a single `weed server -s3` process, with master, volume and S3 gateway in one pod and a 2 GiB PVC. It idles at well under 128 MiB.
- **OpenStack:** an external S3-compatible endpoint, either Swift with the `s3api` middleware or Ceph RGW, depending on what the cloud offers. Configured by `objectStore.external.endpoint`, with the in-chart store disabled.

*Alternatives:*
- **MinIO:** AGPL. Its community edition stopped publishing images and binaries in 2025, so we would have to build it from source.
- **Garage:** AGPL, a valid option. We'll swap to it if SeaweedFS's S3 gateway lacks a feature.

The chart only depends on "an S3 endpoint plus a bucket plus keys", so swapping is a values change.

### 5. PostgreSQL: official image, plain StatefulSet
`postgres:16` official image, a StatefulSet with one replica and a PVC, credentials from a Secret, and an init script creating the `mlflow` and `mlflow_auth` databases.

*Alternatives:*
- **Bitnami charts:** Bitnami moved most images to a paid catalogue in 2025.
- **CloudNativePG operator** (Apache-2.0): better for production HA, but an extra operator to install. It is noted as the OpenStack upgrade path.

### 6. `mlflow-server` image
A new Dockerfile target, `mlflow-server`, contains `mlflow`, `psycopg2-binary` and `boto3`, runs as non-root, and is built by CI like the other images.

The upstream `ghcr.io/mlflow/mlflow` image is not used, because it has no PostgreSQL or S3 drivers.

### 7. Tracking module in `src/training/tracking.py`
A thin wrapper that the training entrypoint uses:

**Connection.** It resolves the URI from the environment. Without a URI it falls back to `sqlite:///<OUTPUT_DIR>/mlflow.db` and prints a warning.

**Preflight.** It calls `MlflowClient().search_experiments(max_results=1)` before fitting. On failure it raises, so a dead or unauthenticated server stops the run early.

**Run contents.**
- Params: the config, `data_sha256`, `n_train`/`n_test`, `git_sha` and `image_tag`, from the environment (`GIT_SHA`, `IMAGE_TAG`) set by the Dockerfile build arguments.
- Metrics: the seven spec metrics, computed in `training/evaluate.py`.
- Artifacts: `metrics.json`, `confusion_matrix.json`, `schema.yaml`, `reference.parquet` (the full training split, 1,279 rows, small enough to keep whole) and `test.parquet`.
- Model: `mlflow.sklearn.log_model` with signature, input example and `registered_model_name`.

**Output.** The run ID and the new version are written to `OUTPUT_DIR/run.json`, so a script or an Airflow task can pick them up.

`log_loss` and `rmse` need probabilities, which is why the model stays a scikit-learn estimator with `predict_proba`. The local `model.joblib` output from Epic 1 is kept for offline development only.

### 8. Promotion: `src/models/registry_manager.py`
- `python -m models.registry_manager evaluate --version N [--dry-run]` and `python -m models.registry_manager rollback`. Model name, thresholds and `min_improvement` come from `params.yaml` under `promotion:`.
- **Defaults:** `min_roc_auc: 0.78`, `min_f1: 0.70`, `min_improvement: 0.005`. These were calibrated in task 3.3 against 5 split seeds: the lowest observed values were ROC-AUC 0.807 and F1 0.735. The provisional 0.80 was lowered because it left too little margin. The observed values are recorded in `params.yaml`.
- **Evaluation on identical data:**
  - Download the candidate run's `test.parquet`.
  - Load both the candidate and the champion with `mlflow.sklearn.load_model("models:/wine-quality/<v>")`.
  - Score both on that test set.
  - Do not trust the logged metrics for the comparison. A champion trained on a different split would otherwise be compared unfairly.
- **Decision:** a dataclass printed as JSON. Tags are set with `set_model_version_tag`, and aliases with `set_registered_model_alias`.
- **Rollback:** swaps the aliases and tags `rolled_back_at` on the demoted version.
- **Concurrency:** a crash between alias moves leaves the registry inconsistent. The command first sets `previous-champion`, then `champion`; a rerun is idempotent.

*Alternative:* promotion inside the training command. Rejected because Airflow (Epic 4) needs "train" and "promote" as separate tasks with separate retries, and the spec names a dedicated module.

### 9. Serving loads `models:/wine-quality@champion`
- `serving/model.py` detects a `models:/…@alias` URI.
- It resolves the version with `MlflowClient.get_model_version_by_alias` to build `model_version` (`wine-quality/4`), then loads it with `mlflow.sklearn.load_model`.
- It authenticates with the `serving` user from a Secret.
- A missing alias makes `/health/ready` return 503. This keeps the Epic 1 behaviour when there is no model.

### 10. Chart changes
- **Added:**
  - `mlflow` Deployment + Service (+ optional Ingress) + user-init hook Job;
  - `postgres` StatefulSet + Service;
  - `seaweedfs` StatefulSet + Service + bucket-init Job.
- **Toggles:** `tracking.enabled`, `postgres.enabled`, `objectStore.mode: internal|external`.
- **Removed:** the model PVC and the training Job's pod affinity from Epic 1.
- **Training Job:** gains `MLFLOW_TRACKING_URI` and the `trainer` credentials Secret. Its command becomes `python -m training && python -m models.registry_manager evaluate --version $(jq …run.json)`, wrapped in `scripts/train-and-promote.sh` inside the image.
- **Secrets:** created by `scripts/deploy.sh` from CI variables or prompted locally, never in values files. For the local cluster, `scripts/local-secrets.sh` generates random passwords once.

### 11. Lean local sizing
Every platform component is sized to fit the Epic 1 local budget. The values below are the `values-local.yaml` defaults; OpenStack values raise them.

| Workload | Request | Limit | Volume | Settings |
|---|---|---|---|---|
| mlflow server | 100m / 384Mi | 500m / 640Mi | none | `--workers 1`, job runner off (measured ~400 MB) |
| postgres | 100m / 128Mi | 500m / 256Mi | 1 GiB | `shared_buffers=32MB`, `max_connections=30` |
| seaweedfs | 100m / 128Mi | 500m / 256Mi | 2 GiB | single process, small volume size limit |
| init Jobs (users, bucket) | 50m / 64Mi | 200m / 128Mi | none | short-lived |

With the Epic 1 inference and training workloads, and with the model PVC removed, the totals measured by the chart test are:
- CPU requests 0.75 CPU;
- memory requests 1,344 MiB;
- memory limits 2,688 MiB;
- volumes 3 GiB.

The bootstrap Job's limit is 256 MiB.

All are within budget. The chart test enforces this.

**Image size, measured.**
- skinny, skops and the MLflow client dependencies took inference to 507 MB and, with `pyarrow`, the trainer to 661 MB. Two changes bring both back under target:
  - the reference and test sets are logged as **CSV** (`data/reference.csv`, `data/test.csv`), so the trainer no longer needs `pyarrow` (150 MB);
  - images no longer **precompile bytecode** (about 90 MB each). Python compiles in memory at startup, which suits the read-only root filesystem.
- Result: trainer 419 MB, inference 420 MB. mlflow-server is 752 MB: full `mlflow` requires pyarrow, and this image has no size target.

**Client images.**
- The trainer and inference images install `mlflow-skinny` instead of `mlflow`. Skinny contains the tracking client, the registry client and model loading, without the server, UI, SQLAlchemy or Alembic. That saves several hundred MB per image and keeps both images under the 500 MB target.
- The dev environment installs full `mlflow`, because the tests use a local SQLite tracking store, which needs the server-side store code.
- Inside the cluster, `MLFLOW_TRACKING_URI` is always set, so the skinny images never need the SQLite fallback.

*Alternative:* full `mlflow` everywhere. It is simpler, but it doubles image size and pulls the server stack into the inference pod for nothing.

### 12. CI changes
- Add a `build-mlflow-server` job.
- `chart-lint` renders the new toggles in both modes.
- Unit tests use a temporary SQLite tracking store, so CI needs no server and still trains nothing in the cluster.

## Risks / Trade-offs

- **[Risk]** SeaweedFS's S3 gateway may not support every MLflow or KServe call (e.g. multipart upload of large artifacts). → **Mitigation:** an integration test in task 5.2 logs and downloads a model through it. Fall back to Garage by changing values.
- **[Risk]** MLflow basic-auth is basic. Passwords are hashed, there is no SSO, and the UI is session-less. → **Mitigation:** the server is ClusterIP-only on the local cluster, and behind Ingress with TLS on OpenStack. OAuth2 proxy and Keycloak are listed as a later hardening.
- **[Risk]** The KServe/MLServer MLflow runtime may lag MLflow 3.x model formats. → **Mitigation:** the version pinned in the lock is checked against MLServer's supported MLflow range when Epic 3 starts. If needed, pin `mlflow<3` in the training extra; the alias API exists from 2.9 onwards.
- **[Risk]** A single PostgreSQL replica with no backups means losing the PVC loses the registry. → **Mitigation:** documented. `pg_dump` CronJob and CloudNativePG are listed for the OpenStack hardening phase.
- **[Trade-off]** Re-scoring the champion on each candidate's test set costs one extra model download per promotion. That is negligible here, and it makes comparisons fair.
- **[Risk]** Thresholds that are too strict mean nothing is ever promoted; too loose means no protection. → **Mitigation:** calibrated from the seed baseline in task 3.3, versioned in `params.yaml`, and every decision states its numbers.

## Migration Plan

1. Archive Epic 1, then apply this change.
2. On the local cluster (k3s or Minikube):
   - `scripts/local-up.sh k3s`, which also builds and loads the `mlflow-server` image;
   - `scripts/local-secrets.sh`;
   - `scripts/deploy.sh local` (the platform comes up);
   - `scripts/run-training.sh local` (train, register and promote);
   - restart inference, then check `model_version` = `wine-quality/1`.
3. On OpenStack:
   - Create the Secrets and the S3 bucket and keys in the cloud's object store.
   - Set `objectStore.external.*` in `values-openstack.yaml`.
   - Run the manual deploy.
4. **Rollback:** use `python -m models.registry_manager rollback` for a bad model, or `helm rollback` for a bad release. No data migration is needed: Epic 1's PVC models are not imported, and the first run creates `wine-quality/1`.

## Open Questions

- Which object store does the OpenStack cloud expose (Swift `s3api` or Ceph RGW), and at which endpoint? This is a values entry; the chart doesn't change.

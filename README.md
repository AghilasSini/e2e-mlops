# e2e-mlops — wine-quality classifier on an open-source MLOps stack

Predicts whether a red wine is **good** (quality score ≥ 6) from 11 physicochemical
measurements. The project is a teaching example of an end-to-end MLOps setup built
only from open-source, self-hosted tools. It runs on a modest personal machine
(k3s or Minikube) and on OpenStack-managed Kubernetes.

> **Status: Epics 1–3 of 6.**
> - Epic 1: code quality, containers, GitLab CI/CD and Kubernetes delivery.
> - Epic 2: a self-hosted MLflow tracking server and model registry, with champion
>   promotion.
> - Epic 3: the champion is served by **KServe**, and every prediction is logged to a
>   PostgreSQL **feature log**.
>
> Airflow, Evidently and Prometheus/Grafana follow. See [Roadmap](#roadmap).

## Architecture

Two pipelines, deliberately kept apart:

```
 CODE PIPELINE (GitLab CI/CD)                RUNTIME (Kubernetes)
 ────────────────────────────                ─────────────────────────────────────────────────
 git push ─► test ─► build ────► registry    scripts/run-training.sh
            │lint   │trainer        │          └► Job (from suspended CronJob): train-and-promote
            │tests  │inference      │              1. validate → split → fit → evaluate
            │chart  │mlflow-server  │              2. log run + register version  ──┐
            │       │feature-logger │              3. evaluate → move `champion`   ──┤
            └───────┴─► deploy ─────┴─► helm       4. resolve champion → "N"         │
                        local (opt-in)           └► scripts/serve-champion.sh         ▼
                        openstack (manual)          helm upgrade                 MLflow server (basic-auth)
                                                    inference.championVersion=N    │ PostgreSQL: metadata
                                                         │                         │ S3: SeaweedFS / cloud
                                                         ▼                         │
                     client ──► KServe InferenceService `wine-quality`  ◄── loads models:/wine-quality/N
                                │ predictor: our inference image (FastAPI)
                                │   /predict · /v2/models/wine-quality/infer · /health/*
                                └ logger sidecar ──CloudEvents──► feature-logger ──► PostgreSQL
                                  (async, never blocks)                              database feature_log
```

* **CI never trains a model.** It tests, builds images and deploys code. Training is a
  runtime job.
* **Deployments use the immutable commit-SHA tag**, never `latest`.
* **Models only travel through the MLflow registry.** There is no shared volume between
  training and inference.
* **Promotion is not deployment.** Promotion moves the registry's `champion` alias. A
  *champion rollout* then pins the exact version in the Helm release, so what is served
  is visible in `helm history` and can be reverted with `helm rollback`.

## Repository layout

| Path | Content |
|---|---|
| `src/common/` | typed configuration loader (`config.py`), logging |
| `src/data/` | schema, validation, features and `is_good` target, stratified split |
| `src/training/` | model, evaluation, MLflow tracking (`tracking.py`), `python -m training` |
| `src/models/` | `registry_manager.py`: champion promotion and rollback |
| `src/models/serving_sync.py` | resolves the champion alias to the exact version to serve |
| `src/serving/` | FastAPI app, request/response schemas, model loading, HTML form, Open Inference Protocol v2 (`v2.py`) |
| `src/feature_log/` | feature logger: CloudEvents receiver (`app.py`), payload parsing, PostgreSQL storage |
| `tests/` | pytest suite (data, training, models, serving, chart, scripts, integration) |
| `config/config.yaml`, `params.yaml`, `schema.yaml` | paths, names, hyper-parameters, promotion thresholds, data schema |
| `data/raw/winequality-red.csv` | seed dataset (1,599 wines) |
| `Dockerfile` | multi-stage build: `trainer`, `inference`, `mlflow-server` and `feature-logger` targets |
| `deploy/helm/e2e-mlops/` | Helm chart, with `values-local.yaml` and `values-openstack.yaml` |
| `deploy/kserve/` | pinned KServe and cert-manager versions, values and images; `inferenceservice.yaml`, the rendered reference manifest |
| `scripts/` | `local-up.sh`, `kserve-up.sh`, `local-secrets.sh`, `deploy.sh`, `run-training.sh`, `serve-champion.sh`, `wait-inference.sh`, `train-and-promote.sh`, `mlflow-server.sh`, `mlflow_bootstrap.py`, `fetch_tool.py`, `kserve_crd_schema.py` |
| `.gitlab-ci.yml` | CI/CD pipeline |
| `docs/` | audit of the original repository, verification records of each epic |
| `openspec/` | change proposals, designs and specs |
| `research/` | exploratory notebooks from the original tutorial (not used by the pipeline) |

## Requirements

* **Development:** Python 3.11 and [uv](https://docs.astral.sh/uv/).
* **Local cluster:** a machine with at least **4 CPU cores, 8 GB RAM and 20 GB of
  free disk**, plus:
  * [k3s](https://k3s.io) (recommended on Linux) or
    [Minikube](https://minikube.sigs.k8s.io) (macOS/Windows);
  * `kubectl`, [Helm](https://helm.sh) 3.22;
  * `podman` or `docker`;
  * the serving add-ons (KServe and cert-manager), installed once per cluster by
    `scripts/kserve-up.sh` (see [Serving with KServe](#serving-with-kserve)).

## Local development

No cluster or server is needed. Without `MLFLOW_TRACKING_URI`, training, promotion and
serving all share a local MLflow store in `outputs/mlflow.db`, which is git-ignored.

```bash
uv sync --all-extras                 # Python 3.11 virtualenv from uv.lock
uv run pytest --cov=src              # tests + coverage (fails below 80 %)
uv run flake8 src tests scripts
uv run black --check src tests scripts
uv run isort --check-only src tests scripts

uv run python -m training            # train + log the run + register wine-quality/<n>
cat outputs/metrics.json             # roc_auc ≈ 0.82, f1 ≈ 0.75 on the seed data
uv run python -m models.registry_manager evaluate --version 1   # first model -> champion

uv run uvicorn serving.app:app --port 8080   # serves models:/wine-quality@champion
uv run mlflow ui --backend-store-uri sqlite:///outputs/mlflow.db   # browse runs locally
```

Or run training and promotion in one step: `scripts/train-and-promote.sh`.

Training stops with a non-zero exit status if the data does not match `schema.yaml`.
It names every offending column, and nothing is registered.

### Configuration

| Setting | File | Environment override |
|---|---|---|
| dataset path | `config/config.yaml` → `data.raw_path` | `DATA_PATH` |
| schema path | `config/config.yaml` → `data.schema_path` | `SCHEMA_PATH` |
| output directory | `config/config.yaml` → `output.dir` | `OUTPUT_DIR` |
| model served by the API | `config/config.yaml` → `serving.model_uri` (`models:/wine-quality@champion`) | `MODEL_URI` (`models:/wine-quality/<N>` to pin a version, or a local `.joblib` path) |
| MLflow experiment / registered model | `config/config.yaml` → `tracking`, `registry` | — |
| tracking server | — | `MLFLOW_TRACKING_URI`, `MLFLOW_TRACKING_USERNAME`, `MLFLOW_TRACKING_PASSWORD` |
| target threshold, split, `alpha`, `l1_ratio` | `params.yaml` | — |
| promotion thresholds | `params.yaml` → `promotion` | — |
| config root | — | `APP_ROOT` (default: current directory) |

Credentials never go in these files.

## Experiment tracking and model registry (MLflow)

Every training run records the following in the `wine-quality` experiment:

- **Parameters and lineage:** hyper-parameters, seed, test fraction, the SHA-256 of
  the dataset, train/test sizes, git commit and image tag.
- **Metrics** on the held-out test set: `roc_auc`, `f1`, `accuracy`, `precision`,
  `recall`, `log_loss`, and `rmse` of the predicted probabilities.
- **Artifacts:** `metrics.json`, `confusion_matrix.json`, `schema.yaml`, plus
  `data/reference.csv` (the training split, used by drift monitoring later) and
  `data/test.csv`.
- **The model:** a scikit-learn pipeline in skops format, with its signature and an
  input example, registered as a new version of `wine-quality`.

Registering a version does **not** change what is served.

### Promotion (`src/models/registry_manager.py`)

```bash
python -m models.registry_manager evaluate --version 4 [--dry-run]
python -m models.registry_manager rollback
```

A candidate becomes `champion` only if both hold:

1. **It meets the minimums** in `params.yaml` on its own held-out test set:
   `min_roc_auc` 0.78 and `min_f1` 0.70. These are calibrated on 5 seeds, where the
   lowest observed values were 0.807 and 0.735.
2. **It beats the current champion's ROC-AUC by `min_improvement` (0.005)**, with both
   models re-scored on the *candidate's* test set.

When it is promoted, `previous-champion` moves to the old champion first, then
`champion` moves to the candidate. These are registry **aliases**; MLflow's stages are
deprecated.

Each decision is printed as JSON and tagged on the version: `promotion_status`, the
reason and the scores. `rollback` makes `previous-champion` the champion again.

Exit status:
- **0** when a decision is reached, whether or not the candidate was promoted;
- **1** on errors (unknown version, unreachable registry).

So an orchestrator can tell failures apart from rejections.

### The server on Kubernetes

- **Components:**
  - **MLflow server** (`mlflow-server` image), with the registry and **basic-auth**
    enabled.
  - **PostgreSQL** for metadata and the auth database.
  - **S3** for artifacts: SeaweedFS in the chart locally, or the cloud's S3 endpoint
    on OpenStack.
- **Proxied artifacts:** clients upload and download artifacts through the server, so
  training and inference never hold S3 keys.
- **Users and permissions:** an idempotent hook Job (`scripts/mlflow_bootstrap.py`)
  creates two service users:
  - `trainer`: EDIT on the experiment, MANAGE on the model;
  - `serving`: READ on both.
- **Network:** the server is reachable only inside the cluster (ClusterIP). On
  OpenStack an optional Ingress exposes it, with TLS required.
- **Anonymous requests** get 401.

Open the UI:

```bash
kubectl -n e2e-mlops port-forward svc/e2e-mlops-mlflow 5000:5000   # then http://localhost:5000
kubectl -n e2e-mlops get secret e2e-mlops-mlflow-admin -o jsonpath='{.data.password}' | base64 -d; echo
```

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/predict` | JSON prediction, single or batch |
| `GET` | `/health/live` | 200 while the process runs |
| `GET` | `/health/ready` | 200 once a model is loaded, otherwise 503 (retries the load every 30 s) |
| `GET`/`POST` | `/` | HTML form |
| `GET` | `/docs` | OpenAPI documentation |
| `POST` | `/v2/models/wine-quality/infer` | [Open Inference Protocol v2](https://github.com/kserve/open-inference-protocol) prediction |
| `GET` | `/v2/health/live`, `/v2/health/ready`, `/v2`, `/v2/models/wine-quality`, `/v2/models/wine-quality/ready` | OIP v2 health and metadata |

There is no endpoint that trains a model or runs a command.

```bash
curl -s -X POST http://127.0.0.1:8080/predict -H 'content-type: application/json' -d '{
  "fixed_acidity": 7.4, "volatile_acidity": 0.7, "citric_acid": 0.0, "residual_sugar": 1.9,
  "chlorides": 0.076, "free_sulfur_dioxide": 11, "total_sulfur_dioxide": 34,
  "density": 0.9978, "ph": 3.51, "sulphates": 0.56, "alcohol": 9.4}'
# {"prediction":0,"label":"not_good","probability":0.216...,"model_version":"wine-quality/1"}
```

* **Batch:** send `{"instances": [{...}, {...}]}`. The response is
  `{"predictions": [...], "model_version": "..."}`, in the same order.
* **Validation:** inputs must be finite, non-negative JSON numbers. Missing, unknown,
  non-numeric or negative fields return **422** with one entry per field.
* **No model:** with no champion yet, `/predict` returns **503**.
* **Errors:** unexpected errors return **500** with a generic message and are logged
  server-side.
* **New champion:** a promotion takes effect with the *champion rollout*
  (`scripts/serve-champion.sh`), which `scripts/run-training.sh` runs for you. See
  [Serving with KServe](#serving-with-kserve).

**Open Inference Protocol v2.** One named `FP64` input per feature, all of the same
length. The answer has an `INT64` `prediction` output and an `FP64` `probability` output
(probability of *good*), plus `model_version`. Validation is the same as `/predict`, and
errors return **400** with `{"error": "..."}`.

```bash
curl -s -X POST http://127.0.0.1:8080/v2/models/wine-quality/infer -H 'content-type: application/json' -d '{
  "inputs": [
    {"name": "fixed_acidity", "shape": [1], "datatype": "FP64", "data": [7.4]},
    {"name": "volatile_acidity", "shape": [1], "datatype": "FP64", "data": [0.7]},
    {"name": "citric_acid", "shape": [1], "datatype": "FP64", "data": [0.0]},
    {"name": "residual_sugar", "shape": [1], "datatype": "FP64", "data": [1.9]},
    {"name": "chlorides", "shape": [1], "datatype": "FP64", "data": [0.076]},
    {"name": "free_sulfur_dioxide", "shape": [1], "datatype": "FP64", "data": [11]},
    {"name": "total_sulfur_dioxide", "shape": [1], "datatype": "FP64", "data": [34]},
    {"name": "density", "shape": [1], "datatype": "FP64", "data": [0.9978]},
    {"name": "ph", "shape": [1], "datatype": "FP64", "data": [3.51]},
    {"name": "sulphates", "shape": [1], "datatype": "FP64", "data": [0.56]},
    {"name": "alcohol", "shape": [1], "datatype": "FP64", "data": [9.4]}]}'
# {"model_name":"wine-quality","model_version":"wine-quality/1","id":"…","outputs":[
#   {"name":"prediction","datatype":"INT64","shape":[1],"data":[0]},
#   {"name":"probability","datatype":"FP64","shape":[1],"data":[0.216…]}]}
```

## Serving with KServe

The champion runs as a KServe **InferenceService** named `wine-quality`. Its predictor
is our own inference image (custom predictor). It loads the model from the MLflow
registry, so the model keeps exactly the MLflow/scikit-learn/skops environment it was
trained with. `inference.mode` chooses how inference runs, with the same image,
configuration and Secrets either way:

| `inference.mode` | What runs | Used by |
|---|---|---|
| `kserve` | an InferenceService, plus the feature logger | `values-local.yaml`, `values-openstack.yaml` |
| `deployment` | the plain Deployment + Service of Epics 1–2 (NodePort 30080 locally, optional Ingress); no feature log | `values.yaml` default; fallback for clusters without KServe |

| Profile | KServe deployment mode | Scaling |
|---|---|---|
| local | **Standard** (called RawDeployment before KServe 0.15): Deployment + Service, no Knative, no mesh | 1 replica (min = max = 1); **no scale-to-zero locally** |
| OpenStack | **Knative** (called Serverless): needs Knative Serving + Kourier | 0 to 3 replicas, concurrency target 5, **scale-to-zero** when idle |

`scripts/deploy.sh` stops before Helm when the mode is `kserve` and KServe is missing.
The error names `scripts/kserve-up.sh` and the fallback
`scripts/deploy.sh local --set inference.mode=deployment`.

### Access (local)

```bash
kubectl -n e2e-mlops get inferenceservice wine-quality            # READY True
kubectl -n e2e-mlops port-forward svc/wine-quality-predictor 8080:80
# then the curl examples of the API section, on http://127.0.0.1:8080
```

Calls through this Service go through the logger sidecar, which listens on port 9081
and forwards to the container's 8080, so they are recorded in the feature log.

### Champion rollout and rollback

A promotion only moves the registry alias. The rollout pins the exact version in the
release (`inference.championVersion`), so the predictor loads the immutable
`models:/wine-quality/<N>`:

```bash
scripts/run-training.sh local                    # train → promote → roll out the champion it resolved
scripts/serve-champion.sh local --version 2      # pin version 2 by hand (after a manual promotion)
helm -n e2e-mlops history e2e-mlops              # every rollout is a release revision
helm -n e2e-mlops rollback e2e-mlops --wait && scripts/wait-inference.sh   # serve the previous version again
```

- **How the version is found.** `run-training.sh` reads the champion from the last log
  line of the training Job (`{"champion_version": "N", ...}`), because the MLflow server
  is only reachable inside the cluster.
- **When it does nothing.** If version N is already served, it says "already served" and
  touches nothing. With no champion at all, it exits non-zero and leaves the service
  unchanged.
- **When it fails.** A version that never becomes ready is rolled back automatically.
- **Waiting for KServe.** Helm does not wait for custom resources, so
  `scripts/wait-inference.sh` waits for the InferenceService after an upgrade. The
  rollout and `deploy.sh` call it for you. After a manual `helm rollback`, run it
  yourself.
- **Registry alias.** `helm rollback` does not move the registry alias. If the reverted
  model was promoted by mistake, also run `python -m models.registry_manager rollback`.
- **Code deploys keep the pin.** `deploy.sh` reads the version the release serves and
  passes it on, unless `CHAMPION_VERSION=N` overrides it.
- **No `kubectl rollout restart`.** In KServe mode the controller undoes it. Change the
  pinned version instead.

### Add-ons: KServe and cert-manager

`scripts/kserve-up.sh <local|openstack>` installs, once per cluster and idempotently:
1. cert-manager **v1.21.3**;
2. KServe **v0.21.0** (CRDs, then the controller);
3. the size of the logger sidecar: KServe's chart hard-codes it, so the script patches
   the `agent` and `logger` keys of the `inferenceservice-config` ConfigMap.

Versions, chart URLs and SHA-256 checksums are in `deploy/kserve/versions.env`. The
resources are in `deploy/kserve/values-*.yaml`. On Minikube, `scripts/local-up.sh` also
loads the add-on images listed in `deploy/kserve/images.txt` into the node; k3s pulls
them from their public registries.

```bash
scripts/kserve-up.sh local
kubectl get pods -n cert-manager; kubectl get pods -n kserve
```

| Add-on (local) | Requests | Limits | Measured idle |
|---|---|---|---|
| cert-manager controller / webhook / cainjector | 10m / 48 + 24 + 80 MiB | 100m / 128 + 64 + 192 MiB | 22 + 11 + 68 MiB |
| cert-manager startupapicheck (Job, at install) | 10m / 24 MiB | 100m / 64 MiB | — |
| KServe controller + kube-rbac-proxy | 60m / 152 MiB | 350m / 320 MiB | 15–38 + 11 MiB |
| **total** | **328 MiB** (test: ≤ 512 MiB) | | ≈ 125 MiB |
| logger sidecar, per predictor pod (counted in the chart budget) | 25m / 64 MiB | 100m / 128 MiB | 9 MiB |

**OpenStack prerequisite (cluster owner).** Install Knative Serving with the Kourier
networking layer before `scripts/kserve-up.sh openstack`, which checks for them and
stops with instructions otherwise. *(Knative mode was verified on a kind test cluster
with Knative Serving 1.21.3 and Kourier 1.21.1: the predictor scaled to zero after about
60 s idle and answered the next request in about 16 s. Not yet run on OpenStack.)*

## Feature log

KServe's logger sidecar sends every request and every response as a CloudEvent, sharing
one id, to the **feature logger**. The logger stores them in database `feature_log`, as
user `feature_logger`, on the chart's PostgreSQL.

- **Asynchronous.** A slow or stopped logger never changes a prediction.
- **Order-independent.** Each event is an upsert, so the request and the response can
  arrive in any order and redeliveries change nothing.
- **Creation.** The tracking bootstrap hook creates the database and the role.
- **Credentials.** The password is in its own Secret, `e2e-mlops-feature-log`. The
  logger has no MLflow or S3 credentials.
- **Network.** A NetworkPolicy lets only the predictor pods reach the logger. k3s and
  kind enforce it; Minikube's default CNI does not (start Minikube with
  `--cni=calico` to enforce it, at the cost of a little more memory).

| Table | Key | Columns |
|---|---|---|
| `inference_log`: one row per call | `event_id` | `inference_service`, `namespace`, `received_at`, `model_version`, `request_body`, `response_body` (raw text), `status` (`parsed` / `unparsed`) |
| `inference_features`: one row per predicted wine | (`event_id`, `row_index`) | the 11 features (same names as `reference.csv`), `prediction`, `probability`, `model_version`, `received_at` |

What gets stored:
- **Both formats.** JSON API calls (single and `instances`) and OIP v2 calls produce the
  same feature rows.
- **Rejected requests.** A 422 or 400 is kept as `unparsed`, with no feature rows.
  KServe sends no response event for an error answer.
- **GET calls** made through the Service, such as health checks, are counted and
  dropped.
- **Counters.** `GET /metrics` on the logger reports `feature_log_events_total{outcome=...}`.

```bash
kubectl -n e2e-mlops exec -it e2e-mlops-postgres-0 -- sh -c 'psql -U "$POSTGRES_USER" -d feature_log'
```
```sql
SELECT status, model_version, count(*) FROM inference_log GROUP BY 1, 2;
SELECT received_at, alcohol, ph, prediction, round(probability::numeric, 3), model_version
  FROM inference_features ORDER BY received_at DESC LIMIT 10;
SELECT request_body FROM inference_log WHERE status = 'unparsed';   -- rejected calls
```

## Local cluster quick start (k3s or Minikube)

Both distributions use the same `values-local.yaml`. Our four images are built on your
machine. On k3s they are pushed to a small local registry, so no step needs root after
the kubeconfig copy; on Minikube they are loaded straight into the node.

### k3s (Linux)

*(Verified on k3s v1.36.5 in Epic 3: every step below, then the checks of
`docs/epic3-verification.md`.)*

Run every command below **as your normal user, not in a root shell** (`sudo -i`,
`sudo su`). Only the steps that need it call `sudo` themselves.

**1. Give your user its own kubeconfig.** k3s writes `/etc/rancher/k3s/k3s.yaml`
readable by root only. In a root shell, `~` is `/root`, so the copy would land in
`/root/.kube/config` and your user still couldn't reach the cluster. Use explicit
paths:

```bash
mkdir -p "$HOME/.kube"
sudo cp /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config"
sudo chown "$(id -u):$(id -g)" "$HOME/.kube/config"
chmod 600 "$HOME/.kube/config"
```

**2. Point `KUBECONFIG` at it.** The `kubectl` that k3s installs
(`/usr/local/bin/kubectl → k3s`) **ignores `~/.kube/config`**. Without `KUBECONFIG`
it reads the root-only `/etc/rancher/k3s/k3s.yaml` and fails. Set it in your shell
profile:

```bash
echo 'export KUBECONFIG="$HOME/.kube/config"' >> ~/.bashrc
export KUBECONFIG="$HOME/.kube/config"
kubectl get nodes          # must list your node before you go further
```

**3. Load the images, deploy, train and check.**

What each script does:
- `local-up.sh k3s` builds the trainer, inference, mlflow-server and feature-logger
  images and pushes them to a local registry on `127.0.0.1:5000`. The registry is a
  `registry:2.8.3` container named `e2e-mlops-registry`, run by your own podman or
  docker and reachable only from this machine. k3s pulls our images from it over plain
  HTTP, which containerd allows for `localhost`, and pulls PostgreSQL, SeaweedFS,
  cert-manager and KServe from their public registries. No sudo.
- `kserve-up.sh local` installs cert-manager and KServe, once per cluster.
- `deploy.sh local` creates the Secrets once and installs the platform. On a first
  install it also trains, promotes and rolls out a first model. On a k3s node it uses
  the images in `localhost:5000/e2e-mlops` (override with `IMAGE_REPOSITORY`).

```bash
scripts/local-up.sh k3s                       # ≈ 1.5 min once the build cache is warm
curl -s http://localhost:5000/v2/_catalog     # e2e-mlops/{feature-logger,inference,mlflow-server,trainer}

scripts/kserve-up.sh local                    # ≈ 1 min
scripts/deploy.sh local                       # ≈ 8–14 min on a first install
kubectl -n e2e-mlops get inferenceservice wine-quality
kubectl -n e2e-mlops port-forward svc/wine-quality-predictor 8080:80 &
curl -s http://127.0.0.1:8080/health/ready    # {"status":"ok","model_version":"wine-quality/1"}
```

Without KServe, `scripts/deploy.sh local --set inference.mode=deployment` keeps the
Epic 2 layout, and `curl http://127.0.0.1:30080/health/ready` then reaches it through
the NodePort. The deployment mode was run on kind, but this NodePort URL was not
checked there: kind needs extra port mappings, so a port-forward was used instead.

### Minikube (macOS/Windows/Linux)

*(Verified with Minikube v1.39.0, KVM driver, Kubernetes v1.37.0, in Epic 3.)*

```bash
scripts/local-up.sh minikube --start   # starts Minikube with 2 CPUs / 4 GB / 20 GB if needed
scripts/kserve-up.sh local
scripts/deploy.sh local
kubectl -n e2e-mlops port-forward svc/wine-quality-predictor 8080:80
```

Minikube writes `~/.kube/config` for your user itself. Steps 1–2 above are k3s only.

### Retrain, promote and switch models

```bash
scripts/run-training.sh local   # Job: train -> register -> evaluate/promote -> resolve; then roll out
```

A candidate that is not better than the champion is registered but not promoted, and
the rollout reports "already served". See
[Champion rollout and rollback](#champion-rollout-and-rollback).

### Secrets (local)

`scripts/deploy.sh local` runs `scripts/local-secrets.sh`, which creates these Secrets
**once**, with random 32-character passwords:

- `e2e-mlops-postgres`
- `e2e-mlops-s3`
- `e2e-mlops-mlflow-admin`, `-trainer`, `-serving`
- `e2e-mlops-feature-log` (user `feature_logger` of the feature-log database)

Re-running never rotates them. To rotate one, delete the Secret, re-run, then restart
the workloads.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `open /etc/rancher/k3s/k3s.yaml: permission denied` | k3s's `kubectl` without `KUBECONFIG` | step 2: `export KUBECONFIG="$HOME/.kube/config"` |
| `The connection to the server localhost:8080 was refused` | `KUBECONFIG` points to a file that does not exist (e.g. it was copied as root into `/root/.kube`) | redo step 1 as your normal user, then `ls -l "$HOME/.kube/config"` |
| `kubectl get nodes` works in one terminal but not in another (or not for a script/IDE) | the working terminal runs as root, or has `KUBECONFIG` set only there | `whoami; echo "$KUBECONFIG"` in both; set `KUBECONFIG` in `~/.bashrc` |
| pods stuck in `ErrImagePull` / `ImagePullBackOff` for `localhost:5000/...` (k3s) | the local registry is stopped (e.g. after a reboot) or the images were never pushed | rerun `scripts/local-up.sh k3s`; it starts the registry and pushes again |
| pods stuck in `ErrImageNeverPull` (Minikube) | images not loaded into the node | rerun `scripts/local-up.sh minikube` |
| `kserve-up.sh` times out on cert-manager; `kubectl get issuers -A` hangs while built-in resources answer (k3s) | k3s's API server stopped serving newly created CRDs (seen once on k3s v1.36.5) | `sudo systemctl restart k3s`, then rerun `scripts/kserve-up.sh local` |
| `/health/ready` returns 503 | no champion yet, or MLflow not up yet | `scripts/run-training.sh local`; readiness retries every 30 s |
| MLflow `401` from the training Job | the trainer user doesn't exist yet | the bootstrap hook Job creates it at install/upgrade: `kubectl -n e2e-mlops logs job/e2e-mlops-mlflow-bootstrap` |
| `inference.mode=kserve but KServe is not installed` | no InferenceService CRD | `scripts/kserve-up.sh local`, or `--set inference.mode=deployment` |
| MLflow answers **403** through `port-forward` | the local port is not 5000, so the `Host` header is not an allowed host | use `port-forward svc/e2e-mlops-mlflow 5000:5000` |
| `kubectl rollout restart` on `wine-quality-predictor` does nothing | KServe owns that Deployment | `scripts/serve-champion.sh local --version N` |
| feature logger answers 503 just after the first install | its database is created by the post-install hook | wait for the hook; KServe does not resend lost events |

### Footprint on a modest machine

The chart test `tests/deploy/test_chart.py` enforces this budget for the local
profile, with everything enabled: CPU requests ≤ 1, memory requests ≤ 2 GiB, memory
limits ≤ 3 GiB, volumes ≤ 5 GiB, one replica per workload. In KServe mode it also counts
the logger sidecar that KServe injects into the predictor pod.

| Workload | Requests | Limits | Volume | Measured idle (kind) |
|---|---|---|---|---|
| inference predictor (`wine-quality`) | 100m CPU / 192 MiB | 500m / 384 MiB | — | 172 MiB |
| + logger sidecar (`agent`) | 25m / 64 MiB | 100m / 128 MiB | — | 9 MiB |
| feature logger | 50m / 96 MiB | 200m / 192 MiB | — | 55 MiB |
| mlflow server (1 worker, job runner off, one process) | 100m / 384 MiB | 500m / 640 MiB | — | 414 MiB; ≈ 290 MiB since the launcher fix (k3s) |
| postgres (MLflow + feature log) | 100m / 128 MiB | 500m / 256 MiB | 1 GiB | 61 MiB |
| seaweedfs (S3) | 100m / 128 MiB | 500m / 256 MiB | 2 GiB | 99 MiB |
| training (only while it runs) | 250m / 384 MiB | 1 CPU / 768 MiB | — | — |
| init Jobs (bucket, users; at install) | 50m / 64 MiB each | 128 / 256 MiB | — | — |
| **total** | **0.825 CPU / 1,504 MiB** | **3,008 MiB** | **3 GiB** | 811 MiB |

The serving add-ons (KServe, cert-manager) come on top: 328 MiB requested, about
125 MiB in use. In `deployment` mode the totals are those of Epic 2: 0.75 CPU,
1,344 MiB requests, 2,688 MiB limits.

> **Budget headroom.** Memory limits are now at 3,008 of 3,072 MiB, so about 64 MiB is
> left. Epic 4 (Airflow) cannot fit in an "everything enabled" budget: it will introduce
> **per-epic budget profiles**, each enforced by the chart test.

Every component can be switched off. Extra arguments to `deploy.sh` go to Helm:

```bash
scripts/deploy.sh local --set training.enabled=false
```

## GitLab CI/CD setup

The pipeline in `.gitlab-ci.yml` runs `test → build → deploy`. Pipelines that only
change documentation are skipped.

| Stage | Jobs |
|---|---|
| test | `lint` (flake8, black, isort), `unit-tests` (JUnit + Cobertura coverage; includes a real local MLflow server), `chart-lint` (helm lint and kubeconform for both profiles × both inference modes, the InferenceService checked against KServe's CRD schema, both object-store modes, chart tests, and the check that `deploy/kserve/inferenceservice.yaml` matches the chart) |
| build | `build-trainer`, `build-inference`, `build-mlflow-server`, `build-feature-logger`. Built with [Buildah](https://buildah.io) and pushed as `$CI_REGISTRY_IMAGE/<target>:<short SHA>`, plus `latest` on the default branch. Merge requests build without pushing. |
| deploy | `deploy-openstack` (default branch, **manual**, environment `production`); `deploy-local` (only when `LOCAL_DEPLOY_ENABLED=true`, manual, allowed to fail) |

Setup, once per project:

1. **Create the project** on the GitLab instance (e.g. `git.univ-lemans.fr`) and push
   this repository.
   - **Revoke the old credentials first.** The history contains leaked credentials
     listed in [`docs/audit/AUDIT.md`](docs/audit/AUDIT.md).
2. **Enable the Container Registry** (Settings → General → Visibility).
3. **Create a deploy token** named `gitlab-deploy-token` with `read_registry`
   (Settings → Repository → Deploy tokens). GitLab then exposes it to jobs as
   `CI_DEPLOY_USER` / `CI_DEPLOY_PASSWORD`. `deploy.sh` turns it into the
   cluster's `gitlab-registry` pull secret.
4. **Add the CI/CD variables** (Settings → CI/CD → Variables, protected and masked):
   - `KUBECONFIG_OPENSTACK`: *file* type, the production cluster's kubeconfig.
   - **Platform Secrets:** `POSTGRES_PASSWORD`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`,
     `MLFLOW_ADMIN_PASSWORD`, `MLFLOW_FLASK_SECRET_KEY`, `MLFLOW_TRAINER_PASSWORD`,
     `MLFLOW_SERVING_PASSWORD`, `FEATURE_LOG_PASSWORD`. Values must be URL-safe, and
     MLflow passwords at least 12 characters. `deploy.sh openstack` stops before Helm
     if one is missing.
   - `PRODUCTION_HOST`: optional, the inference ingress host.
   - `LOCAL_DEPLOY_ENABLED=true`: optional, see below.
5. **Object store (OpenStack):** create the bucket `mlflow-artifacts` and S3 keys in
   the cloud's object store (Swift `s3api` or Ceph RGW). Then set
   `objectStore.external.endpoint` in `values-openstack.yaml`, or pass it with
   `--set` (see below). `objectStore.createBucket` is `false` there.
6. **Serving add-ons (OpenStack cluster, once):** install Knative Serving with Kourier,
   then run `scripts/kserve-up.sh openstack` with a kubeconfig for that cluster. The
   production values serve through KServe in Knative mode, with scale-to-zero.
   *(Knative mode verified on a kind test cluster; not yet on OpenStack.)*
7. **Runners:**
   - The build jobs need a Docker or Kubernetes executor that can run the
     `quay.io/buildah/stable` image unprivileged. They use `STORAGE_DRIVER=vfs` and
     `BUILDAH_ISOLATION=chroot`. *(Not yet verified on the target GitLab instance.)*
   - Optional `deploy-local`: register a runner tagged `local-cluster` on the machine
     that runs k3s or Minikube, and set `LOCAL_DEPLOY_ENABLED=true`.
8. **Protect `main`.**

CI downloads helm, kubectl and kubeconform as pinned releases, verified by SHA-256
(`scripts/fetch_tool.py`).

To deploy by hand to OpenStack *(not verified: no OpenStack cluster available yet)*.
The eight Secret variables from step 4 must also be exported:

```bash
KUBECONFIG=~/.kube/openstack IMAGE_REPOSITORY=registry.example.org/group/e2e-mlops \
IMAGE_TAG=<short sha> REGISTRY=registry.example.org REGISTRY_USER=... REGISTRY_PASSWORD=... \
scripts/deploy.sh openstack \
  --set objectStore.external.endpoint=https://object-store.example.org \
  --set inference.ingress.host=wine.example.org
```

**Rollback:**
- **A bad model:** `python -m models.registry_manager rollback` (with the trainer
  credentials) prints the restored version. Then run
  `scripts/serve-champion.sh openstack --version <that version>`. To revert only what is
  served: `helm -n e2e-mlops rollback e2e-mlops --wait && scripts/wait-inference.sh`.
- **A bad release:** `helm -n e2e-mlops rollback e2e-mlops`, or rerun the deploy job
  of an earlier pipeline. Deploys use `--atomic`, and in KServe mode they also wait for
  the InferenceService, so a release that never becomes ready is rolled back
  automatically.

## Security

* No credentials in the repository, images or chart values. Runtime credentials come
  from CI/CD variables and Kubernetes Secrets, and reach containers only by
  `secretKeyRef`. See [SECURITY.md](SECURITY.md).
* MLflow requires authentication, and each service user has only the permissions it
  needs.
* Containers run as non-root users with a read-only root filesystem and all
  capabilities dropped. KServe's logger sidecar inherits the predictor's security
  context.
* The feature log has its own database user and Secret. Only predictor pods may reach
  the logger (NetworkPolicy).

## Roadmap

1. ✅ **CI/CD & Kubernetes delivery:** tests, images, GitLab CI, Helm.
2. ✅ **MLflow tracking and registry:** self-hosted server, experiment logging,
   champion promotion.
3. ✅ **KServe** real-time serving of the champion (Standard mode locally, Knative on
   OpenStack), champion rollout and rollback, and a PostgreSQL feature log of every
   prediction. Verified on kind, k3s and Minikube, and in Knative mode with
   scale-to-zero on a kind test cluster; the OpenStack run is pending.
4. **Airflow** retraining and inference-extraction DAGs (train and promote as separate
   tasks).
5. **Evidently** drift detection against the champion's `reference.csv`.
6. **Prometheus + Grafana** dashboards and alerts.

## License

MIT, see [LICENSE](LICENSE).

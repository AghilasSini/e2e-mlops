# Design

## Context

State after Epics 1–2 (archived):

- **Serving.** The inference image (FastAPI, 420 MB) loads `models:/wine-quality@champion` once at startup, using the `serving` MLflow user, and runs as a plain Deployment.
- **Training.** Training runs as a Job created from a suspended CronJob, with the command `train-and-promote`. That command prints the promotion decision as JSON.
- **Platform.** The chart contains MLflow, PostgreSQL and SeaweedFS. With everything enabled locally it uses 0.75 CPU / 1,344 MiB requests, 2,688 MiB limits and 3 GiB of volumes, against a budget of 1 CPU / 2 GiB / 3 GiB / 5 GiB.
- **Verification.** No kubeconfig was readable on the development machine, so the k3s runs of Epics 1–2 (tasks 7.2 and 5.4) were skipped.

Facts checked on 2026-11-10:
- **Versions.** KServe v0.21.0 was released 2026-09-25. cert-manager v1.21.2 was released 2026-09-11.
- **KServe defaults.** KServe's chart defaults to Knative mode (`controller.deploymentMode: Knative`). Its default sizing is heavy: the storage-initializer is limited to 1 CPU / 1 GiB, and default InferenceService resources are 1 CPU / 2 GiB.
- **MLServer.** MLServer, the runtime behind KServe's `mlflow` model format, last had a stable release (1.7.1) in June 2025. Its `-mlflow` image is about 730 MB compressed.

## Goals / Non-Goals

**Goals:**
- Serve the champion through KServe without changing the model's runtime environment.
- Make promotion → deployment an explicit, auditable, reversible step that Airflow (Epic 4) can call.
- Capture production traffic in a form Evidently (Epic 5) can read directly.
- Verify everything on a real Kubernetes cluster, even without access to the owner's k3s.

**Non-Goals:**
- Canary or traffic splitting.
- Request batching.
- GPU.
- Transformers or explainers.
- Feature-log retention.
- Ground truth (Epic 4).
- Installing Knative on OpenStack (an owner prerequisite).
- An OpenStack run (no cluster is available).

## Decisions

### 1. Predictor = our inference image, plus Open Inference Protocol v2
The InferenceService uses a **custom predictor container**: the existing inference image. A FastAPI router adds the OIP v2 endpoints:

- `GET /v2/health/live` and `GET /v2/health/ready`;
- `GET /v2/models/wine-quality` and `GET /v2/models/wine-quality/ready`;
- `POST /v2/models/wine-quality/infer`.

**Request format.** The infer request takes one named FP64 input per feature, for example `{"name": "alcohol", "shape": [n], "datatype": "FP64", "data": [...]}`.

**Response format.** The response has `prediction` (INT64) and `probability` (FP64) outputs, plus `model_version`.

**Validation.** Inputs are validated by the same pydantic `WineFeatures` model as `/predict`, one per row. Errors return 400 with `{"error": "..."}`, as the protocol specifies. `/predict`, the form and `/health/*` are unchanged.

*Alternatives:*
- **`modelFormat: mlflow` (MLServer):** zero code, but the runtime has had no stable release for 17 months, the image is several GB, and it is untested with MLflow 3.17 skops models. The runtime's MLflow would also differ from the one the model was trained with.
- **KServe sklearn server:** needs a pickled model and pins its own scikit-learn version (version skew), and it bypasses our validation.
- **`kserve` Python SDK predictor:** heavier dependencies (Kubernetes client, gRPC) for what is a small router.

### 2. The model is loaded from the registry by version, not through `storageUri`
The predictor keeps loading through MLflow with the `serving` user. Its `MODEL_URI` is set by the chart:

- `models:/wine-quality/<N>` when `inference.championVersion` is set;
- otherwise `models:/wine-quality@champion`, so a fresh install comes up with whatever is champion.

This is an "`mlflow://` backend URI" in the sense of Story 3.1.

*Alternative:* `storageUri: s3://mlflow-artifacts/<exp>/models/<model_id>/artifacts` with KServe's storage-initializer. Rejected because:
- the storage-initializer image is about 100 MB compressed, about 1 GB unpacked;
- its init container defaults to a 1 GiB limit;
- it needs an extra read-only S3 key distributed to the serving namespace.

All three hurt the modest-machine budget, for no gain: a pinned registry version is just as immutable. This supersedes the remark in Epic 2's design that KServe would read the S3 path.

### 3. Champion rollout: Helm value, resolved in-cluster
**Helm holds the version.** The pinned version lives in the Helm release as `inference.championVersion`. A change to it changes the pod template, so KServe or the Deployment rolls out. `helm history` shows every rollout, and `helm rollback` reverts one. Code deploys keep the value: `deploy.sh` reads the current value with `helm get values` and passes it on unless `CHAMPION_VERSION` overrides it.

**The version is resolved in-cluster.** The MLflow server is ClusterIP-only, so an operator's machine or a CI runner can't reach it. The training Job therefore resolves the champion after promotion: `train-and-promote.sh` ends with `python -m models.serving_sync resolve`, which prints `{"champion_version": "N", ...}` as its last line.

**The rollout command.** `scripts/serve-champion.sh <target> (--version N | --from-job <job>)`:
1. takes the version, either given or parsed from the training Job's last log line;
2. compares it with the deployed value, and exits 0 with "already served" if they match;
3. otherwise runs `helm upgrade --reuse-values --set inference.championVersion=N --atomic --wait`.

`run-training.sh` calls `serve-champion.sh --from-job` instead of `kubectl rollout restart`.

**Manual flows.** Manual rollbacks (`registry_manager rollback` prints the restored version) and hand promotions call `serve-champion.sh --version N`.

**Safety.** A version that doesn't exist never becomes ready, so `--atomic` restores the previous release. No new workload is added, which matters with almost no budget headroom left.

*Alternative:* a separate in-cluster "resolver" Job. It costs another CronJob template and about 128 MiB of limits, and the budget can't afford that.

### 4. Deployment modes
`inference.mode`:

| Mode | Rendered | Used by |
|---|---|---|
| `deployment` | the Epic 2 Deployment and Service | `values.yaml` default, so it renders without CRDs |
| `kserve` | an `InferenceService` named after `tracking.modelName` (`wine-quality`) | `values-local.yaml`, `values-openstack.yaml` |

`inference.kserve.deploymentMode` sets the `serving.kserve.io/deploymentMode` annotation. Since KServe 0.15, RawDeployment is called `Standard` and Serverless is called `Knative`. The old names are deprecated aliases, so the chart uses the new ones (owner decision, 2026-11-11):

| Profile | Deployment mode | Scaling |
|---|---|---|
| local | `Standard` (RawDeployment) | `autoscalerClass: hpa`, metric cpu, target 80, min = max = 1 |
| OpenStack | `Knative` (Serverless) | Knative autoscaler, metric concurrency, target 5, `minReplicas: 0`, max 3 |

`deploy.sh` checks for `crd/inferenceservices.serving.kserve.io` before Helm runs, as the spec requires.

**Access.**
- **Locally:** `kubectl port-forward svc/wine-quality-predictor 8080:80`. The Epic 1 NodePort (30080) applies to Deployment mode only. Going through KServe's Service also routes traffic through the logger sidecar.
- **On OpenStack:** Knative's domain configuration gives the URL, and our Ingress template applies to Deployment mode only.

### 5. KServe and cert-manager installation: `scripts/kserve-up.sh <local|openstack>`
- **Install.** Helm installs cert-manager v1.21.3 (`crds.enabled=true`), then KServe v0.21.0 (CRD chart, then resources chart), from their official chart repositories. Versions and resource overrides live in `deploy/kserve/values-{cert-manager,kserve}-{local,openstack}.yaml`.
- **Local:** `controller.deploymentMode=Standard`, controller and rbac-proxy sized small, cert-manager's 3 pods at 10m/32Mi requests each. Total add-on memory requests ≤ 512 MiB, checked by a test.
- **Logger sidecar.** Its resources are not exposed by KServe's chart. They live in `inferenceservice-config` (key `logger`/`agent`), which the script patches to 25m/64Mi requests and 100m/128Mi limits. Task 1.1 confirms the exact keys against v0.21.0.
- **OpenStack:** Serverless mode, which needs Knative Serving with Kourier (much lighter than Istio). The script checks for Knative and stops with instructions if it's missing.
- **Offline images.** On Minikube, `local-up.sh` also pulls and loads the KServe and cert-manager images listed in `deploy/kserve/images.txt` (about 300 MB). *Amended 2026-11-13:* on k3s it pushes our images to a rootless local registry on `localhost:5000` instead of `sudo k3s ctr images import`, which never reached the kubelet on the owner's machine; k3s pulls the third-party images from their public registries.

### 6. Feature logger: KServe logger, CloudEvents receiver, PostgreSQL
**Event delivery.** The predictor declares `logger: {mode: all, url: http://<release>-feature-logger.<ns>.svc.cluster.local/}`. KServe's agent sidecar then sends each request and each response as a CloudEvent in binary mode (`ce-id`, `ce-type` `…inference.request`/`…inference.response`, `ce-inferenceservicename`, `ce-namespace`), with the raw body. It does this asynchronously, so serving is never blocked.

Task 1.2 checked this on KServe v0.21.0 (fixtures in `tests/feature_log/fixtures/`):
- **Headers.** `Ce-Id` (shared by a request and its response), `Ce-Type` (`org.kubeflow.serving.inference.request` or `.response`), `Ce-Inferenceservicename`, `Ce-Namespace`, `Ce-Component`, `Ce-Endpoint` (empty), `Ce-Metadata` (`{}`), `Ce-Time`, `Ce-Recordedtime`, `Ce-Source` and `Ce-Specversion` `1.0`. Each event is a `POST /` with the raw body.
- **No request path.** No header names the path that was called, so the parser recognises the JSON API and OIP v2 by the body's shape.
- **Error answers.** A non-2xx answer (e.g. the 422) produces only the request event. Its entry therefore stores the request, with no response.
- **Probes.** The kubelet probes the container ports directly, and the agent's own probe is not logged. A GET sent through the Service (e.g. `/health/ready`) is logged with an empty request body, which the noise filter drops.
- **Port.** With a logger, the predictor Service targets the agent on port 9081, which proxies to the container's 8080.

**The receiver.** A new package `src/feature_log/` (FastAPI) with three modules:
- `events.py`: parse the CloudEvent headers and body;
- `parsing.py`: turn a body into feature rows, predictions and model version, covering the JSON API (single and `instances`) and OIP v2. Anything else is `unparsed`;
- `store.py`: SQLAlchemy Core, PostgreSQL via psycopg2 in production, SQLite in tests.

**Tables.**

| Table | Key | Columns |
|---|---|---|
| `inference_log` | `event_id` | `inference_service`, `received_at`, `model_version`, `request_body`, `response_body` (JSON), `status` (`parsed`/`unparsed`) |
| `inference_features` | (`event_id`, `row_index`) | 11 feature columns, `prediction`, `probability`, `model_version`, `received_at` |

Epic 5 reads `inference_features` directly against `reference.csv`, which has the same feature columns.

**Pairing and idempotence.** Each event is an upsert keyed by `event_id` (and `row_index`), so the order of arrival and redeliveries don't matter.

**Noise filter.** Events with an empty request body, which are GET calls such as health checks, are counted and dropped, so they don't flood the table. Their response can arrive first (seen in task 1.2), so a response event whose body is not a prediction response (JSON API or OIP v2) is stored only when its request entry already exists, and is otherwise dropped and counted.

**Schema.** Tables are created at startup (`create_all`). No migration tool is needed yet.

**Image.** A new `feature-logger` target with extra `feature-log` (FastAPI, uvicorn, SQLAlchemy, psycopg2-binary), estimated at about 250 MB. Sized at 50m/96Mi requests and 200m/192Mi limits.

*Alternatives:*
- **Agent → Kafka → consumer:** a whole broker on a laptop.
- **Logging inside the app:** couples serving to the database; the spec asks for KServe payload logging.

### 7. Feature-log database and access
- **Location.** It lives in the existing PostgreSQL instance: database `feature_log`, owner role `feature_logger`, password from the new Secret `e2e-mlops-feature-log`.
- **Creation.** The existing bootstrap hook creates both idempotently: `scripts/mlflow_bootstrap.py` gains `ensure_feature_log_db`, which uses psycopg2 with the PostgreSQL admin credentials and updates the password on re-run. This avoids a new hook Job, which the budget can't afford, and works on releases already installed by Epic 2, where the initdb script no longer runs.
- **Network.** A NetworkPolicy lets only the predictor pods (label `serving.kserve.io/inferenceservice`) reach the logger. k3s enforces NetworkPolicies; Minikube's default CNI doesn't, which is documented.
- **Secrets.** The logger gets no MLflow or S3 credentials.

### 8. Budget
The chart test adds the logger sidecar's resources, read from `deploy/kserve/values-kserve-local.yaml`, to the predictor pod when `inference.mode=kserve`. Expected local totals:
- **CPU requests** 0.75 + 0.05 (logger) + 0.025 (sidecar) ≈ **0.83**;
- **memory requests** 1,344 + 96 + 64 = **1,504 MiB**;
- **memory limits** 2,688 + 192 + 128 = **3,008 MiB**, out of 3,072;
- **volumes** 3 GiB.

The add-ons (≤ 512 MiB requests) are budgeted separately. Once tasks run, they are measured with `kubectl top`.

**Consequence.** There is about 64 MiB of headroom left. Epic 4 (Airflow) can't fit under an "everything enabled" budget, so it must introduce **budget profiles**, one per epic, each enforced by the chart test. That is flagged in the proposal, as a spec change for Epic 4.

### 9. Verification cluster: kind on rootless podman when k3s isn't reachable
Everything KServe-related needs a real cluster: CRDs, webhooks, the controller, the agent sidecar. If the owner's k3s still has no readable kubeconfig, the integration tasks create a **throwaway kind cluster** (`KIND_EXPERIMENTAL_PROVIDER=podman`, kind pinned, about 600 MB for the node) in the scratch area. That needs no sudo. Images are loaded with `kind load image-archive`, and the cluster is deleted afterwards.

This is the same Kubernetes API that k3s and Minikube serve, so the chart and scripts are exercised for real. The README states which distribution each check ran on.

It can also run the skipped Epic 1 task 7.2 and Epic 2 task 5.4. Their notes and the verification documents are updated with the results, but those archived changes are not reopened.

*Alternative:* keep waiting for the k3s kubeconfig. That would block every cluster check of the remaining epics.

## Risks / Trade-offs

- **[Risk]** The agent's CloudEvent headers or logged endpoints differ from what decision 6 assumes. → **Mitigation:** task 1.2 captured real events from KServe v0.21.0 into a fixture before the parser is written, and the parser tests use that fixture.
- **[Risk]** KServe v0.21 isn't validated against the Kubernetes 1.36 that the owner's k3s runs. → **Mitigation:** the kind node uses Kubernetes 1.33 (inside KServe's tested range). The README states it, and the owner's k3s run is recorded separately when it happens.
- **[Risk]** Logging is fire-and-forget: if the logger is down, events are lost. → **Mitigation:** accepted, because the spec requires that predictions never suffer. The logger logs and counts drops, and Epic 6 alerts on them.
- **[Risk]** About 64 MiB of budget headroom. → **Mitigation:** decision 8, and Epic 4 introduces profiles.
- **[Risk]** Serverless mode and scale-to-zero can't be tested (no OpenStack or Knative). → **Mitigation:** the rendered manifest is validated against the CRD schema, and a test asserts `minReplicas: 0` plus the Serverless annotation. *Amended 2026-11-13:* Knative mode was then run on a kind test cluster with Knative Serving + Kourier (scale to zero and cold start verified). It found that Knative rejects a pod-level `securityContext`, so the InferenceService now sets those fields on the container in both modes.
- **[Trade-off]** The champion version is resolved inside the training Job, so `serve-champion.sh --from-job` depends on that Job's last log line. A JSON contract test guards that line, and `--version N` is always available.
- **[Risk]** NetworkPolicy isn't enforced on Minikube's default CNI. → **Mitigation:** documented. The logger holds no secrets beyond its own database password.

## Migration Plan

1. `scripts/kserve-up.sh local`, once per cluster.
2. `scripts/local-up.sh k3s`, which now also builds `feature-logger` and pushes the images to the local registry (`localhost:5000`).
3. `scripts/deploy.sh local`. The upgrade from Epic 2 creates the feature-log database through the hook and switches inference to the InferenceService.
4. `scripts/run-training.sh local`, which trains, promotes, then rolls out the champion.

**Rollback:**
- `helm rollback` returns to the Deployment mode release.
- `--set inference.mode=deployment` keeps the Epic 2 behaviour without KServe.

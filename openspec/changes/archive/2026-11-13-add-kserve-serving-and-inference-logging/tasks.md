# Tasks

## 1. Serving add-ons and verification cluster

- [x] 1.1 Pin and install the add-ons.
  - Write `deploy/kserve/values-cert-manager-{local,openstack}.yaml` and `deploy/kserve/values-kserve-{local,openstack}.yaml`: cert-manager v1.21.3 (v1.21.2 in the original plan; owner chose the patch release on 2026-11-11), KServe v0.21.0 (or newer if ≥ 2 weeks old at apply time), RawDeployment locally, explicit small resources.
  - Write `scripts/kserve-up.sh <local|openstack>`: Helm installs, then the `inferenceservice-config` patch for the logger sidecar (25m/64Mi requests, 100m/128Mi limits), then a Knative check for openstack.
  - Prepare a verification cluster, as design decision 9 describes: the owner's k3s if `~/.kube/config` is readable, otherwise a throwaway kind cluster on rootless podman.

  Verify:
  - `shellcheck` is clean;
  - `tests/deploy/test_kserve_values.py` checks that every add-on container has requests and limits, with summed memory requests ≤ 512 MiB;
  - on the verification cluster, `kserve-up.sh local` exits 0 and the KServe controller is Ready;
  - `kubectl top pods -n kserve -n cert-manager` is recorded;
  - the exact `inferenceservice-config` keys used for the sidecar are recorded in a task note.
  - Note (2026-11-11): done on a throwaway **kind v0.33.0 cluster on rootless podman 4.9.3, Kubernetes v1.34.11** (kind v0.33 ships no 1.33 node image; `~/.kube/config` is still absent, so the owner's k3s was not used). Rootless podman only had the `memory` and `pids` controllers, so the cluster was created inside `systemd-run --user --scope -p Delegate=yes` (no sudo).
    - **Mode names.** KServe 0.15+ renamed RawDeployment → `Standard` and Serverless → `Knative`. The old names are still parsed but deprecated (`pkg/constants`). The owner chose the new names on 2026-11-11.
    - **Charts.** Helm's OCI pull from ghcr.io failed on a local Docker credential helper, so `kserve-up.sh` downloads the release `.tgz` files over HTTPS and checks the SHA-256 pinned in `deploy/kserve/versions.env`. `deploy/kserve/images.txt` lists the 7 add-on images for `local-up.sh`.
    - **Sidecar keys.** In v0.21.0 the injected `agent` container takes its resources from the **`agent`** key of `inferenceservice-config` (`agent_injector.go`: `ag.agentConfig.CpuRequest…`). The **`logger`** key's resources are only parsed and validated. The script patches both keys to 25m/64Mi requests and 100m/128Mi limits, keeping `image` and `defaultUrl`.
    - **Results.** `shellcheck` is clean, and `tests/deploy/test_kserve_values.py` passes (8 tests). Local add-on memory requests total **328 MiB**: cert-manager 48 + 24 + 80 + 24 (startupapicheck Job), KServe manager 128 + rbac-proxy 24. `kserve-up.sh local` exited 0 in 67 s, and a rerun also exited 0. `kserve-controller-manager` is 2/2 Ready.
    - **`kubectl top`, idle** (metrics-server v0.9.0, installed on the verification cluster only): cert-manager-controller 1m/22Mi, cainjector 1m/68Mi, webhook 1m/11Mi, kserve manager 4m/15Mi, kube-rbac-proxy 1m/11Mi. The cainjector request was raised from 48Mi to 80Mi after this measurement.
- [x] 1.2 Spike: capture real logger traffic.
  - Deploy a throwaway InferenceService running the current inference image with `logger: {mode: all}`, pointed at a capture service that writes every received request (headers and body) to its log.
  - Send a `/predict` call (single and batch), a 422 call, and let health probes run.

  Verify:
  - the captured events are saved as fixtures under `tests/feature_log/fixtures/` (CloudEvent headers and bodies; no secrets);
  - a task note records which `ce-*` headers exist, whether probes are logged, and the port KServe's predictor Service targets.

  If the format contradicts design.md decision 6, stop and update the design before group 4.
  - Note (2026-11-11): ran on the 1.1 cluster. An InferenceService `spike` (Standard mode) ran `localhost/e2e-mlops/inference:local` with a joblib model mounted from a ConfigMap, and `logger: {mode: all}` pointed at a Python capture server. Calls: `GET /health/live`, single and 3-instance `/predict`, `{"alcohol": "abc"}` (422), `GET /health/ready`. Fixtures: `tests/feature_log/fixtures/kserve-v0.21.0-json-api.json` (9 events).
    - **`ce-*` headers present:** `Ce-Id`, `Ce-Type` (`org.kubeflow.serving.inference.request`/`.response`), `Ce-Inferenceservicename`, `Ce-Namespace`, `Ce-Component` (`predictor`), `Ce-Endpoint` (empty), `Ce-Metadata` (`{}`), `Ce-Time`, `Ce-Recordedtime`, `Ce-Source` (`http://localhost:9081/`), `Ce-Specversion` (`1.0`). Bodies are raw. `Content-Type` appears only when the client sent one.
    - **Probes are not logged:** none appeared in about 60 s. The kubelet probes port 8080 directly, and the agent's probe on 9081 is not logged. GETs sent *through the Service* are logged with an empty request body, and their response event can arrive before the request.
    - **The predictor Service targets port 9081** (the agent), which proxies to 8080. The injected `agent` container had 25m/64Mi requests and 100m/128Mi limits, confirming the 1.1 patch.
    - **Two differences from decision 6, now in design.md:** no header carries the request path, so payloads are recognised by body shape; and a non-2xx answer (the 422) produces **no response event**. The inference-logging 422 scenario now says "raw request payload" for that reason. The CloudEvent format itself matches decision 6, so group 4 is not blocked.
    - **OIP v2 capture (after 2.1).** The rebuilt image was loaded into the same `spike` service. A 2-row infer request gave a request and a response event. A request without `alcohol` got a 400 and, like the 422, produced no response event. Saved as `tests/feature_log/fixtures/kserve-v0.21.0-oip-v2.json` (3 events).
    - **`kubectl rollout restart` is undone by KServe in Standard mode.** The controller reconciles the predictor Deployment's pod template and removes the restart annotation, so no new pod starts. The pods had to be deleted by hand. This confirms that the Epic 2 restart could not have rolled out a new model in KServe mode, and pinning (3.2) is required.

## 2. Inference API: Open Inference Protocol v2 and pinned versions

- [x] 2.1 Add `serving/v2.py`, a router for the OIP v2 endpoints from design.md decision 1: named FP64 inputs per feature, INT64 `prediction` and FP64 `probability` outputs, `model_version`, and 400 `{"error"}` responses using the `WineFeatures` validation. Mount it in `serving/app.py`. Verify: `tests/serving/test_v2.py` covers every scenario of the prediction-api delta's "Open Inference Protocol v2", plus metadata, unequal input lengths, wrong datatype and unknown input, and the existing API tests still pass.
  - Note (2026-11-11): `serving/v2.py` provides `GET /v2`, `/v2/health/{live,ready}`, `/v2/models/wine-quality` and `/ready`, and `POST …/infer`. Inputs may be flat or row-major nested data, with `shape` checked against the number of values. Optional requested `outputs` are supported, an unknown model name gives 404, and no model gives 503 `{"error"}`. The row limit (1,000) is now `schemas.MAX_BATCH_SIZE`, shared with `/predict`. `tests/serving/test_v2.py` has 28 tests and all pass, along with the existing serving tests. `test_api.py::test_no_route_runs_commands` now reads included routers from OpenAPI, because FastAPI 0.162 wraps them as `_IncludedRouter`, and its allowlist gained the six `/v2` paths.
- [x] 2.2 Check that pinned registry versions behave as the modified "Serve the registry champion" requirement says: `MODEL_URI=models:/wine-quality/3` serves `wine-quality/3` while version 4 is champion. Verify: a scenario test in `tests/serving/test_registry_serving.py` passes.
  - Note (2026-11-11): no serving change was needed, because `ModelStore` already loads `models:/<name>/<N>` exactly. The new scenario test `test_pinned_version_while_another_is_champion` creates versions 2–4, moves `champion` to 4, starts the app with `MODEL_URI=models:/wine-quality/3`, and checks that `/health/ready`, `/predict` and the OIP infer all report `wine-quality/3`. It passes, and all 84 serving tests pass.

## 3. Champion rollout

- [x] 3.1 Add `models/serving_sync.py`: `python -m models.serving_sync resolve` prints `{"champion_version": "N", "model_name": ..., "resolved_at": ...}` as one JSON line, and exits 1 when there is no champion. End `train-and-promote.sh` with it, so the training Job's last log line is the resolved champion. Verify: tests cover resolve with and without a champion; a contract test checks that the script's last stdout line parses and contains `champion_version`.
  - Note (2026-11-11): `python -m models.serving_sync resolve` exits **1** when there is no champion, as specified, and **2** when the registry errors (unreachable, bad config). Nothing is printed on stdout in either case.
    - **Exit codes in `train-and-promote.sh`.** Code 1 becomes a stderr note and exit 0, so a rejected first candidate stays a normal outcome, as in Epic 2. Code 2 fails the Job.
    - **Contract.** The promotion decision JSON also has a `champion_version` key (the old champion, or `null`). The champion line is therefore recognised by `resolved_at` plus `champion_version`, both in the contract test and in `serve-champion.sh`.
    - **Tests.** `tests/models/test_serving_sync.py` has 5 tests: with a champion, without one, the CLI's single line, exit 1, and exit 2. `tests/scripts/test_train_and_promote.py` has 4: the last line is the champion after a promotion and after a rejection, and a rejected first candidate exits 0 with no champion line. All pass, and shellcheck is clean.
- [x] 3.2 Write `scripts/serve-champion.sh <local|openstack> (--version N | --from-job JOB)`:
  - takes the version, compares it with `helm get values` and exits 0 with "already served" if equal;
  - otherwise runs `helm upgrade --reuse-values --set inference.championVersion=N --atomic --wait`;
  - exits non-zero when there is no version or the release doesn't become ready.

  Also:
  - `run-training.sh` calls it with `--from-job` instead of `rollout restart`;
  - `deploy.sh` keeps the pinned `inference.championVersion` unless `CHAMPION_VERSION` overrides it, and stops before Helm when `inference.mode=kserve` and the InferenceService CRD is missing, with the message from the spec.

  Verify: `shellcheck` is clean, and `tests/scripts/test_serve_champion.py` (fake `kubectl`/`helm`) covers: version from a job log, "already served" without an upgrade, no champion → non-zero, pinned version kept by `deploy.sh`, and the missing-CRD message.
  - Note (2026-11-11): `shellcheck` is clean, and `tests/scripts/test_serve_champion.py` passes (19 tests). Besides the five required cases, it covers: `--version`; invalid versions; usage errors; a failed upgrade; an InferenceService that never becomes Ready (rolled back); waiting for the predictor rollout; the `CHAMPION_VERSION` override; and a deploy with no pinned version.
    - **Added `scripts/wait-inference.sh`.** `helm upgrade --wait` does not wait for custom resources, so this script waits until KServe has observed the new generation (`status.observedGeneration`) and reports Ready. In Standard mode it also waits for `deployment/<isvc>-predictor` to roll out, because the old pods keep the InferenceService Ready during a rollout. Both `serve-champion.sh` and `deploy.sh` call it and run `helm rollback` if it fails, which gives KServe mode the same guarantee as `--atomic`. In Deployment mode it exits 0 at once.
    - **Choices.**
      - `--from-job` reads `kubectl logs --tail=1` and accepts the line only if it has both `resolved_at` and `champion_version` (see 3.1).
      - `deploy.sh` reads the pinned version just before each Helm call, so a bootstrap's fresh pin survives the final upgrade.
      - `deploy.sh` reads the inference mode from the values file and from any `inference.mode=` argument without calling Helm, so the missing-CRD check really happens before Helm.
      - `run-training.sh` now exits non-zero when there is no champion to serve, which is the spec's "No champion" rule.

## 4. Feature logger (`src/feature_log/`)

- [x] 4.1 Implement `feature_log/events.py` and `feature_log/parsing.py`: CloudEvent header and body parsing, JSON API (single and `instances`) and OIP v2 requests and responses, turned into feature rows, predictions and model version; everything else becomes `unparsed`. Verify: `tests/feature_log/test_parsing.py` runs on the fixtures from 1.2 and covers single, batch, OIP, a 422 body and an empty body.
  - Note (2026-11-11): `tests/feature_log/test_parsing.py` passes (44 tests). It runs on both fixture files from 1.2, covering single, batch, OIP, the OIP 400, the 422 body, empty bodies and health answers, plus 26 malformed payloads.
    - **Shape checks only.** Payloads are recognised by shape (there is no path header): exactly the 11 features, all finite numbers, booleans rejected. The predictor already validated every request it answered.
    - **Partial OIP outputs.** An OIP response with only some requested outputs gives `prediction` or `probability` = `None` for the missing one.
    - **Event errors.** `ce-time` is parsed with its nanoseconds trimmed. A missing `ce-id` or an unknown `ce-type` raises `EventError`.
    - **Packaging, needed so `feature_log` imports no pandas.**
      - `FEATURE_COLUMNS` moved to the pandas-free `data/columns.py`, still re-exported by `data.features`.
      - In `pyproject.toml`, the base dependencies are now only `pyyaml`. numpy, pandas, scikit-learn and joblib form a new `core` extra, included by `train` and `serve`, so those images get the same packages.
      - The `feature-log` extra (fastapi, uvicorn, sqlalchemy, psycopg2-binary) is added here and included in `dev`.
      - `uv lock` kept the same 138 package versions.
- [x] 4.2 Implement `feature_log/store.py` and `feature_log/app.py`:
  - SQLAlchemy Core tables `inference_log` and `inference_features` as in design.md decision 6, created at startup;
  - upserts keyed by `event_id` / (`event_id`, `row_index`);
  - empty-body events dropped and counted;
  - a FastAPI receiver at `POST /`, plus `/health/live` and `/health/ready`;
  - database URL from the environment, password from a Secret.

  Verify: `tests/feature_log/` (SQLite) covers every inference-logging scenario that doesn't need a cluster: single, batch, OIP, response before request, redelivery, unparsed 422, empty body dropped.
  - Note (2026-11-11): `tests/feature_log/` passes (63 tests: 44 parsing, 19 store/app), covering every listed scenario. It also covers: a request and its response delivered concurrently; the OIP 400; an unparsed answer to a stored request; rejected and oversized events; a database that is down (live, not ready, 503) and one that comes back (schema created then); and the password never being logged.
    - **Raw bodies.** `request_body` and `response_body` are `Text` rather than JSON, so a rejected payload is stored byte for byte. The table also has a `namespace` column.
    - **Ordering.** Each event is one transaction whose first statement upserts the `inference_log` row, which locks it. The entry's `status` is then recomputed from both bodies (unparsed when either body doesn't parse or the row counts differ), and an unparsed entry keeps no feature rows. Rows that the response created first take the request's time once it arrives.
    - **Noise filter.** As in design.md decision 6: empty requests, and non-prediction answers with no stored request, are dropped and counted.
    - **Endpoints and settings.** `GET /metrics` exposes `feature_log_events_total{outcome=…}` for Epic 6. Database calls run in the thread pool. The service starts with `uvicorn --factory feature_log.app:create_app`. Settings are `FEATURE_LOG_DATABASE_URL` (no password) plus `FEATURE_LOG_DB_PASSWORD` (from the Secret).
- [x] 4.3 Extend `scripts/mlflow_bootstrap.py` with an idempotent `ensure_feature_log_db`: role `feature_logger` (password updated on re-run) and database `feature_log` owned by it, using the PostgreSQL admin credentials. Add the `feature-logger` Dockerfile target and the `feature-log` extra. Verify:
  - hadolint is clean;
  - the image runs as UID 10001 and is ≤ 300 MB;
  - in podman with real PostgreSQL 16.14, the bootstrap run twice leaves the database and role in place;
  - the logger container stores a fixture event into PostgreSQL;
  - no secret appears in the logs.
  - Note (2026-11-11): all checks pass.
    - **Bootstrap.** `ensure_feature_log_db` runs only when `FEATURE_LOG_PASSWORD` is set, and otherwise prints "skipped". It connects to `PGHOST`/`PGPORT` as `POSTGRES_ADMIN_USER`, with autocommit. It creates or alters the role (`LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE`, password updated on every run), creates the database or sets its owner, and revokes `PUBLIC`'s access.
    - **Image.** hadolint 2.15.1 is clean. `feature-logger` is **180 MB**, runs as UID 10001, and contains no pandas. The mlflow-server image is rebuilt and unchanged at 752 MB.
    - **Podman run** with `postgres:16.14-bookworm` and random passwords:
      - Three bootstrap runs: created, then "password updated / exists" twice, the third with a rotated password. The role is `rolsuper=f`, `rolcreatedb=f`, and the ACL is `{feature_logger=CTc/feature_logger}`.
      - The logger container (`--read-only`) used the rotated password, became ready, created both tables (owned by `feature_logger`), and stored the 12 fixture events: 3 parsed entries carrying `spike/1`, 2 unparsed (the 422 and the OIP 400), 6 feature rows with predictions, and 4 dropped. `/metrics` agreed.
      - None of the three passwords appears in the logger log or the bootstrap logs (grep: 0 lines each).

## 5. Helm chart

- [x] 5.1 Extend the chart:
  - values `inference.mode` (`deployment` default; `kserve` in local and OpenStack values), `inference.championVersion`, `inference.kserve.{deploymentMode, autoscaling}` per design.md decision 4, and `featureLog.*`;
  - template `inferenceservice.yaml` (custom predictor, `MODEL_URI` from `championVersion`, serving credentials, resources, probes, logger URL, deployment-mode annotation);
  - feature-logger Deployment, Service and NetworkPolicy;
  - `secrets.featureLog`;
  - the bootstrap hook gets the PostgreSQL admin and feature-log Secrets;
  - the Epic 2 Deployment is rendered only in `deployment` mode.

  Verify:
  - `helm lint` passes for both profiles;
  - `kubeconform -strict` passes, with the InferenceService validated against KServe v0.21's CRD schema;
  - `tests/deploy/test_chart.py` adds checks for: each mode renders the right kind; RawDeployment with min = max = 1 locally; Serverless with `minReplicas: 0` on OpenStack; `MODEL_URI` pinned when `championVersion` is set; the logger has no MLflow or S3 credentials; and the budget including the logger sidecar from `values-kserve-local.yaml` (expected ≈ 0.83 CPU, 1,504 MiB requests, 3,008 MiB limits).
  - Note (2026-11-11): all checks pass.
    - **Lint and schemas.** `helm lint` passes for both profiles. `kubeconform -strict` (Kubernetes 1.33.0) passes: 15/15 resources locally and 12/12 on OpenStack, with the InferenceService checked against the v0.21.0 CRD. That schema comes from the new `scripts/kserve_crd_schema.py`, which downloads the pinned kserve-crd chart, checks its SHA-256, and makes the CRD strict like `openapi2jsonschema`. An unknown predictor field and a string `minReplicas` are both rejected.
    - **Tests.** `tests/deploy/test_chart.py` has 62 tests. Epic 2 tests now run against the predictor in either mode.
    - **Budget, local kserve mode:** **0.825 CPU / 1,504 MiB requests / 3,008 MiB limits / 3 GiB of volumes**, exactly as designed. Deployment mode is still 0.75 / 1,344 / 2,688.
    - **Choices.**
      - The predictor uses the v1beta1 fields `scaleMetric`/`scaleTarget` and numeric probe ports, since Knative accepts no named ports.
      - The feature logger is rendered only in kserve mode, because only InferenceService pods get the sidecar.
      - The logger's **readiness probe uses `/health/live`**, because Helm `--wait` runs before the post-install hook that creates its database. `/health/ready` keeps the database check.
      - `featureLog.enabled` without `tracking.enabled` fails with a clear message.
    - **Not added:** the proposal's "local NodePort Service for the predictor". Design decision 4 settled on `kubectl port-forward svc/wine-quality-predictor`, which also routes through the logger sidecar.
- [x] 5.2 Generate `deploy/kserve/inferenceservice.yaml` (local values, `helm template -s templates/inferenceservice.yaml`) and add `tests/deploy/test_reference_manifest.py`, which fails when the file is stale. Add `e2e-mlops-feature-log` to `local-secrets.sh`, and `FEATURE_LOG_PASSWORD` to `deploy.sh openstack`. Make `local-up.sh` build `feature-logger` and import the KServe and cert-manager images listed in `deploy/kserve/`. Verify: the reference test passes, then fails after an edit to the template; the fake-kubectl secrets tests cover the new Secret; `shellcheck` is clean.
  - Note (2026-11-11): all checks pass.
    - **Reference manifest.** `deploy/kserve/inferenceservice.yaml` is generated by `python tests/deploy/test_reference_manifest.py --write`: a 3-line "generated, do not edit" header plus the `helm template … -s templates/inferenceservice.yaml` output. The test passes. After changing `mode: all` to `mode: request` in the template it fails with "deploy/kserve/inferenceservice.yaml is stale … Regenerate it with …", and it passes again once the template is restored.
    - **Secrets.** `local-secrets.sh` creates `e2e-mlops-feature-log` (`username=feature_logger`, random 32-character password). `deploy.sh openstack` applies it from `FEATURE_LOG_PASSWORD`, with `FEATURE_LOG_USER` defaulting to `feature_logger`, and stops before Helm when it is missing. `tests/scripts/test_local_secrets.py` passes (9 tests): 6 Secrets created and kept on rerun, 5 distinct passwords, 6 applied on OpenStack, and a new missing-password test.
    - **`local-up.sh`** also builds `feature-logger` and pulls and imports the 7 images in `deploy/kserve/images.txt`.
    - `shellcheck` is clean.


## 6. Integration on a real cluster

- [x] 6.1 On the verification cluster from 1.1:
  1. `kserve-up.sh local`, load the images, then `deploy.sh local` (Epic 2 → Epic 3 upgrade path: deploy Deployment mode first, then switch).
  2. `run-training.sh local` → the champion is rolled out and the InferenceService is Ready.
  3. `/predict` and `/v2/models/wine-quality/infer` through `svc/wine-quality-predictor` carry `wine-quality/1`.
  4. Feature rows appear in PostgreSQL within 10 s: 1 for a single call, 3 for a batch, 2 for an OIP call; a 422 call is stored unparsed.
  5. Scale the logger to 0: predictions still return 200.
  6. A second identical training is rejected, and `serve-champion` reports "already served".
  7. Promote version 2 by hand, run `serve-champion --version 2`, then `helm rollback`: the service serves version 1 again.
  8. Record `kubectl top` for every pod against the budget.

  Verify: the transcript is saved to `docs/epic3-verification.md`, naming the cluster distribution and Kubernetes version.
  - Note (2026-11-11): all 8 steps pass on **kind v0.33.0 / Kubernetes v1.34.11** (rootless podman). The transcript is in `docs/epic3-verification.md`.
    - **Results.** Steps 3–4 took about 0.6 s per call: 1 / 3 / 2 complete feature rows, the 422 stored unparsed, 0 errors. With the logger at 0 replicas, predictions returned 200 in about 10 ms. The second training was rejected with "version 1 already served" and no new Helm revision. Rolling out v2 then running `helm rollback` returned to v1. At idle, 811 Mi in use.
    - **Also checked:** the NetworkPolicy is enforced (an unlabelled pod times out), and a fresh install directly in kserve mode works (bootstrap, train, pin, Ready).
    - **Two defects found and fixed, each with a test:**
      - the Epic 2 bootstrap hook had no writable `/tmp`;
      - a request and its response both tried to create the feature-log tables on first use, which lost one event. Schema creation is now locked.
    - **For the README (7.2):**
      - `helm rollback` does not wait for the InferenceService, so `scripts/wait-inference.sh` must run after it, and the registry alias has to be reset separately.
      - An MLflow port-forward on a local port other than 5000 gets 403 from MLflow's host check.
      - KServe undoes `kubectl rollout restart` on the predictor Deployment.
- [x] 6.2 On the same cluster, run the checks from the skipped Epic 1 task 7.2 (deploy, train, ready, predict) and Epic 2 task 5.4 (401 anonymous; metrics and artifacts; alias; pod-restart persistence; model download through the server). Append the results to `docs/epic1-verification.md` and `docs/epic2-verification.md`, and add a note to the archived tasks files. Verify: both documents have a filled-in cluster section, naming the distribution.
  - Note (2026-11-11): run on the same kind cluster during 6.1 step 1, in Deployment mode (the Epic 1–2 layout), before switching to kserve mode.
    - **Epic 1 7.2:** deploy, training, `/health/ready` 200, `/predict` all pass.
    - **Epic 2 5.4:** all 8 steps pass. The anonymous call gets 401; a port-forward on a port other than 5000 gets 403 from MLflow's host check.
    - **Documents:** `docs/epic1-verification.md` section 3 and `docs/epic2-verification.md` section 4 are filled in, naming kind v0.33.0 / Kubernetes v1.34.11. Their summary rows say the k3s run itself is still pending. Notes were added under the archived tasks 7.2 and 5.4, with their checkboxes left unticked, since those changes are not reopened.
    - **One Epic 2 defect fixed here:** the bootstrap hook's `/tmp`.

## 7. CI and documentation

- [x] 7.1 CI changes:
  - a `build-feature-logger` job;
  - `chart-lint` validates the InferenceService against the KServe CRD schema (pinned), runs the reference-manifest check, and renders both inference modes.

  Verify: `check-jsonschema` passes, `gitlab-ci-local --list` shows the new job, and `gitlab-ci-local --container-executable podman --concurrency 1 lint unit-tests chart-lint` passes.
  - Note (2026-11-11): all checks pass.
    - **CI file.** `check-jsonschema` 0.39.0 (`vendor.gitlab-ci`) passes. `gitlab-ci-local` 4.77.0 `--list` shows 8 jobs, including `build-feature-logger` in `build`.
    - **chart-lint** now runs `scripts/kserve_crd_schema.py` (pinned chart, SHA-256 checked). It then runs `helm lint` and `kubeconform -strict` for local and OpenStack × kserve and deployment modes (15/13/12/11 resources valid, plus 13 for the external object store), with the InferenceService checked against KServe's schema. `pytest tests/deploy` (64 tests) includes the reference-manifest check.
    - **Local run** in podman containers: `--concurrency 1 lint unit-tests chart-lint` passes all three. unit-tests: 288 passed, 56 helm tests skipped there, 97.9 % coverage.
    - **Also:** the header comment lists `FEATURE_LOG_PASSWORD` and the Knative/Kourier and `kserve-up.sh openstack` prerequisite. The new files are marked `git add --intent-to-add`, as in Epics 1–2, because gitlab-ci-local copies only files Git knows about.
- [x] 7.2 Update the README:
  - serving architecture (KServe modes, OIP v2 examples, port-forward access);
  - champion rollout and rollback commands;
  - the feature-log schema and how to query it;
  - add-on installation and footprint;
  - the OpenStack Knative/Kourier prerequisite and `FEATURE_LOG_PASSWORD`;
  - the budget-headroom note.

  Verify: every README command block was run in 6.1, or is marked as not run. Then run `openspec validate add-kserve-serving-and-inference-logging` and confirm it passes.
  - Note (2026-11-11): `openspec validate add-kserve-serving-and-inference-logging` passes.
    - **What changed.** The README now covers Epics 1–3. It has a new architecture diagram, an OIP v2 section with a curl example, a "Serving with KServe" section (modes, access, champion rollout and rollback, add-on install and footprint, the OpenStack Knative/Kourier prerequisite), and a "Feature log" section (schema, behaviour, `psql` queries). The quick start, Secrets, troubleshooting (4 new rows), footprint (with the budget-headroom note), CI setup (`FEATURE_LOG_PASSWORD`, add-ons step), rollback, security and roadmap are updated too.
    - **Run on the kind cluster after the edit, exactly as written:** `kubectl get inferenceservice`, the port-forward, `/health/ready`, the `/predict` curl, the OIP curl, `kubectl get pods` for the add-ons, `helm history`, `serve-champion.sh --version 1` ("already served"), and the three SQL queries. The other commands were run during 6.1: `kserve-up.sh local`, `deploy.sh local`, `run-training.sh local`, `helm rollback … && wait-inference.sh`.
    - **Marked as not run:** the k3s-specific steps (`local-up.sh k3s`, `k3s ctr`), Minikube, the NodePort URL on kind, and everything OpenStack/Knative.
  - Note (2026-11-13), after the owner's review: the runs that were "not run" above. Details in `docs/epic3-verification.md` sections C–E.
    - **Minikube** v1.39.0 (KVM, Kubernetes v1.37.0): the README flow as written passes all checks. Its default CNI does not enforce the NetworkPolicy; the README now suggests `--cni=calico`.
    - **Knative mode** on a kind test cluster with Knative Serving 1.21.3 + Kourier 1.21.1: `kserve-up.sh openstack` passes, KPA with the chart's settings, predict and OIP through Kourier are logged, scale to zero after 61 s idle, cold start 15.6 s.
    - **Defect fixed:** Knative's webhook rejects a pod-level `securityContext`. `templates/inferenceservice.yaml` now puts those settings on the container (both modes); chart test updated, reference manifest regenerated.
    - **k3s** v1.36.5: `sudo k3s ctr images import` never reached the kubelet, so `scripts/local-up.sh k3s` now pushes our images to a rootless local registry on `localhost:5000` (no sudo) and `scripts/deploy.sh local` uses it on a k3s node. Every check passes, including the NetworkPolicy, the second training and the v2 rollout / rollback. README quick start and troubleshooting updated.
    - **Open findings, not fixed:** Knative cannot resolve `localhost/...` image tags (test-only); k3s once stopped serving new CRDs until restarted.
    - **MLflow OOM fixed (owner decision, 2026-11-13).** It was OOMKilled at 640 Mi because `mlflow server` kept an idle parent process (about 250 Mi) next to the uvicorn worker. `scripts/mlflow_server_exec.py` runs the same CLI but execs uvicorn, so one process remains: idle about 290 Mi (was 427 Mi), peak 548 Mi under training + rollout + rollback (was 639 Mi, killed), no restarts. Unit, contract and integration tests added. Along the way, `deploy.sh local` now sets `image.pullPolicy=Always` for images from the local registry, so a restarted pod runs the last pushed `local` image.

- Spec amendment at archive time (2026-11-13, owner decision): the cluster-deployment requirement "Local images without a registry" contradicted the k3s local registry, so this change renames it to "Local images without a remote registry" and modifies it (images built locally, loaded into the node or pulled from a loopback-only local registry, no remote registry, credentials or GitLab; a restarted pod runs the rebuilt image; Minikube stays fully offline).

## Workflow follow-up

- Owner: install Knative Serving and Kourier on the OpenStack cluster before the first Serverless deploy.
- Epic 4 must introduce per-epic budget profiles (the "everything enabled" budget has about 64 MiB of headroom left).
- Archive this change after the owner's review.

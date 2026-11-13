# Tasks

## 1. Dependencies and configuration

- [x] 1.1 Add `mlflow-skinny` (latest 3.x) to the `train` and `serve` extras, full `mlflow` of the same version to `dev` (needed by the SQLite-based tests), and a `tracking-server` extra with `mlflow`, `psycopg2-binary` and `boto3`. Add `pyarrow` for the parquet artifacts. Re-lock with `uv lock`. Verify: `uv sync --frozen --all-extras` succeeds, and `pip-audit` on the lock reports no known vulnerabilities (or lists each with a reason).
- [x] 1.2 Extend `config/config.yaml` with `tracking.experiment_name` (`wine-quality`) and `registry.model_name` (`wine-quality`). Add a `promotion:` block to `params.yaml` with `min_roc_auc`, `min_f1` and `min_improvement`. Remove every DagsHub reference from code, notebooks and docs. Verify: the config tests cover the new keys, and `grep -rni dagshub . --exclude-dir={.git,docs,openspec}` returns nothing.

## 2. Experiment tracking (`src/training/`)

- [x] 2.1 Extend `training/evaluate.py` to compute the seven spec metrics (`roc_auc`, `f1`, `accuracy`, `precision`, `recall`, `log_loss`, `rmse` on probabilities) and a confusion matrix. Verify: unit tests check `rmse` against a hand-computed example and check that every metric is finite.
- [x] 2.2 Implement `training/tracking.py`:
  - resolve the URI from `MLFLOW_TRACKING_URI`, or fall back to local SQLite in `OUTPUT_DIR` with a warning;
  - run a preflight call that fails fast;
  - log params and lineage (`data_sha256`, `n_train`/`n_test`, `GIT_SHA`, `IMAGE_TAG`), the metrics, the artifacts (`metrics.json`, `confusion_matrix.json`, `schema.yaml`, `reference.parquet`, `test.parquet`) and the model with signature, input example and registration;
  - write `run.json`.

  Wire it into `python -m training`. Verify: tests against a temporary SQLite tracking store cover:
  - every scenario of `specs/experiment-tracking`;
  - a non-zero exit with an unreachable `MLFLOW_TRACKING_URI` (`http://127.0.0.1:9`) and no registered version;
  - registration that leaves `champion` unset.

  (Audit TRK-01, TRK-03)
  - Note (2026-11-09): `tests/training/test_tracking.py` has 10 tests, all passing, and the 21 Epic 1 training tests still pass through the local SQLite fallback. Two interpretation choices:
    - **Data artifacts.** The spec says they have "the same columns as the schema". They hold the 11 model features (snake_case) plus the `is_good` target, which is what drift monitoring compares. They are stored under `data/` in the run.
    - **Preflight timing.** The preflight runs after the split but before fitting. It caps MLflow's HTTP retries at 2, with a 15 s timeout unless overridden, so an unreachable server fails in seconds rather than minutes.

    Models are serialized with skops (MLflow 3.17's default outside Databricks, no pickle), and `skops` is added to the client extras.

## 3. Model promotion (`src/models/`)

- [x] 3.1 Implement `models/registry_manager.py`:
  - `evaluate --version N [--dry-run]`: absolute gate, then re-score candidate and champion on the candidate's `test.parquet`, then the `min_improvement` rule, then move `previous-champion` before `champion`, then set the decision tags, then print the JSON decision;
  - `rollback`;
  - exit codes as specified.

  Verify: `tests/models/test_registry_manager.py`, against a temporary SQLite registry with versions trained on purpose with good and bad hyper-parameters, covers every scenario of `specs/model-promotion`: first model, better, not better, below threshold, dry run leaving the registry unchanged, rollback, rollback with no previous champion, and unknown version exiting non-zero.
- [x] 3.2 Add `scripts/train-and-promote.sh`, copied into the trainer image. It runs `python -m training`, then `evaluate` with the version taken from `run.json`, and exits non-zero only if a command errors. Verify: `shellcheck` is clean, and a test runs it against a local SQLite store, checking that it exits 0 and that `champion` is set after the first run.
  - Note (2026-11-09): shellcheck is clean. `tests/scripts/test_train_and_promote.py` passes (3 tests): the first run promotes v1; a second identical run is rejected but exits 0; a training failure exits non-zero before evaluation. The script is installed as `/usr/local/bin/train-and-promote` and is the trainer image's default command; the image build is verified in 5.1.
- [x] 3.3 Calibrate the thresholds. Run training on the seed data with the default parameters 5 times with different seeds, and set `min_roc_auc` and `min_f1` a margin below the lowest observed values. Record the observed values and the chosen thresholds in `params.yaml` comments. Verify: the default-parameter model passes the gate in the test suite. (Audit PIPE-09)
  - Note (2026-11-09): observed ROC-AUC 0.807–0.845 and F1 0.735–0.786 over seeds 42, 1, 7, 123 and 2026. The design's provisional `min_roc_auc: 0.80` was only 0.0065 below the worst seed, so it was lowered to **0.78**; `min_f1` stays **0.70** and `min_improvement` stays 0.005. `test_default_model_passes_the_calibrated_gate` and `test_degenerate_model_fails_the_calibrated_gate` both pass.

## 4. Serving from the registry

- [x] 4.1 Update `serving/model.py`:
  - `models:/<name>@<alias>` URIs resolve to a version through the client, load with `mlflow.sklearn.load_model`, and report `model_version` as `<name>/<version>`;
  - a missing alias makes readiness return 503;
  - local paths keep working for development.

  Set the default `MODEL_URI` to `models:/wine-quality@champion`. Verify: `tests/serving/` covers "Champion served" (`wine-quality/1`) and "No champion yet" (503) against a temporary SQLite registry, and the Epic 1 API tests still pass. (Audit TRK-02)
  - Note (2026-11-09): `tests/serving/test_registry_serving.py` adds 9 tests; with the 44 Epic 1 API tests, all 53 pass. Covered: champion served as `wine-quality/1`; no champion, or an empty registry, gives 503; the default `MODEL_URI` comes from config; explicit `models:/<name>/<version>`; loaded once over 100 calls; a malformed URI keeps the service alive but not ready. Without `MLFLOW_TRACKING_URI`, serving reads the same local store that training writes (`<OUTPUT_DIR>/mlflow.db`), so local development works without a server. The service loads the *resolved* version, not the alias, so a promotion landing mid-load cannot mismatch the version number and the weights. The inference image's `MODEL_URI` default is now the champion.

## 5. Platform on Kubernetes

- [x] 5.1 Add the `mlflow-server` target to the `Dockerfile`: tracking-server extra, non-root, entrypoint `mlflow server … --serve-artifacts --app-name basic-auth`. Add `GIT_SHA` and `IMAGE_TAG` build arguments, exposed as environment variables, to the trainer image. Verify: `hadolint` is clean, the image builds, `id -u` is 10001, and `mlflow --version` prints the locked version.
  - Note (2026-11-09): built with podman. hadolint is clean, `id -u` is 10001 in all three images, `mlflow --version` prints 3.17.2, and the trainer has mlflow-skinny 3.17.2. The `GIT_SHA`/`IMAGE_TAG` build args were already in place from Epic 1.
    - **Sizes:** trainer 419 MB and inference 420 MB (both ≤ 500 MB); mlflow-server 752 MB (no target; full `mlflow` requires pyarrow).
    - **End-to-end with the real images**, all with `--read-only` root filesystems: the server image's bootstrap creates the users and grants; anonymous requests get 401; the trainer image's default command `train-and-promote` registers and promotes `wine-quality/1`; the inference image, as the `serving` user, is ready and serves `wine-quality/1`. 0 secrets in container logs.
    - **Also added:** `scripts/mlflow-server.sh` (entrypoint), `scripts/mlflow_bootstrap.py` (idempotent users and grants), and `tests/integration/test_mlflow_server.py` (5 tests against a real local server).
- [x] 5.2 Extend the Helm chart:
  - `postgres` StatefulSet and init script, `seaweedfs` StatefulSet and bucket-init Job, and the `mlflow` Deployment, Service, optional Ingress and user-init hook Job;
  - `objectStore.mode: internal|external` toggles;
  - Secrets referenced by name only;
  - the model PVC and the training affinity removed;
  - the training Job running `train-and-promote.sh` with the `trainer` credentials, and inference using the `serving` credentials.

  Verify:
  - `helm lint` and `kubeconform -strict` pass for local values and for OpenStack values in external mode;
  - `tests/deploy/test_chart.py` asserts no volume is shared between the training and inference pods, external mode renders no SeaweedFS workload, every container is non-root with resource limits, and the Epic 1 local budget test still passes with tracking, postgres, seaweedfs, inference and training all enabled (expected ≈ 0.75 CPU / 1.2 GiB requests and ≈ 2.4 GiB limits, per design.md decision 11).
  - Note (2026-11-09): helm lint passes and kubeconform `-strict` (Kubernetes 1.33) reports 13/13 local and 11/11 OpenStack-external resources valid. `tests/deploy/test_chart.py` has 28 tests, all passing.
    - **Measured local totals:** 0.75 CPU and 1,344 MiB requests, **2,688 MiB** limits (the estimate was 2.4 GiB; MLflow is now sized from measurements), 3 GiB volumes. All within budget.
    - **Before templating**, the platform was proven end to end in podman, with read-only root filesystems:
      - postgres 16.14 runs as UID 999 and SeaweedFS 4.50 as UID 10001;
      - the bucket is created, and a wrong S3 key is rejected;
      - training is logged into SeaweedFS through MLflow, and all 12 artifacts are stored;
      - after restarting all three containers, the champion, metrics and artifacts survive, and the model downloads through the server;
      - 0 secrets in logs.
    - **Fixes found on the way:**
      - SQLAlchemy needs the explicit `postgresql+psycopg2://` driver.
      - SeaweedFS 4.x needs `WEED_JWT_FILER_SIGNING_KEY`, now an object-store Secret key.
      - MLflow 3's job runner (~1.4 GB) is disabled via `MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false`; the server measured ~400 MB, so it is sized at 384Mi requests and 640Mi limits.
      - The MLflow admin password must be ≥ 12 characters.
    - **Readiness retry:** inference retries the model load from its readiness probe, at most every 30 s while not loaded. A pod started before MLflow or a champion becomes ready without a restart; `tests/serving` covers this.
- [x] 5.3 Add `scripts/local-secrets.sh`, which generates random passwords once and creates the Postgres, S3, MLflow admin, trainer and serving Secrets. Extend `scripts/deploy.sh` to create the same Secrets from CI variables on OpenStack. Verify: `shellcheck` is clean, and a rerun of `local-secrets.sh` doesn't rotate existing Secrets.
  - Note (2026-11-09): shellcheck is clean. **The no-rotation check ran against a fake `kubectl`** (Secrets stored as files), not a real cluster: `tests/scripts/test_local_secrets.py` has 7 tests, all passing. Covered:
    - 5 Secrets with exactly the keys the chart references;
    - 32-character URL-safe random values, all distinct;
    - `s3.json` matching the S3 keys;
    - a rerun keeps every value;
    - no secret value ever on a kubectl command line;
    - `deploy.sh openstack` stops before Helm if any CI secret variable is missing, and otherwise applies all 5 Secrets.

    The test found a real bug, now fixed: `tr </dev/urandom | head` died of SIGPIPE under `pipefail`. The real-cluster run is part of 5.4.
- [ ] 5.4 Run the integration check on the local k3s cluster:
  1. `local-up.sh k3s`, `local-secrets.sh`, `deploy.sh local`, then `run-training.sh local`.
  2. An anonymous `curl` to the MLflow API returns 401.
  3. The run shows the seven metrics and five artifacts.
  4. `champion` points to `wine-quality/1`.
  5. After an inference restart, `/predict` returns `model_version` `wine-quality/1`.
  6. Delete the mlflow, postgres and seaweedfs pods; after they restart, the run, its artifacts and the alias are still there.
  7. Download the model through the server, which exercises the SeaweedFS S3 path.
  8. `kubectl top pods` with the whole stack idle shows memory use within the local budget, recorded in the transcript.

  Verify: the transcript is saved to `docs/epic2-verification.md`. If the local cluster is unreachable from the implementation machine (for example, the k3s kubeconfig is not readable), stop and ask instead of marking the task done.
  - Note (2026-11-10): **left open by owner decision.** No user-readable kubeconfig was available on narses (`/home/asini/.kube/config` absent). The same flow was verified with the real images and the full platform (PostgreSQL, SeaweedFS, MLflow) in podman; see `docs/epic2-verification.md` section 2.
  - Note (2026-11-11, from Epic 3 task 6.2; this archived change is not reopened): steps 1–8 were run on a throwaway **kind v0.33.0 / Kubernetes v1.34.11** cluster, with the Epic 3 code in Deployment mode.
    - **Passed:**
      - anonymous call → 401 (403 through a port-forward on a port other than 5000: MLflow's host check);
      - 7 metrics and 5 artifacts;
      - `champion` → `wine-quality/1`;
      - `/predict` → `wine-quality/1` after a restart;
      - the run, artifacts and alias survive deleting the mlflow, postgres and seaweedfs pods;
      - the model downloads through the server (S3 path);
      - idle memory 865 Mi.
    - **Defect found and fixed in Epic 3:** the bootstrap hook Job needs a writable `/tmp` (the podman runs of section 2 hid this).
    - **Still pending:** the k3s run itself.
    - Evidence: `docs/epic2-verification.md` section 4.
  - Note (2026-11-13, from Epic 3): on the owner's **k3s** v1.36.5, the bootstrap, training, promotion, alias and model download through the server ran in KServe mode (`docs/epic3-verification.md` section E). The anonymous-401 and pod-restart persistence checks were not repeated there, so the box stays unticked. Found there: MLflow was OOMKilled at its 640 Mi limit, fixed in Epic 3 by exec'ing uvicorn instead of keeping the `mlflow server` parent (`scripts/mlflow_server_exec.py`).

## 6. CI and documentation

- [x] 6.1 Add a `build-mlflow-server` job to `.gitlab-ci.yml`, pass `GIT_SHA`/`IMAGE_TAG` build arguments to the trainer build, and render both object-store modes in `chart-lint`. Confirm that no CI job runs training against a cluster. Verify: `check-jsonschema --builtin-schema vendor.gitlab-ci` passes, `gitlab-ci-local --list` shows the new job in `build`, and `grep -n 'python -m training\|train-and-promote' .gitlab-ci.yml` returns nothing.
  - Note (2026-11-09):
    - check-jsonschema passes, and `gitlab-ci-local --list` shows `build-mlflow-server` in `build` (7 jobs).
    - The task's grep returns nothing: no CI job trains.
    - `GIT_SHA`/`IMAGE_TAG` build args were already passed to every build.
    - `chart-lint` renders local (internal SeaweedFS), OpenStack (external S3), and local with external S3.
    - **Install scope:** CI now installs `--extra dev` instead of `--all-extras` (the tests need no server drivers), and `UV_HTTP_TIMEOUT` is set to 120 s.
    - **Local run:** `gitlab-ci-local --container-executable podman --concurrency 1 lint unit-tests chart-lint` passes all three: unit-tests ran 162 tests including the real MLflow server integration tests, 28 helm tests skipped, 98.7 % coverage; chart-lint passes kubeconform 13/11/11 and 28 chart tests.
    - Build and deploy jobs were not run locally (they need the GitLab registry and a cluster).
- [ ] 6.2 Document the work in the README:
  - tracking architecture (proxied artifacts, auth users);
  - how to open the MLflow UI with `port-forward`;
  - the local resource footprint and which components can be switched off;
  - the promotion rules and the `evaluate`/`rollback` commands;
  - the Secrets to create on OpenStack;
  - the external S3 settings.

  Verify: the commands match the ones run in 5.4. Then run `openspec validate add-mlflow-tracking-and-registry` and confirm it passes.
  - Note (2026-11-10): the README is written. Its local-development block was run from a fresh export, and every step passed (`docs/epic2-verification.md` section 1). It stays open because its check compares against the 5.4 cluster commands, which haven't run; the cluster blocks are marked as not yet verified in the README.

## Workflow follow-up

- Milestone check: present the Epic 1 and Epic 2 code structure to the user for validation, and wait before starting Epic 3 (KServe).
- Owner: supply the OpenStack object-store endpoint and bucket credentials for production.
- Archive this change after validation.

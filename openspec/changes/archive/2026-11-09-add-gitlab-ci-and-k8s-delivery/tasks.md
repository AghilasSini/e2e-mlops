# Tasks

## 1. Repository hygiene

- [x] 1.1 Add `.gitignore`: Python, venv, `artifacts/`, `logs/`, `mlruns/`, `*.joblib`, `.env`, coverage outputs. Untrack the generated files with `git rm --cached`: `artifacts/`, `logs/`, `mlruns/`, `**/__pycache__`, `*.egg-info`. Move the seed CSV to `data/raw/winequality-red.csv` with `git mv`. Verify: `git ls-files | grep -E '__pycache__|egg-info|^artifacts/|^logs/|^mlruns/'` is empty, and `data/raw/winequality-red.csv` is tracked with 1,600 lines. (Audit HYG-01, DATA-03; DATA-02 in part, data versioning stays in P5)
- [x] 1.2 Replace the DagsHub token in `research/05_model_evaluation.ipynb` and both tokens in `README.md` with `os.environ[...]` / `<YOUR_TOKEN>` placeholders. Verify: `gitleaks detect --no-git --source .` reports no findings in the working tree. (Audit SEC-03/04, CONT-02; history unchanged)

## 2. Tooling and dependencies

- [x] 2.1 Create `pyproject.toml`: Python `>=3.11,<3.12`, src layout, packages `common`, `data`, `training`, `serving`; optional groups `train`, `serve`, `dev`. Configure `black`, `isort` (profile black), `pytest` and `coverage` (`fail_under = 80`) there, and `flake8` in `.flake8` (max-line-length 100, black-compatible ignores). Generate `uv.lock`. Delete `setup.py`, `requirements.txt` and `template.py`. Verify: `uv sync --frozen --all-extras` succeeds on Python 3.11 in a clean venv, and `pip-audit` on the locked set reports no known vulnerabilities (or each remaining one is listed with a reason). (Audit DEP-01…04, SERV-04, HYG-02, HYG-05)
- [x] 2.2 Rewrite `config/config.yaml`, `params.yaml` and `schema.yaml` for the new layout. `schema.yaml` gets the corrected `chlorides` column with dtype, `nullable` and `min`. `params.yaml` gets the target threshold 6, `test_size` 0.2, `random_state` 42, `alpha`, `l1_ratio`. Add `src/common/config.py`, which loads typed config with environment overrides (`DATA_PATH`, `OUTPUT_DIR`, `MODEL_URI`), and `src/common/logging.py`. Verify: `tests/common/test_config.py` checks that defaults load and that an environment variable overrides a path.

## 3. Data processing (`src/data/`)

- [x] 3.1 Implement `data/schema.py` and `data/validation.py`. Validation returns a report covering missing/extra columns, dtype, nulls and minimums, and raises `SchemaValidationError` on any error. Verify: `tests/data/test_validation.py` covers every scenario of `specs/wine-data-processing` under "Schema enforcement", including a regression test for the old last-column-wins bug. (Audit PIPE-01, PIPE-04)
- [x] 3.2 Implement `data/features.py`, which builds the `is_good` target and the feature matrix with snake_case feature names, and `data/split.py`, which does a seeded, stratified split. Verify: tests cover the threshold boundary (5→0, 6→1, 7→1), byte-identical reruns, and class ratios within 2 percentage points. (Audit PIPE-03)

## 4. Training (`src/training/`)

- [x] 4.1 Implement `training/train.py`, a pipeline of StandardScaler and elastic-net LogisticRegression with `random_state`, and `training/evaluate.py`, which writes `roc_auc`, `f1`, `accuracy` and `n_test` to `metrics.json`. Add the entrypoint `python -m training`: validate → split → fit → evaluate → write `model.joblib` and `model_version`, the latter set to the UTC timestamp plus the git SHA, or `MODEL_VERSION` when set. Any failed step exits non-zero. Verify: `tests/training/` covers successful output files, no artifacts on invalid data, metrics within [0, 1], and identical predictions across two runs. (Audit PIPE-02, PIPE-06, PIPE-07)
- [x] 4.2 Delete `src/mlProject/` and `main.py`. Verify: `grep -rn mlProject --include='*.py' --include='*.toml' --include='*.yaml' .` returns nothing outside `docs/` and `openspec/`, and `uv run python -m training` on the seed data exits 0 with ROC-AUC > 0.75 recorded in the task note.
  - Note (2026-11-09, commit 22d8919 + working tree): `python -m training` exits 0 with ROC-AUC 0.824, F1 0.754, accuracy 0.744, n_test 320. The grep is clean once `app.py` is deleted in 5.2.

## 5. Inference service (`src/serving/`)

- [x] 5.1 Implement `serving/schemas.py`: pydantic v2 `WineFeatures` with `extra="forbid"` and `ge=0`, plus `PredictRequest` (single or `instances`) and `PredictResponse`. Implement `serving/model.py`, which loads once from `MODEL_URI` and reports readiness. Verify: `tests/serving/test_schemas.py` covers valid input, unknown field, non-numeric value and negative value.
- [x] 5.2 Implement `serving/app.py` (FastAPI):
  - `POST /predict`, plus 503 when no model is loaded;
  - `/health/live` and `/health/ready`;
  - a generic 500 handler that logs the exception;
  - `GET /`, the Jinja form, with a result page, adapting `templates/` and `static/`.

  Delete `app.py`. Verify: `tests/serving/test_api.py` uses FastAPI's TestClient against a model fixture and covers every scenario in `specs/prediction-api`, including `/train` → 404, a 422 listing the fields for `{"alcohol": "abc"}`, and the model loaded at most once across 100 calls. (Audit SEC-05, SERV-01…03, PIPE-08)
- [x] 5.3 Run the whole suite. Verify: `uv run pytest --cov=src` passes with coverage ≥ 80 %, and `uv run flake8 src tests && uv run black --check . && uv run isort --check-only .` are clean. (Audit PIPE-05, HYG-04)

## 6. Container images

- [x] 6.1 Write the multi-stage `Dockerfile`, with stages `builder`, `trainer` and `inference`:
  - base `python:3.11-slim-bookworm`, user UID 10001;
  - `uv sync --frozen --no-dev` with the per-target extras;
  - `HEALTHCHECK` on `/health/live` for inference.

  Write a `.dockerignore`. Verify: `hadolint Dockerfile` reports no warnings; both targets build with `buildah` or `docker`; `id -u` in each image is 10001; the inference container answers `/health/live` with 200; each image is ≤ 500 MB; and `docker run … find / -name '*.ipynb' -path '*research*'` finds nothing. (Audit CONT-01…05)
  - Note (2026-11-09): built with rootless podman 4.9.3, because Docker isn't accessible on the implementation machine. Results: hadolint clean; trainer 392 MB, inference 411 MB; UID 10001 in both; no `.ipynb`, research files, tests or `.git` in either image. The trainer runs with `--read-only` and writes its model. Inference `/health/live`, `/health/ready` and `/predict` return 200, `/train` returns 404, and the container healthcheck reports healthy.

## 7. Helm chart and deploy scripts

- [x] 7.1 Create `deploy/helm/e2e-mlops/`:
  - `Chart.yaml` and `values.yaml`;
  - `values-local.yaml` (k3s and Minikube; budgeted resources per design.md decision 11) and `values-openstack.yaml`;
  - an `enabled` flag per component;
  - templates for the inference Deployment, Service and Ingress, the training Job (`training.enabled`, with pod affinity to inference), the model PVC and the ServiceAccount;
  - security contexts (`runAsNonRoot`, `readOnlyRootFilesystem`, dropped capabilities), resources and probes.

  Verify: `helm lint` passes with both values files; `helm template … | kubeconform -strict -summary` reports 0 invalid; and `tests/deploy/test_chart.py` asserts three things: every container has `runAsNonRoot` and resource limits; the local rendering with everything enabled is within the budget (CPU requests ≤ 1, memory requests ≤ 2 GiB, memory limits ≤ 3 GiB, volumes ≤ 5 GiB, 1 replica); and `training.enabled=false` removes the Job. The test renders with `helm template` and is skipped when helm is absent.
  - Note (2026-11-09): helm v3.22.1 lint passes for both values files, and kubeconform v0.7.0 (`-strict`, Kubernetes 1.33.0) reports 5/5 local and 6/6 OpenStack resources valid. The 21 chart tests pass. Local totals are 0.35 CPU and 576 MiB requested, 1,152 MiB memory limits, and a 1 GiB volume. The budget test was checked to fail with 2 GiB requested. Training is a suspended CronJob template (design.md decision 8, amended).
- [ ] 7.2 Write the three scripts:
  - `scripts/deploy.sh <local|openstack>`: creates the namespace and the `gitlab-registry` pull secret from environment variables, then runs `helm upgrade --install --atomic --wait --timeout 5m` with the `IMAGE_TAG` given;
  - `scripts/run-training.sh <target>`: launches the training Job and waits for completion, then restarts the inference rollout;
  - `scripts/local-up.sh <k3s|minikube>`: checks that the cluster is reachable, starting Minikube with `--cpus 2 --memory 4096` if asked. It builds the images with podman or docker and loads them into the cluster (`k3s ctr images import` or `minikube image load`) with tag `local`.

  Verify: `shellcheck scripts/*.sh` is clean. On the k3s cluster of the implementation machine, `local-up.sh k3s` → `deploy.sh local` → `run-training.sh local` leads to `/health/ready` 200 and a valid `/predict` response, and `kubectl top pods` stays within the budget. The Minikube path is checked only if Minikube is installed; otherwise it is recorded as not verified. If the k3s kubeconfig is not readable by the user, stop and ask rather than marking the check done.
  - Note (2026-11-09): **left open by owner decision.** All three scripts are written and pass shellcheck. The owner built and imported the images into k3s. The cluster run itself was not done: no user-readable kubeconfig was available to the implementation session (`/home/asini/.kube/config` absent on host narses after four attempts). The README carries a "pending" marker and the k3s access steps.
  - Note (2026-11-11, from Epic 3 task 6.2; this archived change is not reopened): the same sequence was run on a throwaway **kind v0.33.0 / Kubernetes v1.34.11** cluster (rootless podman), with the Epic 3 code in Deployment mode, which is the Epic 1–2 layout. The images were loaded with `kind load image-archive` instead of `local-up.sh`.
    - **Passed:** `deploy.sh local` (bootstrap, training, final upgrade); `/health/ready` 200; a valid `/predict` before and after a restart; `kubectl top`: 865 Mi in use.
    - **Bug found and fixed in Epic 3:** the tracking bootstrap hook had no writable `/tmp`.
    - **Still pending:** the k3s run itself (the kubeconfig is still root-only) and Minikube (not installed).
    - Evidence: `docs/epic1-verification.md` section 3.
  - Note (2026-11-13, from Epic 3): `local-up.sh` was run on **Minikube** v1.39.0 and on the owner's **k3s** v1.36.5, followed by `deploy.sh local` → train → predict, in KServe mode (`docs/epic3-verification.md` sections C and E). On k3s, `local-up.sh k3s` no longer uses `sudo k3s ctr images import`, which never reached the kubelet: it pushes the images to a rootless local registry on `localhost:5000`. The box stays unticked: the Deployment mode itself was not rerun on k3s.

## 8. GitLab CI/CD

- [x] 8.1 Write `.gitlab-ci.yml` per design.md decision 10:
  - jobs `lint`, `unit-tests` (JUnit and Cobertura artifacts), `chart-lint`, `build-trainer`, `build-inference` (Buildah, SHA tag, plus `latest` on the default branch, no push on merge requests), `deploy-local` (only when `LOCAL_DEPLOY_ENABLED=true`, manual, `allow_failure: true`) and `deploy-openstack` (`when: manual`, `environment: production`);
  - workflow rules that skip docs-only changes;
  - every image pinned to a version.

  Verify: `check-jsonschema --builtin-schema vendor.gitlab-ci .gitlab-ci.yml` passes; `gitlab-ci-local --list` shows the 6 jobs in stages `test → build → deploy` (7 with `LOCAL_DEPLOY_ENABLED=true`); and `gitlab-ci-local lint unit-tests chart-lint` runs green locally where Docker is available, otherwise it is recorded as not run. (Audit CICD-01, CICD-04…08, migration requirements R1–R9)
  - Note (2026-11-09): check-jsonschema 0.39.0 (`vendor.gitlab-ci`) passes. gitlab-ci-local 4.77.0 `--list` shows 6 jobs, or 7 with `LOCAL_DEPLOY_ENABLED=true` (`deploy-local`: manual, allow_failure). `gitlab-ci-local --container-executable podman lint unit-tests chart-lint` passes all three: 100 passed and 21 helm tests skipped in unit-tests at 99 % coverage; chart-lint passes lint, kubeconform 5/5 and 6/6, and 21 tests. The build and deploy jobs were not run locally, because they need the GitLab registry and a cluster. Tool downloads in CI are checksum-verified by `scripts/fetch_tool.py`.
- [x] 8.2 Delete `.github/workflows/main.yaml`. Verify: `.github/` contains no workflow, and `grep -rniE 'ecr|ec2|aws' --include='*.y*ml' --include='*.sh' --include='Dockerfile' .` returns nothing outside `docs/` and `openspec/`. (Audit CICD-02/03; CICD-09…11 dropped with the workflow)
  - Note (2026-11-09): `.github/` is gone. The literal grep matches substrings such as "s**ecr**et" and "R**ecr**eate", so it was run with `-w` instead: `grep -rniwE 'ecr|ec2|aws|amazonaws'` over yml, sh, Dockerfile, py and toml returns 0 files outside `docs/` and `openspec/`. README AWS text is removed in 9.1.

## 9. Documentation and integration check

- [x] 9.1 Rewrite `README.md`: architecture overview (code pipeline vs runtime), local development (`uv sync`, `pytest`, `python -m training`, `uvicorn`), a local quick start for k3s and Minikube with the machine requirements (4 cores, 8 GB RAM, 20 GB disk) and how to switch components off, GitLab setup (registry, deploy token, CI variables, runner tags, the unprivileged Buildah requirement), and the API contract with `curl` examples. Update `SECURITY.md` with a real reporting contact and the secrets policy. Drop the AWS account IDs and the FullAccess IAM advice (Audit HYG-03, SEC-06…08). Verify: each README command block was run during 5.3, 6.1 and 7.2, and any block not run is marked as such.
  - Note (2026-11-09): these README blocks were run: local development (5.3 and 9.2), configuration, the API `curl` (6.1 container and 9.2), and the image builds (6.1). Three blocks are marked in the README as not verified: the k3s quick start (pending 7.2 cluster access), Minikube (not installed) and the manual OpenStack deploy (no cluster). The Buildah runner requirement is marked unverified on the target GitLab.
- [x] 9.2 End-to-end check from a fresh clone: `uv sync --frozen --all-extras`, then lint, tests, `python -m training`, then start `uvicorn serving.app:app` and run the README `curl` against `/predict`. Verify: every step exits 0 and the transcript is saved to `docs/epic1-verification.md`. Also confirm `openspec validate add-gitlab-ci-and-k8s-delivery` passes.
  - Note (2026-11-09): nothing is committed yet, so a "fresh clone" would miss the changes. The check instead runs on a fresh export of every file Git would commit (`git ls-files -co --exclude-standard`, 106 files) into an empty directory, with a new venv. All 12 steps pass: uv sync, flake8, black, isort, 100 tests (21 helm tests skipped) at 99.18 % coverage, training (ROC-AUC 0.824), readiness, README curl, 422 on bad input, 404 on `/train`, and `openspec validate`. Transcript: `docs/epic1-verification.md`, section 1.

## Workflow follow-up

- Owner: revoke the credentials listed in the audit's *Immediate owner actions* before pushing this history to GitLab.
- Owner: create the GitLab project, deploy token, CI variables and runners, then run the first pipeline.
- Milestone check: present the Epic 1 and Epic 2 code structure for validation before Epic 3.
- Archive this change before applying `add-mlflow-tracking-and-registry`.

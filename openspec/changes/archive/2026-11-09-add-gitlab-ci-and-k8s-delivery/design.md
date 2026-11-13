# Design

## Context

The current state is recorded in `docs/audit/AUDIT.md`, audited at commit `22d8919`:

- **Pipeline:** a 5-stage tutorial pipeline (`src/mlProject`, `main.py`) training an ElasticNet regressor on the tracked `winequality-red.csv` (1,599 rows).
- **Serving:** a Flask app that reloads the model on every request.
- **Delivery:** a GitHub Actions → ECR → EC2 workflow.

Nothing is tested. Training doesn't run on Python 3.8 or 3.12 as pinned.

Constraints from the upgrade spec:

- Open-source tooling only, self-hosted.
- A single-node k3s or Minikube cluster on a **modest personal machine** locally; OpenStack-managed Kubernetes (e.g. Magnum) in production. The reference machine has 4 CPU cores, 8 GB RAM and 20 GB of free disk, and must also run an IDE and a browser.
- The code pipeline (GitLab CI) is strictly separate from the data/runtime pipeline (Airflow and Kubernetes, later epics).
- Linting uses `flake8`, `black` and `isort`; tests use `pytest`.

## Goals / Non-Goals

**Goals:**
- A codebase whose module boundaries match later epics: `training/` for Epic 2, `serving/` behind KServe in Epic 3, `data/` reused by Airflow DAGs in Epic 4 and by Evidently in Epic 5.
- CI that a teacher or student can read top to bottom, with only open-source images.
- One Helm chart that later epics extend instead of replace.

**Non-Goals:**
- MLflow tracking and registry. Epic 2 does this; until then the model is a file on a volume.
- KServe, Airflow, Evidently, Prometheus and Grafana.
- Rewriting Git history to purge the leaked credentials (owner decision, audit P0).
- Provisioning the GitLab instance, the runners or the OpenStack cluster. These are prerequisites.

## Decisions

### 1. Package layout follows the spec paths
Top-level packages live under `src/` (src layout), installed from `pyproject.toml`:
- `common` holds the config loader and logging.
- `data` holds the schema, validation, features and split.
- `training` holds training and evaluation.
- `serving` holds the API and schemas.

Imports read `from data.validation import validate`.

*Alternative:* a single namespace package (`src/wineops/...`). It avoids generic top-level names such as `data`, but contradicts the paths the upgrade spec names (`src/training/`, `src/models/registry_manager.py`, `src/monitoring/`). We follow the spec. A test asserts that the packages import from the installed distribution, not from shadowing modules.

### 2. Classification with an elastic-net logistic regression
- **Target:** `is_good = quality >= 6`. This gives 53 % positives on the seed data, a balanced split.
- **Model:** a scikit-learn `Pipeline(StandardScaler, LogisticRegression(penalty="elasticnet", solver="saga", C=1/alpha, l1_ratio, random_state))`.

This keeps the two existing hyper-parameters, so `params.yaml` stays recognisable. Scaling is needed for `saga` to converge.

*Alternative:* RandomForest. It is stronger, but it changes the teaching narrative and the hyper-parameters. It can be added as a configurable estimator later.

### 3. Schema as data, validation in code
`schema.yaml` declares each column with its dtype, nullability and a `min` of 0. Validation is a pure function that returns a report listing every problem, and raises `SchemaValidationError` when the report contains any error. The training entrypoint does not catch it. This fixes PIPE-01/02.

*Alternative:* pandera or Great Expectations. They are heavier than needed for 12 columns. Evidently in Epic 5 covers distribution checks.

### 4. FastAPI for the inference service
FastAPI (MIT) with pydantic v2 models, served by uvicorn. It gives:
- schema validation with 422 field errors for free;
- `extra="forbid"` for unknown fields;
- an OpenAPI document that tests and later clients can use.

The HTML form is a Jinja2 template reusing `templates/` and `static/`. It posts to the same handler.

The model is loaded once in the app lifespan from `MODEL_URI`. In this epic that is a local path; Epic 2 switches it to `models:/wine-quality@champion` without an API change.

*Alternative:* keep Flask and add pydantic manually. That means more glue code and no OpenAPI.

In Epic 3, KServe serves the model with its MLflow runtime. This service then becomes the browser-facing gateway, so its contract stays.

### 5. Python 3.11, `uv` for locking
- **Python:** 3.11 is supported by the later stack: Airflow, MLServer (KServe's MLflow runtime), Evidently and MLflow.
- **Dependencies:** `pyproject.toml` declares runtime dependencies with compatible ranges, plus optional groups `train`, `serve` and `dev`.
- **Lock:** `uv lock` (Apache-2.0/MIT) pins everything. CI and Docker use `uv sync --frozen`.

*Alternative:* `pip-tools`. It is equivalent but slower, and needs one compiled file per group.

### 6. One multi-stage Dockerfile
- **Base:** `python:3.11-slim-bookworm`.
- **`builder` stage:** creates a venv with uv.
- **`trainer` and `inference` targets:** each copies only the venv and `src/`.
- **User:** non-root UID 10001.
- **Inference:** adds a `HEALTHCHECK` and runs `uvicorn` with no reload.

A `.dockerignore` works as an allow-list: only `pyproject.toml`, `uv.lock`, `src/`, the three config files and the seed CSV reach the build context. `.git`, `research/`, tests and docs never do. This fixes CONT-01…05, including the tokens baked into images (CONT-02).

*Amended during implementation:* the 100 kB seed CSV is copied into the **trainer** image only, as the default `DATA_PATH`. Nothing else provides data to a fresh local cluster before Epic 4's extraction step. A mounted dataset still overrides it through `DATA_PATH`. Third-party `tests/` directories are pruned from the virtualenv, which saves about 160 MB, so both images stay under 500 MB.

*Alternative:* one Dockerfile per image. That duplicates the builder stage.

### 7. Image builds with Buildah, not Docker-in-Docker
- The CI `build` jobs use `quay.io/buildah/stable` (Apache-2.0) with `--storage-driver=vfs` and `--isolation=chroot`. This runs on runners without privileged mode.
- Images are pushed to `$CI_REGISTRY_IMAGE/{trainer,inference}`.
- Tags: `$CI_COMMIT_SHORT_SHA` always, plus `latest` on the default branch only. Deploys use the SHA tag (fixes CICD-05).

*Alternatives:*
- `docker:dind` requires privileged runners.
- Kaniko's upstream project was archived in 2025.

### 8. Helm chart `deploy/helm/e2e-mlops`
**Templates:**
- inference `Deployment`, `Service` and optional `Ingress`;
- training `Job`, rendered only when `training.enabled=true`;
- model `PersistentVolumeClaim`;
- `ServiceAccount`.

**Values:** `values.yaml` holds the defaults, plus two environment files:
- `values-local.yaml`: one replica, NodePort or port-forward, the cluster's default storage class (`local-path` on k3s, `standard` on Minikube), small volumes and the budgeted resources of decision 11. It serves both k3s and Minikube.
- `values-openstack.yaml`: Ingress, Cinder storage class, 2 replicas.

Every component has an `enabled` flag, so a student can run a subset.

**Deploy:** `helm upgrade --install --atomic --wait --timeout 5m`. This gives rollback on a failed release.

**Validation:** `helm lint` and `kubeconform` run in the `test` stage.

*Amended during implementation:*
- **Training template.** Training is rendered as a **suspended CronJob** that serves as a job template. `scripts/run-training.sh` starts runs with `kubectl create job --from=cronjob/...`. A plain Job inside the release would break `helm upgrade`, because Job specs are immutable. It would also tie training to code deploys. Setting `training.suspend=false` turns on scheduled runs until Airflow (Epic 4) takes over.
- **First install.** Inference is correctly not ready until a model exists, so `--atomic --wait` would roll back the very first install. On a release that doesn't exist yet, `scripts/deploy.sh` first installs with `inference.replicas=0`, runs one training Job, then performs the normal `--atomic --wait` upgrade. Every later deploy is the plain `--atomic --wait` upgrade.
- **Shared model volume.** While the ReadWriteOnce model PVC exists, inference uses `strategy: Recreate` and a required self-affinity, so the 2 OpenStack replicas share one node. The training pod requires affinity to inference pods whenever inference runs.

*Alternative:* plain manifests with Kustomize overlays. That is equally open source, but later epics (KServe, MLflow, monitoring) are easier to toggle as chart values.

### 9. Model hand-off in this epic: shared volume (temporary)
The training Job writes `model.joblib`, `metrics.json` and a `model_version` file to the model PVC. The inference pod mounts the PVC read-only.

**The catch:** the PVC is `ReadWriteOnce`. On the local single-node cluster this just works. On OpenStack, Cinder volumes attach to one node at a time, so the training Job gets a pod affinity that places it on the inference pod's node.

This is explicitly temporary. Epic 2 replaces it with the MLflow registry and removes the PVC.

*Alternatives:*
- Bake the model into the image. That couples model and code releases, against the "strict decoupling" principle.
- Use an RWX volume. Not available by default on either target.

### 10. CI layout
```
stages: [test, build, deploy]
lint            (test)   flake8, black --check, isort --check-only
unit-tests      (test)   pytest --cov=src --cov-fail-under=80, junit + cobertura reports
chart-lint      (test)   helm lint + kubeconform (both values files)
build-trainer   (build)  buildah build --target trainer   → push SHA tag
build-inference (build)  buildah build --target inference → push SHA tag
deploy-local    (deploy) default branch, only if LOCAL_DEPLOY_ENABLED=true, runner tag `local-cluster`, manual, allow_failure
deploy-openstack(deploy) default branch, when: manual, environment: production
```

- Merge-request pipelines run `test` and `build`, but don't push (`--no-push` dry build).
- `rules:` skip the whole pipeline for documentation-only changes (`docs/**`, `*.md`).
- `scripts/deploy.sh <target>` takes `KUBECONFIG` from a file-type CI variable. It creates or updates the `gitlab-registry` pull secret from `CI_DEPLOY_USER`/`CI_DEPLOY_PASSWORD`, then runs Helm. It is also runnable by hand: `scripts/deploy.sh local` uses the current kubectl context.

The CI runs Helm, kubectl and kubeconform as pinned versions of upstream images, not vendor-hosted actions.

### 11. Modest-machine profile (applies to every epic)
**Local cluster.**
- **k3s** is recommended on Linux. It runs natively, idles at about 0.5 GB, and ships `local-path` storage and Traefik.
- **Minikube** is supported for macOS and Windows: `minikube start --cpus 2 --memory 4096 --disk-size 20g`. Its VM or container adds about 1 GB.
- The chart uses only standard APIs, so the same `values-local.yaml` serves both.
- k3d (k3s in Docker) also works but isn't documented.

**Budget for the chart's workloads in the local profile.** A test enforces it by rendering `values-local.yaml` with everything that exists enabled:
- sum of CPU requests ≤ 1 CPU;
- sum of memory requests ≤ 2 GiB;
- sum of memory limits ≤ 3 GiB;
- volumes ≤ 5 GiB in total;
- one replica per workload.

Adding the cluster's own overhead gives under 4 GB, leaving the rest of an 8 GB machine to the IDE and browser.

**Epic 1 sizing:**

| Workload | Request | Limit |
|---|---|---|
| inference | 100m / 192Mi | 500m / 384Mi |
| training Job | 250m / 384Mi | 1 CPU / 768Mi |

The training Job only exists while it runs. The dataset has 1,600 rows, so training takes seconds.

**Images without a registry.**
- `scripts/local-up.sh` builds with whichever of `podman` or `docker` is present.
- It then loads the images into the cluster: `k3s ctr images import` (needs sudo) on k3s, `minikube image load` on Minikube.
- The chart uses `imagePullPolicy: IfNotPresent` with a `local` tag in the local profile.

This avoids running a registry on the laptop. Pulling from the GitLab registry still works when a pull secret exists.

**Image size.** The images are kept lean to save disk and memory: slim base, no dev or notebook dependencies, no compilers in the final stages. Target ≤ 500 MB per image, checked in task 6.1.

**Out of the laptop.**
- The GitLab server (GitLab CE needs ≥ 4 GB on its own) and the CI jobs run on the GitLab instance's runners, not on the personal machine.
- The laptop runs only the cluster and, optionally, a runner for `deploy-local`.

**Guidance for later epics.** These are recorded here so each epic's own proposal starts from them. The aim is that the whole stack fits in about 6 GB of cluster memory on a 16 GB machine, and each epic alone fits on an 8 GB machine with the others switched off.
- **Epic 3:** KServe in **RawDeployment** mode locally, with no Knative or Istio. Scale-to-zero (Story 3.1) is then available only on OpenStack in Serverless mode. That epic must confirm this trade-off with the user.
- **Epic 4:** Airflow with LocalExecutor, reusing the Epic 2 PostgreSQL, with one scheduler and one API/web pod.
- **Epics 5–6:** Evidently runs as a batch Job. Prometheus runs alone (not the full kube-prometheus-stack) with 2-day retention, plus a single Grafana and a Pushgateway.

*Alternative:* plan for a full multi-node cluster and leave students to cope. Rejected: the project is used for teaching on personal machines.

### 12. Hygiene in the same change
These steps go in this change, not a separate one, because restructuring touches most files anyway:
- Untrack `artifacts/`, `logs/`, `mlruns/`, `__pycache__/` and `*.egg-info` (`git rm --cached`).
- Add a `.gitignore`.
- Move the seed CSV to `data/raw/`.
- Strip the DagsHub tokens from the notebook and README, replacing them with environment-variable placeholders.

History still contains the secrets. The audit's owner actions are unchanged.

## Risks / Trade-offs

- **[Risk]** Generic top-level package names (`data`, `models`) could clash with a third-party package. → **Mitigation:** an import-origin test; rename to a namespace package if it ever fires.
- **[Risk]** Buildah in an unprivileged container needs user namespaces or `vfs` storage, which is slow. → **Mitigation:** use `vfs` with layer caching through `--layers` and `--cache-to $CI_REGISTRY_IMAGE/cache`. Document the runner settings.
- **[Risk]** A runner elsewhere can't reach a cluster on a personal machine, and a job waiting for a missing runner would hang the pipeline. → **Mitigation:** `deploy-local` exists only when `LOCAL_DEPLOY_ENABLED=true`, is manual, and is allowed to fail. Running `scripts/deploy.sh local` by hand is the normal path.
- **[Risk]** Later epics may not fit the modest machine: Knative and Istio for KServe alone use about 2–3 GB, and Airflow and Prometheus are not small either. → **Mitigation:** decision 11 sets a budget for each later epic. Every component can be switched off, so a student can run only the epic they are working on.
- **[Risk]** Switching to a classifier and a JSON API breaks any existing client of `/predict`. → **Mitigation:** none needed. The only client is the bundled HTML form, which is updated here.
- **[Trade-off]** The model PVC is throw-away work, replaced in Epic 2. We accept it so that Epic 1 can deliver an end-to-end deployable on its own.
- **[Risk]** Pushing this repository to GitLab copies the leaked AWS and DagsHub credentials in its history. → **Mitigation:** revoke them first (audit P0). Optionally push a history-rewritten copy; that is an owner decision.

## Migration Plan

1. **Owner:** create the GitLab project (the code then lives on GitLab) and enable the Container Registry. Add a deploy token, a `KUBECONFIG_OPENSTACK` file variable, and a protected `main` branch.
2. Merge this change. The first pipeline builds the images.
3. **Local:** `scripts/local-up.sh k3s` (or `minikube`), which checks the cluster, builds the images and loads them. Then `scripts/deploy.sh local`, then `scripts/run-training.sh local`, then call `/predict`.
4. **OpenStack:** trigger `deploy-openstack` manually.
5. **Rollback:** `helm rollback e2e-mlops` on the cluster, or rerun the deploy job of the previous pipeline (immutable SHA tags make this exact).
6. Once GitLab CI is green, archive or delete the GitHub repository. Removing `.github/` stops GitHub Actions in any case.

## Open Questions

- Which GitLab instance hosts the project: `git.univ-lemans.fr` or another? The pipeline only uses GitLab's predefined variables, so the answer only changes the README and the runner registration steps.
- Ingress host name and TLS for the OpenStack environment. These are values in `values-openstack.yaml` and can be filled in at deploy time.

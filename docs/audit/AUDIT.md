# Repository Audit — `e2e-mlops` (Phase 1)

| | |
|---|---|
| Audited commit | `22d89191bfb130bccca0dd01acb8c8393c50ca9d` (`main`, 2023-11-07 14:02 +0100, "update dockerfile : errors are fixed") |
| Remote | `git@github.com:AghilasSini/e2e-mlops.git`, **public** on GitHub, 1 fork (GitHub API, 2026-11-09) |
| Audit date | 2026-11-09 |
| OpenSpec change | `openspec/changes/audit-repository` |
| Scope | Read-only. This phase changes no code, configuration, CI or Git history. |

---

## Summary

**Headline risk:** credentials are exposed in the repository's history, and the repository is public. They include a long-lived AWS IAM access key, the private SSH key for the EC2 host, and two DagsHub tokens. Removing them from the files is not enough. The credentials must be **revoked and rotated now** (see *Immediate owner actions*).

Apart from that, the project is a 2023 tutorial snapshot that no longer reproduces:

- On the declared Python 3.8 the training pipeline **crashes** at the MLflow step (DEP-01). On Python 3.12 the dependencies **don't install** (DEP-03).
- The Docker base image is end-of-life, and its apt sources now return 404 (CONT-01).
- Data validation **passes on data that doesn't match the schema** (PIPE-01).
- The CI "lint" and "test" steps only `echo` (CICD-01).
- There are no automated tests.

**Findings by severity** (52 in total):

| Critical | High | Medium | Low | Info |
|---:|---:|---:|---:|---:|
| 3 | 13 | 19 | 12 | 5 |

**Top 5 actions:**
1. Revoke the AWS access key and the EC2 key pair, rotate the DagsHub token, and purge the existing ECR images (SEC-01…03, CONT-02). These are owner actions; do them now.
2. Remove secrets from tracked files and add `.gitignore` / `.dockerignore` (P1).
3. Fix data validation and stop swallowing stage failures, under a first `pytest` suite (P2).
4. Pin dependencies and move to a supported Python, MLflow and base image (P3).
5. Replace GitHub Actions with an open-source CI that runs real lint and tests and deploys immutable image tags (P4).

---

## Immediate owner actions

These do not wait for any phase or for this change to be archived. Everything below has been public since the dates shown.

| # | Action | Findings |
|---|---|---|
| 1 | **Deactivate and delete the IAM access key** `AKIA…` of IAM user `mlproj-user` (committed 2023-11-06 in `f23ab77`). Review CloudTrail for use since that date. Per README L112–114 the user had `AmazonEC2FullAccess` and `AmazonEC2ContainerRegistryFullAccess`. | SEC-01 |
| 2 | **Delete the EC2 key pair `mlproj`** and remove its public key from `~/.ssh/authorized_keys` on any instance launched with it. The private key is still served at `github.com/AghilasSini/e2e-mlops/blob/f23ab77/aws/mlproj.pem` (HTTP 200). | SEC-02 |
| 3 | **Revoke the DagsHub token** `c1f4…` (user `AghilasSini`). It is still at HEAD in `research/05_model_evaluation.ipynb:33`. | SEC-03 |
| 4 | Remove the third-party DagsHub token `6824…` (user `entbappy`, copied from the upstream tutorial) from `README.md`. Consider telling the upstream author that it is public. | SEC-04 |
| 5 | **Delete the existing `mlproj` images in ECR.** They contain the notebook token (see CONT-02). | CONT-02 |
| 6 | Decide whether to rewrite Git history (`git filter-repo`) and ask GitHub Support to purge cached views. A rewrite does **not** affect the existing fork or clones, so rotation (steps 1–3) is what actually closes the exposure. | SEC-01…04 |

---

## Method & tools

**Approach.** Manual code review of every tracked source, config and CI file, plus tools run from an isolated scratch environment. No tool was added to the project. Reproducibility runs used a fresh `git clone` of the audited commit in a scratch directory, `MLFLOW_TRACKING_URI=file:./mlruns`, and **no real credentials**. The working tree of this repository was not modified, apart from the new `docs/audit/` files.

| Tool | Version | Purpose | Evidence |
|---|---|---|---|
| gitleaks | 8.18.4 | Secrets across all history (`--log-opts=--all --redact`) | `evidence/secrets.txt` |
| `git log -p` + grep | — | Notebook JSON secrets that gitleaks misses | `evidence/secrets.txt` |
| pip-audit | 2.10.1 | Known vulnerabilities in the environment resolved from `requirements.txt` | `evidence/pip-audit.txt`, `evidence/freeze-py38.txt` |
| ruff | 0.17.4 | Static analysis (`E,F,B,S` rules) | `evidence/ruff.txt` |
| hadolint | 2.12.0 | Dockerfile lint + base-image apt source check | `evidence/hadolint.txt` |
| actionlint | 1.7.1 | GitHub Actions workflow lint | `evidence/actionlint.txt` |
| uv-managed CPython | 3.8.20 / 3.12.3 | Install, train and serve reproduction | `evidence/repro-py38.log`, `evidence/repro-py312.log` |
| Python script | — | Data-validation bug reproduction | `evidence/validation-bug.txt` |

**Limitations.**
- The Docker daemon was not accessible to the auditor (socket permission denied), so `docker build` was **not executed**. The image build result is inferred (see CONT-01).
- Live AWS, DagsHub and EC2 state cannot be checked from the repository. Claims about it are marked *unverifiable from repo*.

**Severity scale** (impact × exploitability):

| Severity | Meaning |
|---|---|
| Critical | Credential exposure, or remote code/command execution |
| High | Wrong results shipped silently, or a build/deploy/run that fails |
| Medium | Hurts reproducibility, maintainability or defence-in-depth |
| Low | Cosmetic, documentation, minor code quality |
| Info | Context only, or superseded by a decided migration |

**Evidence labels.** **[Observed]** means seen in a tool run or a reproduction. **[Inferred]** means deduced from the code but not executed.

---

## Baseline

- 144 tracked files: `mlruns/` 63, `src/` 46 (incl. 21 `.pyc` and 4 `egg-info`), `artifacts/` 7, `static/` 6, `research/` 6 notebooks, `templates/` 2, plus root files. There is no `.gitignore` and no `.dockerignore`.
- 11 commits, 2023-10-25 → 2023-11-07, single branch `main`. No activity for 3 years.
- Working tree before the audit: all 63 tracked `mlruns/` files are deleted (not staged). `openspec/` and `.claude/` are untracked.
- Stack: Python 3.8, scikit-learn ElasticNet on the red wine-quality dataset, MLflow 2.2.2 (DagsHub), Flask app, Docker → AWS ECR → self-hosted EC2 via GitHub Actions.

---

## 1. Repository hygiene, packaging & documentation

- **HYG-01 (Medium) — No `.gitignore`; build outputs are tracked.** **[Observed]** `git ls-files` lists 21 `__pycache__/*.pyc`, 4 `src/mlProject.egg-info/*` and `logs/running_logs.log`. They create noisy diffs and leak local paths. Committing outputs this way is also what led to the AWS keys being committed (SEC-01). *Fix:* add `.gitignore` and untrack these files (`git rm --cached`).
- **HYG-02 (Low) — Wrong packaging metadata.** `setup.py:24,27` point to `git.univ-lemans.fr`, but the remote is GitHub. The typo `long_destription_content` (`setup.py:23`) means the long-description content type is never set. Version is `0.0.0`. *Fix:* correct the metadata, or move to `pyproject.toml`.
- **HYG-03 (Low) — README is the upstream tutorial's.** The clone URL points to `entbappy/End-to-end-Machine-Learning-Project-with-MLflow` (`README.md:24`), and the MLflow section uses the upstream DagsHub project (L66–79). It contains placeholders ("open up you local host and port", L50) and documents neither tests nor the `/train` endpoint. *Fix:* rewrite it for this project.
- **HYG-04 (Low) — 33 static-analysis issues.** **[Observed]** `evidence/ruff.txt` reports unused imports (×20), duplicate `logger` imports in every `stage_0*` file, star imports (`F403/F405`), unused variables, and `raise` without `from` (`utils/common.py:34`). *Fix:* add ruff to the new CI (P4) and clean up.
- **HYG-05 (Info) — `template.py` is a one-off scaffolding script** that is still tracked. Keep it as teaching material or remove it.

## 2. Secrets & security

Full redacted evidence: `docs/audit/evidence/secrets.txt`. The repository is public, so everything in history is public.

- **SEC-01 (Critical) — AWS IAM access key committed.** **[Observed]** `aws/mlproj-user_accessKeys.csv` (header `Access key ID,Secret access key`, key id `AKIA…`) was added in `f23ab77` (2023-11-06, "cicd added") and deleted in `c854570` the same day. It is still in history. Per README L112–114 the IAM user had *FullAccess* to EC2 and ECR. Whether the key is still active is *unverifiable from repo*. *Fix:* owner action 1.
- **SEC-02 (Critical) — EC2 SSH private key committed.** **[Observed]** `aws/mlproj.pem` (`-----BEGIN RSA PRIVATE KEY-----`) has the same lifecycle as SEC-01. It is still downloadable from GitHub through the commit URL (HTTP 200). A LibreOffice lock file `aws/.~lock…#` was also committed, which shows the whole `aws/` folder was added by accident. *Fix:* owner action 2.
- **SEC-03 (Critical) — Owner's DagsHub token at HEAD.** **[Observed]** `research/05_model_evaluation.ipynb:33` sets `MLFLOW_TRACKING_PASSWORD="c1f4…"` (user `AghilasSini`). It was introduced in `44d11c9` (2023-10-25), was also in README until `f23ab77`, and is still present. It is also baked into Docker images (CONT-02). *Fix:* owner action 3, then read the token from the environment.
- **SEC-04 (High) — Third-party DagsHub token in README.** **[Observed]** `README.md:68,79` contains `entbappy`'s token `6824…`, added in `f23ab77`. Not our credential, but we are republishing it. *Fix:* remove it (owner action 4).
- **SEC-05 (High) — Unauthenticated `GET /train` runs a shell command.** **[Observed]** `app.py:16-19` runs `os.system("python main.py")` for any anonymous GET.
  - In the reproduction it returned `Successfully trained [200]` **even though `main.py` failed** (exit code ignored; see DEP-01). It also overwrote the served model in place with no review.
  - Anyone who can reach port 8080 can trigger a synchronous retrain, which is a DoS vector. Note that a web crawler following links issues exactly these GETs.

  *Fix:* remove the endpoint. Retraining belongs in the pipeline or CI, not the serving app.
- **SEC-06 (Medium) — README recommends over-privileged IAM.** `README.md:112,114` tell users to attach `AmazonEC2FullAccess` and `AmazonEC2ContainerRegistryFullAccess`. *Fix:* document least-privilege policies (ECR push to one repository; no EC2 API access needed by CI).
- **SEC-07 (Low) — AWS account IDs and registry URIs in README.** `README.md:118` (`2603…` in eu-west-3) and L154 (an upstream account). They are not secrets, but they help reconnaissance. *Fix:* use placeholders.
- **SEC-08 (Low) — `SECURITY.md` is unedited GitHub boilerplate.** It claims support for versions "5.1.x / 4.0.x", which don't exist, and gives no reporting channel. *Fix:* write a real policy or remove the file.

## 3. Dependencies & reproducibility

- **DEP-01 (High) — Unpinned dependencies break the training pipeline.** **[Observed]**
  - Only `mlflow==2.2.2`, `python-box==6.0.2` and `ensure==1.0.2` are pinned. Everything else in `requirements.txt` floats, including transitive `setuptools`.
  - On Python 3.8, a fresh install resolves `setuptools==75.3.4` (`evidence/freeze-py38.txt`). `python main.py` then **fails in stage 5** at `mlflow.sklearn.log_model` with `ModuleNotFoundError: No module named 'distutils._modified'` (`evidence/repro-py38.log`).
  - Stages 1–4 do complete, and `metrics.json` is written before the crash.

  *Fix:* use a lock file (`pip-tools` / `uv lock`) with hashes, and upgrade MLflow (P3).
- **DEP-02 (High) — 371 known-vulnerability advisories across 27 resolved packages.** **[Observed]** `evidence/pip-audit.txt`, against the Python 3.8 environment.
  - `mlflow 2.2.2` alone has 142 advisories (fixed only in the 2.x/3.x line, up to 3.17.2).
  - Runtime-relevant ones include `werkzeug` 3.0.6, `flask-cors` 5.0.0, `scikit-learn` 1.3.2 and `gunicorn` 20.1.0 (an MLflow dependency).
  - Many others come from `notebook` / `jupyterlab` / `tornado`, which should not be in the runtime at all (DEP-04).
  - The count drifts as the unpinned set re-resolves.

  *Fix:* P3 upgrade, and add pip-audit to CI (P4).
- **DEP-03 (High) — Stuck on end-of-life Python 3.8; the upgrade path is blocked.** **[Observed]** Python 3.8 has been EOL since October 2024. On Python 3.12, `pip install -r requirements.txt` **fails**: `mlflow 2.2.2` requires `pyarrow` 11.0.0, which has no wheel for 3.12 and fails to build (`evidence/repro-py312.log`). *Fix:* upgrade MLflow together with Python (P3).
- **DEP-04 (Medium) — Dev and notebook dependencies in the runtime.** `notebook`, `matplotlib` and `types-PyYAML` are in `requirements.txt` and so go into the image. `-e .` installs the package in editable mode inside the container. *Fix:* split runtime and dev requirements (or use optional extras), and install the package non-editably.

## 4. ML pipeline correctness

- **PIPE-01 (High) — Data validation reports success on data that doesn't match the schema.** **[Observed]** Reproduced in `evidence/validation-bug.txt`.
  - `schema.yaml:6` declares `clorides`, but the dataset column is `chlorides`. Validation should fail, yet it returns `True`.
  - The cause is `data_validation.py:23-31`: it overwrites `validation_status` and the status file **on every column**, so only the *last* column (`quality`) decides the result.
  - Moving `chlorides` to the end flips the result to `False`. Adding a junk column at the start still gives `True`.

  *Fix:* compare column sets (and dtypes) exactly, and fail on any difference. Add unit tests (P2).
- **PIPE-02 (High) — Stage failures are swallowed, so the pipeline keeps going.** **[Inferred]** `stage_03_data_transformation.py:27-28` catches the "schema is not valid" exception and only `print`s it. `main.py` then runs stage 4 on whatever `train.csv` / `test.csv` already exist; stale files are tracked in `artifacts/`. The gate is also a fragile string parse of `status.txt` (L18-19). *Fix:* raise; pass the validation result explicitly; make `main.py` stop on the first failure.
- **PIPE-03 (Medium) — Non-deterministic split.** **[Observed]** `data_transformation.py:17` calls `train_test_split(data)` without `random_state`. The committed `metrics.json` has RMSE 0.758; the reproduction run produced RMSE 0.811 from the same code and data. *Fix:* put the seed and split ratio in `params.yaml`.
- **PIPE-04 (Medium) — Shallow validation.** Only column names are checked. The dtypes declared in `schema.yaml`, nulls and value ranges are not. *Fix:* validate the schema properly, e.g. with pandera or Great Expectations, as recommendations to evaluate in P2.
- **PIPE-05 (Medium) — No automated tests.** There is no test directory, no pytest, and the CI "unit tests" step is an `echo` (CICD-01). PIPE-01 would have been caught by a single test. *Fix:* P2.
- **PIPE-06 (Low) — `Path` only resolves through a star import.** `model_evaluation.py:43` uses `Path`, which is only available via `from mlProject.constants import *` (L13; ruff F405). *Fix:* import it explicitly.
- **PIPE-07 (Low) — Dead code in the trainer.** `model_trainer.py:18-23` loads the test set and builds `test_x` / `test_y`, which are never used (ruff F841).
- **PIPE-08 (Low) — Hard-coded feature order in serving.** `app.py:25-37` builds an unnamed 11-value array in a hand-written order that must match the training columns. Nothing enforces the match. *Fix:* build a DataFrame using the schema's column order.
- **PIPE-09 (Info) — Model quality is near-baseline.** The committed `metrics.json` shows R² = 0.048. With `alpha=0.5`, ElasticNet predicts close to the mean. This is out of scope for this audit, but worth knowing before treating the model as "production".

## 5. Data & model artifact management

- **DATA-01 (Medium) — Data comes from a third-party personal repository with no integrity check.** `config/config.yaml:5` points to `github.com/entbappy/Branching-tutorial/raw/master/winequality-data.zip`. There is no checksum or version pin. `data_ingestion.py:17` skips the download if a local file exists, so a corrupt or changed file goes unnoticed. *Fix:* use the canonical dataset source plus a checksum, or version the data (P5).
- **DATA-02 (Medium) — Data and the trained model are committed to Git with no versioning.** **[Observed]** `artifacts/` (7 files) includes `data.zip`, `winequality-red.csv`, the train/test splits, `metrics.json` and `model_trainer/model.joblib`. Serving loads this committed model (`prediction.py:10`), so the deployed model is whichever file was last committed, with no link to an experiment. *Fix:* evaluate DVC and/or the MLflow model registry (P5).
- **DATA-03 (Medium) — `mlruns/` is half-tracked.** **[Observed]** 63 files of local MLflow runs are tracked, and all of them are deleted in the current working tree. That is local run state, not source. *Fix:* untrack and ignore `mlruns/` (P1).

## 6. Experiment tracking (MLflow / DagsHub)

- **TRK-01 (Medium) — Tracking configuration is hard-coded and split.** `configuration.py:89` hard-codes the DagsHub URL as `mlflow_uri`, and `model_evaluation.py:35` uses it only as the **registry** URI. The **tracking** URI comes from the environment. Without env vars, runs go to the local `./mlruns` while the registry points at DagsHub. Credentials were supplied by hard-coding (SEC-03). *Fix:* read tracking and registry settings and credentials from the environment only (P1).
- **TRK-02 (Medium) — The registry isn't used for serving.** Evaluation registers `ElasticnetModel` when tracking is remote (`model_evaluation.py:52-55`), but the app always loads `artifacts/model_trainer/model.joblib`. No promotion step connects them. *Fix:* P5.
- **TRK-03 (Low) — Logged models have no signature or input example**, and the logged params don't include the split seed or ratio, which don't exist yet (PIPE-03).

## 7. Serving app (Flask)

- **SERV-01 (Medium) — The model is reloaded from disk on every request.** `app.py:41` creates `PredictPipeline()` per POST, which runs `joblib.load` (`prediction.py:10`). *Fix:* load it once at startup.
- **SERV-02 (Medium) — Errors return HTTP 200.** **[Observed]** A POST with missing or invalid fields returned `something is wrong [200]`. The exception is only `print`ed (`app.py:46-48`). *Fix:* validate inputs; return 4xx/5xx with structured errors; log.
- **SERV-03 (Medium) — Flask development server in production.** `app.py:55` runs `app.run(host="0.0.0.0", port=8080)`, which the container's `CMD` executes directly. There is no health endpoint. *Fix:* use a WSGI server (gunicorn) and add `/health` (P3).
- **SERV-04 (Low) — `Flask-Cors` is a dependency but is never imported.** It is unused, yet it carries 6 advisories (DEP-02). *Fix:* remove it.

## 8. Containerisation

Evidence: `docs/audit/evidence/hadolint.txt`.

- **CONT-01 (High) — End-of-life base image; the build most likely fails today.** **[Inferred]**
  - `FROM python:3.8-slim-buster` (`Dockerfile:1`): Debian buster and Python 3.8 are both EOL, and the tag hasn't been rebuilt since 2023-06-13.
  - The default buster apt sources now return **404** (deb.debian.org and security.debian.org; only archive.debian.org serves them), so `RUN apt update -y && apt install awscli -y` (L3) should fail.
  - Not executed, because there was no Docker access. Confirm with `docker build .`.

  *Fix:* use a supported slim base (P3).
- **CONT-02 (High) — Secrets are baked into the image.** **[Inferred]** `COPY . /app` (L6) with no `.dockerignore` copies `research/05_model_evaluation.ipynb` (SEC-03 token) and `README.md` (SEC-04 token) into every image pushed to ECR.
  - CI checkouts are shallow (`actions/checkout` default `fetch-depth: 1`), so CI-built images carry only HEAD's `.git`.
  - An image built locally from a full clone would also carry the full history, including SEC-01/02.

  *Fix:* `.dockerignore` and a whitelist `COPY` (P1); purge existing images (owner action 5).
- **CONT-03 (Medium) — The container runs as root.** There is no `USER` instruction. *Fix:* add a non-root user.
- **CONT-04 (Medium) — Unneeded tools and bloat.** `awscli` is installed in the runtime image, but the app never calls AWS. hadolint reports DL3027 (`apt` instead of `apt-get`, L3) and DL3042 (pip cache kept, L7). Dev dependencies are included (DEP-04). *Fix:* slim the image, or use a multi-stage build.
- **CONT-05 (Low) — No `HEALTHCHECK`**, and no exposed health route (SERV-03).

## 9. CI/CD (GitHub Actions → ECR → self-hosted EC2)

Evidence: `docs/audit/evidence/actionlint.txt`. **GitHub Actions is being replaced by an open-source CI** (see the migration assessment below). Following design decision 7, each finding is tagged:
- **[PI] platform-independent:** keeps its severity and becomes a requirement for the new CI.
- **[GHA] superseded by the CI migration:** rated Info and not to be fixed.

Whether the EC2/ECR deployment is still live is *unverifiable from repo* (open question).

- **CICD-01 (High) [PI] — The lint and test steps are placeholders.** `main.yaml:22-26` only `echo`, so the "Continuous Integration" gate that every deployment depends on checks nothing.
- **CICD-02 (High) [PI] — Long-lived AWS access keys in CI secrets.** `main.yaml:43-45,76-78` use `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, the same credential class that leaked in SEC-01.
- **CICD-03 (High) [PI] — AWS credentials are injected into the serving container.** `main.yaml:95` passes `-e AWS_ACCESS_KEY_ID=… -e AWS_SECRET_ACCESS_KEY=…` to `docker run`, although the app makes no AWS calls. They can be read with `docker inspect` by anyone with Docker access on the host.
- **CICD-04 (High) [PI] — Redeploys fail on a container-name conflict.** **[Inferred]** The "Stop and remove container" step is commented out (`main.yaml:89-91`). `docker run --name=mlproj` (L95) fails while the previous `mlproj` container exists, so every deployment after the first fails, unless someone removes the container by hand on the host.
- **CICD-05 (Medium) [PI] — Mutable `latest` tag only.** `main.yaml:56,95` always use `IMAGE_TAG: latest`, so there is no rollback and no link between a running image and its commit. *Requirement:* tag with the commit SHA.
- **CICD-06 (Medium) [PI] — A self-hosted runner serves a public repository.** `main.yaml:68` uses `runs-on: self-hosted`, on the EC2 host that also serves production. GitHub advises against self-hosted runners on public repos. The workflow only triggers on pushes to `main`, which limits the risk, but the runner host holds Docker and AWS credentials. *Requirement:* isolated or ephemeral runners, with the deploy step separated from the build.
- **CICD-07 (Medium) [PI] — CI never trains or validates the model.** The image ships whatever `model.joblib` is committed (DATA-02). *Requirement:* a pipeline stage that runs the tests and, optionally, a training smoke test.
- **CICD-08 (Low) [PI] — Narrow triggers.** Only `push` to `main` runs CI (`main.yaml:3-8`), with no pull/merge-request validation. *Requirement:* run lint and tests on PR/MR.
- **CICD-09 (Info) [GHA] — Deprecated `::set-output`** (`main.yaml:63`, actionlint `deprecated-commands`). Superseded by the CI migration.
- **CICD-10 (Info) [GHA] — Outdated actions.** `aws-actions/configure-aws-credentials@v1` is "too old to run on GitHub Actions" (actionlint, L41, L74); `actions/checkout@v3` and `amazon-ecr-login@v1` are also old. Superseded.
- **CICD-11 (Info) [GHA] — `permissions: id-token: write` is granted but unused** (`main.yaml:11`). It was meant for GitHub→AWS OIDC. Superseded. The AWS authentication *need* carries over as a requirement (below).

---

## CI migration assessment (GitHub Actions → open-source CI)

**Decision recorded:** the new version runs CI on open-source tooling only. AWS ECR/EC2 and DagsHub are **kept**. This section compares candidates; **it does not pick one**, which is phase P4's job.

**Candidates:**
- **Forgejo Actions / Gitea Actions:** self-hosted forge plus `forgejo-runner` / `act_runner`.
- **Woodpecker CI:** a standalone CI server that works with GitHub, Gitea/Forgejo and GitLab forges.
- **GitLab CE CI:** for example the existing `git.univ-lemans.fr`, which `setup.py` already references.

### Feature mapping

The ✅ / ⚠️ / ❌ marks are expectations from each tool's documentation, still to be confirmed with a proof of concept in P4:
- ✅ supported natively.
- ⚠️ possible, but with a workaround or caveat.
- ❌ gap.

| Current workflow feature (`main.yaml` line) | Forgejo / Gitea Actions | Woodpecker CI | GitLab CE |
|---|---|---|---|
| Trigger `push` to `main` (L3-5) | ✅ same `on:` syntax | ✅ `when: event: push, branch: main` | ✅ `rules: if $CI_COMMIT_BRANCH == "main"` |
| `paths-ignore: README.md` (L7-8) | ✅ same syntax | ✅ `when: path: exclude:` | ✅ `rules: changes:` |
| Job chain `needs:` (L28, L66) | ✅ same syntax | ✅ multiple workflows with `depends_on` | ✅ `stages` / `needs:` |
| `actions/checkout` (L20, 34, 71) | ✅ (mirrored or GitHub-hosted actions) | ✅ built-in clone step | ✅ built-in |
| Repo secrets `${{ secrets.* }}` (L43-45, 56, 76-78, 86, 95) | ✅ repo/org secrets, same syntax | ✅ `from_secret:` | ✅ masked/protected CI/CD variables |
| Shell `run:` steps, incl. the lint/test placeholders (L22-26) | ✅ same syntax | ✅ `commands:` in a step `image:` | ✅ `script:` |
| `apt-get install jq unzip` on the runner (L36-38) | ✅ `run:` step (or a preinstalled runner image) | ✅ step `image:` with the tools | ✅ job `image:` or `before_script` |
| `aws-actions/configure-aws-credentials` (L41, 74) | ⚠️ the Node action usually runs, but needs to be fetched from GitHub or mirrored; otherwise use `aws` CLI env vars | ⚠️ no action; env vars for the `aws` CLI or plugin settings | ⚠️ no action; CI variables + `aws` CLI |
| `amazon-ecr-login` (L49, 82) | ⚠️ as above, or `aws ecr get-login-password \| docker login` | ✅ docker-buildx plugin has ECR settings, or the CLI login | ✅ CLI login in a `docker:dind` or shell-executor job |
| `docker build` / `push` (L59-62) | ✅ on a runner with Docker | ✅ buildx plugin or a privileged/local backend | ✅ dind or shell executor |
| Step outputs (`set-output`, L63) | ✅ `$GITHUB_OUTPUT`-compatible | ⚠️ no step outputs; pass data through the workspace | ✅ `artifacts` / `dotenv` reports |
| `runs-on: self-hosted` on EC2 (L68) | ✅ register `forgejo-runner` / `act_runner` on EC2 | ✅ Woodpecker agent on EC2 (local or Docker backend) | ✅ `gitlab-runner` (shell executor) on EC2 |
| `docker pull` / `run` / `prune` deploy (L85-98) | ✅ shell step on the EC2 runner | ✅ step on the EC2 agent | ✅ shell job on the EC2 runner |
| AWS auth without long-lived keys (OIDC, L11) | ⚠️ OIDC ID-token support is recent or version-dependent: verify. Fallback: scoped IAM user | ❌ no native AWS OIDC: scoped IAM user, or a secrets store (e.g. OpenBao/Vault) | ✅ `id_tokens:` + AWS `AssumeRoleWithWebIdentity` |
| Code hosting | Moves to Forgejo/Gitea (or a mirror) | **Can stay on GitHub** | Moves to GitLab (pull mirroring of external repos is not in CE) |
| Porting effort | Lowest: same YAML dialect | Medium: rewrite in Woodpecker YAML | Medium: rewrite as `.gitlab-ci.yml` |

**Gaps to resolve in P4:**
- **AWS authentication.** Every candidate except GitLab CE falls back to static credentials unless OIDC is confirmed. That makes CICD-02 a hard requirement to design around.
- **Code hosting.** Whether the code moves (open question) narrows the choice: staying on GitHub favours Woodpecker.

### Requirements the new CI must satisfy (from the [PI] findings)

| # | Requirement | Source |
|---|---|---|
| R1 | Run real lint (ruff), tests (pytest) and dependency audit (pip-audit) as blocking gates | CICD-01, PIPE-05, HYG-04, DEP-02 |
| R2 | Authenticate to AWS with short-lived or least-privilege credentials, scoped to ECR push for one repository | CICD-02, SEC-01, SEC-06 |
| R3 | Never inject AWS credentials into the serving container | CICD-03 |
| R4 | Idempotent deployment: replace the running container safely, with a health check before switching | CICD-04, SERV-03, CONT-05 |
| R5 | Immutable image tags (commit SHA), with `latest` optional as an alias; rollback by tag | CICD-05 |
| R6 | Isolate build runners from the production host; deploy as a separate, minimal step | CICD-06 |
| R7 | A pipeline stage that validates the model artifact (tests + optional training smoke run) | CICD-07, DATA-02 |
| R8 | Validate pull/merge requests, not only `main` pushes | CICD-08 |
| R9 | Keep pushing to AWS ECR and deploying to EC2; keep MLflow tracking on DagsHub, with credentials from CI secrets | Decision (proposal) |

---

## Findings table

Sorted by severity, then by area.

| ID | Sev. | Area | Title | Evidence | Phase |
|---|---|---|---|---|---|
| SEC-01 | Critical | Secrets | AWS IAM access key in public history | `f23ab77` `aws/mlproj-user_accessKeys.csv` | P0 |
| SEC-02 | Critical | Secrets | EC2 SSH private key in public history | `f23ab77` `aws/mlproj.pem` | P0 |
| SEC-03 | Critical | Secrets | Owner's DagsHub token at HEAD | `research/05_model_evaluation.ipynb:33` | P0, P1 |
| SEC-04 | High | Secrets | Third-party DagsHub token in README | `README.md:68,79` | P0, P1 |
| SEC-05 | High | Security | Unauthenticated `/train` runs a shell command; reports success on failure | `app.py:16-19` | P3 |
| DEP-01 | High | Dependencies | Unpinned deps → training crashes in stage 5 | `requirements.txt`, `repro-py38.log` | P3 |
| DEP-02 | High | Dependencies | 371 vulnerability advisories / 27 packages | `pip-audit.txt` | P3 |
| DEP-03 | High | Dependencies | Python 3.8 EOL; 3.12 install fails | `repro-py312.log` | P3 |
| PIPE-01 | High | Pipeline | Validation passes on mismatched schema | `data_validation.py:23-31`, `validation-bug.txt` | P2 |
| PIPE-02 | High | Pipeline | Stage 3 swallows schema failure | `stage_03_data_transformation.py:27-28` | P2 |
| CONT-01 | High | Container | EOL base image; apt sources 404 | `Dockerfile:1,3`, `hadolint.txt` | P3 |
| CONT-02 | High | Container | Tokens baked into image via `COPY .` | `Dockerfile:6` | P0, P1 |
| CICD-01 | High | CI/CD [PI] | Lint/test steps are `echo` | `main.yaml:22-26` | P4 |
| CICD-02 | High | CI/CD [PI] | Long-lived AWS keys | `main.yaml:43-45,76-78` | P4 |
| CICD-03 | High | CI/CD [PI] | AWS creds injected into app container | `main.yaml:95` | P4 |
| CICD-04 | High | CI/CD [PI] | Redeploy fails on container-name conflict | `main.yaml:89-95` | P4 |
| SEC-06 | Medium | Security | README recommends FullAccess IAM | `README.md:112,114` | P1 |
| HYG-01 | Medium | Hygiene | No `.gitignore`; build outputs tracked | `git ls-files` | P1 |
| DEP-04 | Medium | Dependencies | Dev/notebook deps in runtime; `-e .` | `requirements.txt` | P3 |
| PIPE-03 | Medium | Pipeline | No `random_state`; metrics differ per run | `data_transformation.py:17` | P2 |
| PIPE-04 | Medium | Pipeline | Validation ignores dtypes/nulls/ranges | `data_validation.py`, `schema.yaml` | P2 |
| PIPE-05 | Medium | Pipeline | No automated tests | repo-wide | P2 |
| DATA-01 | Medium | Data | Third-party data URL, no checksum | `config/config.yaml:5` | P5 |
| DATA-02 | Medium | Data | Data + model committed, unversioned | `artifacts/` | P5 |
| DATA-03 | Medium | Data | `mlruns/` tracked and deleted locally | `git status` | P1 |
| TRK-01 | Medium | Tracking | Hard-coded registry URI; tracking from env | `configuration.py:89`, `model_evaluation.py:35` | P1 |
| TRK-02 | Medium | Tracking | Registry not used by serving | `model_evaluation.py:52-55`, `prediction.py:10` | P5 |
| SERV-01 | Medium | Serving | Model reloaded per request | `app.py:41`, `prediction.py:10` | P3 |
| SERV-02 | Medium | Serving | Errors return HTTP 200 | `app.py:46-48`, `repro-py38.log` | P3 |
| SERV-03 | Medium | Serving | Flask dev server in production | `app.py:55` | P3 |
| CONT-03 | Medium | Container | Runs as root | `Dockerfile` | P3 |
| CONT-04 | Medium | Container | awscli + apt/pip hygiene (DL3027, DL3042) | `Dockerfile:3,7` | P3 |
| CICD-05 | Medium | CI/CD [PI] | `latest`-only tag | `main.yaml:56,95` | P4 |
| CICD-06 | Medium | CI/CD [PI] | Self-hosted runner on public repo / prod host | `main.yaml:68` | P4 |
| CICD-07 | Medium | CI/CD [PI] | CI never validates the model | `main.yaml` | P4 |
| SEC-07 | Low | Security | AWS account IDs in README | `README.md:118,154` | P1 |
| SEC-08 | Low | Security | `SECURITY.md` boilerplate | `SECURITY.md` | P1 |
| HYG-02 | Low | Hygiene | Wrong packaging metadata, typo | `setup.py:23-27` | P1 |
| HYG-03 | Low | Hygiene | README is the upstream tutorial's | `README.md:24,50,66-79` | P1 |
| HYG-04 | Low | Hygiene | 33 ruff issues | `ruff.txt` | P2 |
| PIPE-06 | Low | Pipeline | `Path` via star import | `model_evaluation.py:13,43` | P2 |
| PIPE-07 | Low | Pipeline | Unused test set in trainer | `model_trainer.py:18-23` | P2 |
| PIPE-08 | Low | Pipeline | Hard-coded feature order in serving | `app.py:25-37` | P2 |
| TRK-03 | Low | Tracking | No model signature; seed not logged | `model_evaluation.py` | P5 |
| SERV-04 | Low | Serving | Unused `Flask-Cors` dependency | `requirements.txt` | P3 |
| CONT-05 | Low | Container | No HEALTHCHECK | `Dockerfile` | P3 |
| CICD-08 | Low | CI/CD [PI] | No PR/MR validation | `main.yaml:3-8` | P4 |
| HYG-05 | Info | Hygiene | Scaffolding `template.py` tracked | `template.py` | P1 |
| PIPE-09 | Info | Pipeline | R² = 0.048 (near-baseline model) | `artifacts/model_evaluation/metrics.json` | P5 |
| CICD-09 | Info | CI/CD [GHA] | Deprecated `set-output` (superseded) | `main.yaml:63` | P4 (drop) |
| CICD-10 | Info | CI/CD [GHA] | Outdated actions (superseded) | `main.yaml:20,41,49,74,82` | P4 (drop) |
| CICD-11 | Info | CI/CD [GHA] | Unused `id-token: write` (superseded) | `main.yaml:11` | P4 (drop) |

Totals: Critical 3 · High 13 · Medium 19 · Low 12 · Info 5 = **52**.

---

## Remediation backlog

Each phase is a candidate OpenSpec change. They are ordered by the highest severity they contain, with dependencies noted.

| Phase | Proposed change | Goal | Findings | Highest | Depends on |
|---|---|---|---|---|---|
| **P0** | *(owner actions, no change)* | Close the credential exposure outside the repo: revoke and rotate, purge ECR images, decide on a history rewrite | SEC-01, SEC-02, SEC-03, SEC-04, CONT-02 | Critical | — |
| **P1** | `repo-hygiene-and-secret-free-config` | No secrets or build outputs in Git or images; configuration and credentials only from the environment; honest docs | SEC-03, SEC-04 (file removal), CONT-02 (`.dockerignore`), HYG-01, HYG-02, HYG-03, HYG-05, DATA-03, TRK-01, SEC-06, SEC-07, SEC-08 | Critical | P0 |
| **P2** | `pipeline-correctness-and-tests` | Validation that actually validates, fail-fast stages, a reproducible split, and a first pytest suite plus ruff cleanup | PIPE-01, PIPE-02, PIPE-03, PIPE-04, PIPE-05, PIPE-06, PIPE-07, PIPE-08, HYG-04 | High | P1 |
| **P3** | `runtime-platform-upgrade` | Locked dependencies on supported Python/MLflow; hardened slim image; production-grade serving without `/train` | DEP-01, DEP-02, DEP-03, DEP-04, CONT-01, CONT-03, CONT-04, CONT-05, SEC-05, SERV-01, SERV-02, SERV-03, SERV-04 | High | P2 (tests protect the upgrade) |
| **P4** | `ci-migration-to-open-source` | Replace GitHub Actions with the chosen open-source CI that satisfies R1–R9; drop the superseded GHA items | CICD-01…08 (as requirements), CICD-09…11 (dropped) | High | P2 (tests to run), P3 (image that builds) |
| **P5** | `data-and-model-management` | Versioned data with a checksum, a registry-driven model promotion and serving, richer MLflow logging, model-quality baseline | DATA-01, DATA-02, TRK-02, TRK-03, PIPE-09 | Medium | P3, P4 |

All 3 Critical and 13 High findings are assigned to a phase (P0–P4).

### Open questions for the owner

- Is the EC2/ECR deployment still live? If not, CICD-04 and CICD-06 drop to Medium/Low, but SEC-01/02 still apply until the key and key pair are revoked.
- Will the code stay on GitHub or move to a self-hosted forge? This narrows the P4 choice (see the migration assessment).

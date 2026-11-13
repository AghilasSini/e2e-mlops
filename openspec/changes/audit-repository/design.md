# Design

## Context

See proposal.md (Why) for motivation. The repository is small: about 30 Python/config files plus notebooks, templates and static assets, and 11 commits, the last on 2023-11-07. It follows the "End-to-end ML project with MLflow" tutorial layout:

- **Training:** `main.py` runs five stages in sequence: ingestion → validation → transformation → trainer → evaluation. They live under `src/mlProject/{components,pipeline}`. The stages are wired through `ConfigurationManager` and three YAML files: `config/config.yaml`, `params.yaml` and `schema.yaml`. The model is a scikit-learn ElasticNet on the red wine-quality dataset.
- **Tracking:** evaluation logs to MLflow. The DagsHub URI is hard-coded in `src/mlProject/config/configuration.py:89`.
- **Serving:** the Flask app `app.py` exposes `/`, `/predict` and `/train`.
- **Delivery:** `.github/workflows/main.yaml` runs on push to `main`: a placeholder CI job, then a build and push to AWS ECR, then `docker run` on a self-hosted EC2 runner.

The preliminary survey (read-only, done while writing this proposal) already found the following. The audit must verify each one, assign it an ID, and add to the list:

| Area | Observation | Evidence |
|---|---|---|
| Secrets | DagsHub token hard-coded via `MLFLOW_TRACKING_PASSWORD` | `research/05_model_evaluation.ipynb:33` |
| Secrets | Third-party DagsHub token copied from the upstream tutorial | `README.md` (dagshub section), commit `f23ab77` |
| Secrets / CI | AWS keys passed as plain `-e` env vars to the runtime container, which also gets `awscli` | `.github/workflows/main.yaml` (deploy job), `Dockerfile` |
| Pipeline correctness | `schema.yaml` lists `clorides`, but the CSV header has `chlorides`. Validation still writes `True`, because `validation_all_columns` overwrites the status on every column, so only the last column decides the result | `schema.yaml`, `src/mlProject/components/data_validation.py`, `artifacts/data_validation/status.txt` |
| Pipeline correctness | Stage 3 swallows its "schema not valid" exception with `print(e)`, so the pipeline carries on | `src/mlProject/pipeline/stage_03_data_transformation.py` |
| Pipeline correctness | `train_test_split` has no `random_state`, so runs are not reproducible | `src/mlProject/components/data_transformation.py` |
| Pipeline correctness | `Path` is used but never imported directly; it only works through `from mlProject.constants import *` | `src/mlProject/components/model_evaluation.py` |
| Serving / security | `GET /train` runs `os.system("python main.py")` with no authentication, which allows anyone to trigger retraining or a DoS | `app.py` |
| Serving | The model is reloaded from disk on every request, and errors return a 200 with `'something is wrong'` | `app.py`, `src/mlProject/pipeline/prediction.py` |
| Container | `python:3.8-slim-buster`: Python 3.8 and Debian buster are both end-of-life. The container runs as root, `COPY . /app` has no `.dockerignore`, and the image has no HEALTHCHECK | `Dockerfile` |
| CI/CD | The lint and test steps only `echo`. The workflow uses deprecated `::set-output` and old action versions (`@v1`/`@v3`) with long-lived AWS keys even though `id-token: write` is granted. The image is always tagged `latest`. The stop-old-container step is commented out, so redeploys conflict on the container name | `.github/workflows/main.yaml` |
| Dependencies | Mostly unpinned (`pandas`, `numpy`, `scikit-learn`, `Flask`…). The runtime image pulls in `notebook` and `matplotlib`. `-e .` is used inside the image | `requirements.txt` |
| Hygiene | 21 `__pycache__/*.pyc` files, `*.egg-info`, `logs/running_logs.log`, data CSV/zip and `model.joblib` are tracked. There is no `.gitignore`. 63 `mlruns/` files are tracked but deleted in the working tree | `git ls-files`, `git status` |
| Packaging / docs | The `setup.py` URL points to `git.univ-lemans.fr`, but the remote is GitHub. There is a typo `long_destription_content`. The README clone URL points to the upstream tutorial. `SECURITY.md` is unedited boilerplate. There are no tests | `setup.py`, `README.md`, `SECURITY.md` |

## Goals / Non-Goals

**Goals:**
- Every finding is reproducible by a reader from the evidence cited.
- Severity is applied consistently, so the backlog ordering can be defended.
- Each area records its claims in two kinds: **observed** (seen or run) and **inferred** (deduced from code but not executed).

**Non-Goals:**
- Auditing the live AWS/DagsHub accounts, IAM policies or the EC2 host. They are not reachable from the repo. The audit notes them as "unverifiable from repo".
- Model-quality benchmarking beyond recording the existing `metrics.json` and noting that the split is non-deterministic.

## Decisions

1. **Report format: one Markdown file in the repo (`docs/audit/AUDIT.md`), not a tool-generated dashboard or GitHub issues.**
   It is versioned with the code and diff-able when re-audited, and easy to reuse for teaching. *Alternative:* one GitHub issue per finding. We rejected it for phase 1 because issues are best created once the backlog is agreed. The backlog section is laid out so it can be turned into issues later.

2. **Severity scale: Critical / High / Medium / Low / Info, defined by impact × exploitability.**
   - Critical: credential exposure, or remote code/command execution.
   - High: wrong results shipped silently, or a deployment that fails.
   - Medium: hurts reproducibility or maintainability.
   - Low: cosmetic or docs.
   - Info: context only.

   *Alternative:* CVSS. It is too heavy for a mostly non-security codebase.

3. **Combine a manual review with a small set of standard tools; do not add tools to the project.**
   Run them from a throwaway virtualenv or container outside the repo's dependencies:
   - `gitleaks detect` (or `git log -p | grep`) over the full history, for secrets.
   - `pip-audit -r requirements.txt` for known CVEs.
   - `ruff check` / `pyflakes` for static issues.
   - `hadolint Dockerfile`.
   - `actionlint` on the workflow.

   Save the raw outputs under `docs/audit/evidence/`. *Alternative:* add them to CI now. We rejected this because it is a remediation, so it belongs in a later phase.

4. **Run reproducibility checks in a clean, isolated environment.**
   The environment is a fresh `python:3.8` container (matching the project's declared version), plus one attempt on a current Python (3.11 or 3.12) to measure upgrade friction. Do the clone into the scratch directory, not the working tree. This way the tracked `artifacts/` and the deleted-but-tracked `mlruns/` are not touched. Record each command and whether it passed or failed.

5. **Redact secrets in the report.** Cite the file, line and commit, and show only the first 4 characters. Never copy a full token into a new file.

6. **Backlog grouping: propose follow-up phases by theme**, for example:
   - P0 secrets & rotation
   - P1 repo hygiene & `.gitignore`
   - P2 pipeline correctness + tests
   - P3 dependency pinning & container hardening
   - P4 CI/CD migration to open-source CI (real lint/test, immutable tags, still pushes to ECR and deploys to EC2)
   - P5 data/model versioning

   The final ordering comes from the audit's severity results, not from this list.

7. **GitHub Actions is being replaced, so CI findings are classified by whether they survive the migration.**
   - **Tied to GitHub Actions itself** (deprecated `::set-output`, outdated `@v1`/`@v3` actions, unused `id-token: write`): rated **Info** and marked "superseded by the CI migration", so no effort goes into fixing them.
   - **Platform-independent** (placeholder lint/test steps, `latest`-only tag, commented-out container stop step, AWS secrets passed to `docker run`, self-hosted runner risk): keep their severity, and are also listed as requirements the new CI must meet.

   The audit compares candidates (Forgejo/Gitea Actions, Woodpecker CI, GitLab CE) against the features the current workflow uses, but does not pick one. *Alternative:* fix the GitHub workflow first, then migrate. Rejected because that work would be thrown away.

## Risks / Trade-offs

- **[Risk]** The tokens are already public, so the audit report alone does not reduce the exposure. → **Mitigation:** put "rotate now" at the top of the report as an owner action, separate from the phased backlog, and tell the owner as soon as the planning step is done.
- **[Risk]** Running `main.py` downloads from a third-party GitHub URL and tries to log to DagsHub. → **Mitigation:** run it offline against the tracked CSV, or with `MLFLOW_TRACKING_URI` pointing to a local `file:` store. Never use the real credentials.
- **[Risk]** The fixed versions in the tutorial (`mlflow==2.2.2`) may no longer install on current Python. → **Mitigation:** that result is itself a finding. Record it and do not work around it.
- **[Risk]** The new CI still has to authenticate to AWS, and without GitHub's OIDC integration that may mean long-lived AWS keys again. → **Mitigation:** the migration assessment records AWS authentication as a requirement each candidate must meet (scoped IAM user, or a secrets store).
- **[Trade-off]** Doing the audit by hand takes longer than running the tools alone, but the tools would miss the logic bugs, such as validation always passing.

## Open Questions

- Is the EC2 / ECR deployment still live? It decides whether the CI/CD findings are rated High or Medium. The owner can answer this when reviewing the report, and the answer does not change the audit method.
- Will the code stay hosted on GitHub or move too? It shapes which CI fits best (Forgejo Actions / GitLab CE vs Woodpecker), but the assessment compares all candidates either way.

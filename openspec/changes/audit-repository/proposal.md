# Proposal

## Why

`e2e-mlops` (GitHub: `AghilasSini/e2e-mlops`) was assembled in Oct–Nov 2023 from a tutorial template and has had no work since. Before we modernise it, we need a written, evidence-based baseline of its condition. A first survey already turned up problems that rule out changing things ad hoc: live-looking credentials in tracked files, a data-validation stage that reports success on a schema that doesn't match the data, a CI "test" job that only runs `echo`, and an end-of-life Docker base image. The audit is phase 1. It sets the scope and the priorities for every later phase. The new version must also run CI on open-source tooling only, replacing GitHub Actions. This phase records that decision and assesses what the migration involves.

## What Changes

- Produce a structured **repository audit report** at `docs/audit/AUDIT.md`. It covers 9 areas: repository hygiene, secrets & security, dependencies & reproducibility, ML pipeline correctness, data & model artifact management, experiment tracking (MLflow/DagsHub), serving app (Flask), containerisation, and CI/CD (GitHub Actions → ECR → self-hosted EC2).
- Every finding records: ID, area, severity (Critical / High / Medium / Low / Info), evidence (`file:line` or commit hash), impact, and a recommended remediation.
- Produce a **prioritised remediation backlog** at the end of the report. It groups findings into proposed follow-up phases, each one a future OpenSpec change.
- Produce a **CI platform migration assessment**. It flags GitHub Actions as being replaced and lists open-source candidates: Forgejo/Gitea Actions, Woodpecker CI, and GitLab CE on `git.univ-lemans.fr` (the URL already in `setup.py`). AWS ECR/EC2 and DagsHub are kept; the new CI must still push to ECR and deploy to EC2.
- Record **reproducibility evidence**: whether the pipeline (`python main.py`), the app (`python app.py`) and the Docker image build in a clean environment, with the exact commands run and their outcomes.
- **No code, config, CI or history changes in this phase.** Anything we fix is deferred to the follow-up changes. The one exception is credential rotation, which happens outside the repo. The report flags it as an immediate owner action.

## Capabilities

### New Capabilities

None. The audit produces documentation and changes no system behaviour, so this change sets `skip_specs: true`. Behavioural specs for the existing system (training pipeline, prediction service, delivery pipeline) belong to the follow-up changes that will modify that behaviour.

### Modified Capabilities

None. `openspec/specs/` is currently empty.

## Impact

- **New files only:** `docs/audit/AUDIT.md`, plus optional raw evidence (command logs) under `docs/audit/evidence/`.
- **No code paths modified.** `src/mlProject/**`, `app.py`, `main.py`, `Dockerfile`, `.github/workflows/main.yaml`, `requirements.txt` and the config YAMLs are read-only for this phase.
- **External/owner actions surfaced:** rotate the DagsHub token in `research/05_model_evaluation.ipynb` and check the third-party token quoted in `README.md`. Both are already in public Git history, so removing them from the files later does not un-leak them.
- **Out of scope:** fixing findings, rewriting Git history, changing cloud infrastructure, choosing new tooling (DVC, pytest, etc.), choosing the final CI platform, and migrating the code hosting. These are recommendations only.

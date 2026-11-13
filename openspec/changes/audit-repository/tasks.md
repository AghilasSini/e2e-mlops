# Tasks

## 1. Report scaffold

- [x] 1.1 Create `docs/audit/AUDIT.md`. Include these sections: Summary, Immediate owner actions, Method & tools (with the severity scale from design.md, decision 2), one section for each of the 9 audit areas, a CI migration assessment, a Findings table, and a Remediation backlog. Also create an empty `docs/audit/evidence/`. Verify: both paths exist, the 9 area headings are present, and `git diff --stat` shows no files changed outside `docs/audit/`.
- [x] 1.2 Record the audit baseline in the report: commit hash, date, remote URL, file counts, and the current `git status` summary, including the 63 tracked-but-deleted `mlruns/` files. Verify: the hash in the report matches `git rev-parse HEAD`.

## 2. Secrets & security

- [x] 2.1 Scan the full Git history for secrets (`gitleaks detect --source . --log-opts=--all`, or `git log -p --all` with token/password patterns) from a tool installed outside the project. Save the redacted output to `docs/audit/evidence/secrets.txt`. Verify: the evidence file exists and lists the hits in `research/05_model_evaluation.ipynb` and `README.md`, and no full token appears in it (`grep -E '[0-9a-f]{40}' docs/audit/evidence/` returns nothing).
- [x] 2.2 Write the secrets findings: the DagsHub tokens, AWS keys passed as container env vars, and the hard-coded MLflow URI. Add a "rotate now" entry to *Immediate owner actions*. Verify: each finding has an ID, Critical/High severity, the commit where it was introduced, and a redacted value.
- [x] 2.3 Review the attack surface of `app.py`: unauthenticated `GET /train` → `os.system`, no input validation, generic error responses. Also review `SECURITY.md`, which is still boilerplate. Verify: findings are recorded with `app.py:<line>` evidence.

## 3. Repository hygiene, packaging & docs

- [x] 3.1 Inventory the tracked files that should not be versioned: `__pycache__`, `*.egg-info`, `logs/`, data files, `model.joblib` and `mlruns/`. Note that `.gitignore` and `.dockerignore` are missing. Verify: the counts in the report match `git ls-files | grep -c <pattern>`.
- [x] 3.2 Audit `setup.py`, `README.md` and `template.py`: the wrong project URL, the `long_destription_content` typo, the upstream clone URL, the missing run/test instructions, and the license/author consistency. Verify: each finding cites file and line.

## 4. Dependencies & reproducibility

- [x] 4.1 Analyse `requirements.txt`: pinned vs unpinned packages, dev-only packages (`notebook`, `matplotlib`, `types-PyYAML`) that end up in the runtime image, and `-e .`. Run `pip-audit -r requirements.txt` in a throwaway venv and save the output to `docs/audit/evidence/pip-audit.txt`. Verify: the evidence file exists and the findings reference it.
- [x] 4.2 In a clean clone in the scratch dir, inside a `python:3.8` container, run `pip install -r requirements.txt`, then `python main.py` with `MLFLOW_TRACKING_URI=file:./mlruns` and no real credentials, then `python app.py` with a `curl` POST to `/predict`. Log each command and its exit code to `docs/audit/evidence/repro-py38.log`. Verify: the log exists, each step is marked pass or fail, and the working tree of this repo is unchanged.
- [x] 4.3 Repeat 4.2 on Python 3.11 or 3.12 to measure upgrade friction, saving to `docs/audit/evidence/repro-py3x.log`. Verify: the log exists and every install or runtime failure is recorded as a finding.

## 5. ML pipeline, data & experiment tracking

- [x] 5.1 Trace the five stages and confirm the validation bug: schema `clorides` vs CSV `chlorides`. Show that `validation_all_columns` returns `True` because only the last column decides the result, and that stage 3 swallows the failure. Verify: a short reproduction (a one-line Python call or a log excerpt) is saved to `docs/audit/evidence/validation-bug.txt` and cited in the finding.
- [x] 5.2 Review the transformation, training and evaluation stages: no `random_state`, the implicit `Path` import, the test set loaded but unused in the trainer, metrics computed only on a random split, and `mlflow.set_registry_uri` vs the tracking URI. Verify: each finding cites `file:line`, and the report clearly labels which claims were observed in the 4.2 run and which are inferred.
- [x] 5.3 Assess data and model artifact management: the data source is a third-party GitHub URL, there is no checksum or versioning, `model.joblib` is committed, and `mlruns/` is in an inconsistent state. Assess MLflow/DagsHub usage too. Verify: the findings are present, with a recommendation (e.g. DVC or the MLflow registry) recorded only as a recommendation.

## 6. Container & CI/CD

- [x] 6.1 Audit the `Dockerfile` by hand and with `hadolint` (output → `docs/audit/evidence/hadolint.txt`). Cover the EOL base image, running as root, `awscli` in the runtime image, `COPY .` without `.dockerignore`, no HEALTHCHECK, and the dev server used in production. Include the image build result from 4.2. Verify: the evidence file exists and the findings reference it.
- [x] 6.2 Audit `.github/workflows/main.yaml` by hand and with `actionlint` (output → `docs/audit/evidence/actionlint.txt`), classifying findings per design.md decision 7. GitHub-Actions-only issues (deprecated `set-output`, outdated action versions, unused `id-token`) are Info, "superseded by the CI migration". Platform-independent issues (placeholder lint/test steps, long-lived AWS keys, `latest`-only tag, commented-out container stop step, secrets passed into `docker run`, self-hosted runner risk) keep their severity. Mark live-infrastructure claims as "unverifiable from repo". Verify: the evidence file exists, every finding cites a workflow line, and each one is tagged superseded or platform-independent.
- [x] 6.3 Write the CI migration assessment section: list every GitHub Actions feature the workflow uses (triggers and path filters, secrets, needs-chain, self-hosted runner, ECR login and AWS credential actions) and map each one to Forgejo/Gitea Actions, Woodpecker CI and GitLab CE, including how each would authenticate to AWS. List the platform-independent findings from 6.2 as requirements for the new CI. Do not pick a platform. Verify: every workflow step has a counterpart or a marked gap for each candidate.

## 7. Consolidation & review

- [x] 7.1 Complete the Findings table. Use unique IDs (e.g. `SEC-01`, `PIPE-03`), apply the severity rubric consistently, and sort by severity. Verify: every finding referenced in the area sections appears in the table exactly once, and the table has no IDs that are missing from the sections.
- [x] 7.2 Write the Remediation backlog. Group findings into proposed follow-up phases (each a candidate OpenSpec change), ordered by the highest severity they contain, and give each phase a one-line goal and the finding IDs it covers. Include the CI migration phase, linked to the platform-independent CI findings that become its requirements. Verify: every Critical or High finding is assigned to a phase, and the CI migration phase exists.
- [x] 7.3 Write the Summary: a headline risk statement, counts by severity, and the top 5 actions. Then run a final scope check. Verify: `git status --short` shows changes only under `docs/audit/` and `openspec/`, and `grep -rE '[0-9a-f]{40}' docs/audit/` returns no full tokens.

## Workflow follow-up

- The owner reviews `docs/audit/AUDIT.md` and answers the Open Question in design.md about whether the EC2/ECR deployment is still live.
- Rotate the exposed DagsHub credentials, an owner action that does not wait for archive.
- Archive this change, then open the follow-up changes proposed in the backlog, starting with the P0 secrets/hygiene phase.

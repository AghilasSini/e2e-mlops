# Security Policy

## Supported versions

Only the latest commit on the default branch (`main`) is supported. Images are
identified by their commit SHA, so a fix ships as a new commit, pipeline and
deployment.

| Version | Supported |
|---|---|
| `main` (0.2.x, Python 3.11 / GitLab CI / Kubernetes) | ✅ |
| 0.0.x (Python 3.8 / GitHub Actions / AWS tutorial version) | ❌ |

## Reporting a vulnerability

* Please report vulnerabilities **privately** by e-mail to
  **aghilas.sini@univ-lemans.fr**, with "[e2e-mlops security]" in the subject. Do not
  use public issues.
* Include the affected commit, a description, and the steps to reproduce.
* You will receive an acknowledgement within **5 working days**, and a decision (fix,
  mitigation or decline, with the reason) within **30 days**.
* If you found exposed credentials, say so in the first line. They are revoked first
  and investigated second.

## Secrets policy

* **Credentials never go in the repository:** not in code, configuration files,
  notebooks, the README, Helm values or Dockerfiles. This covers registry tokens,
  kubeconfigs, MLflow/S3/database passwords and cloud keys.
* **CI:** credentials live only in protected, masked GitLab CI/CD variables.
  Kubeconfigs use *file* variables.
* **Runtime:** credentials reach workloads only through Kubernetes Secrets referenced
  by name, created by `scripts/deploy.sh`. They are never set as literal environment
  values in manifests.
* **Images:** `.dockerignore` is an allow-list, so `.git`, `research/` and local
  files never enter an image.
* **Leaks:** a leaked credential is **revoked and rotated immediately**. Deleting it
  from the files is not enough, because Git history and forks keep it.
* **Scanning:** run `gitleaks detect --source . --log-opts=--all` before publishing
  history.

## Known historical exposure

The repository history from before November 2026 contains leaked credentials: an
AWS access key, an EC2 private key and tokens for the previously hosted MLflow service. They are documented in
[`docs/audit/AUDIT.md`](docs/audit/AUDIT.md) under *Immediate owner actions*. All of
them must be considered compromised and revoked. They are not used anywhere in the
current version.

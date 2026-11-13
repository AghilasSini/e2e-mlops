# ci-pipeline Specification

## Purpose

Defines what the GitLab CI/CD pipeline guarantees for every commit: quality gates, immutable container images in the GitLab registry, and controlled deployment to Kubernetes.

## Requirements

### Requirement: Ordered stages
The pipeline SHALL run the stages `test`, `build` and `deploy` in that order. A failure in a stage SHALL prevent all later stages from running.

#### Scenario: Failing test blocks the build
- **WHEN** a unit test fails on a commit
- **THEN** no image is built or pushed and no deployment runs for that pipeline

### Requirement: Quality gates
The `test` stage SHALL fail when any of the following holds:
- `flake8` reports an issue.
- `black --check` or `isort --check` reports unformatted code.
- `pytest` fails, or line coverage of `src/` is below the configured minimum (default 80 %).
- The Helm chart fails linting or schema validation.

#### Scenario: Formatting violation
- **WHEN** a commit contains a file that `black` would reformat
- **THEN** the `test` stage fails and names the file

#### Scenario: Test report published
- **WHEN** the tests run
- **THEN** the JUnit and coverage reports are attached to the pipeline

### Requirement: Immutable image tags in the GitLab registry
The `build` stage SHALL build the `trainer` and `inference` images and push them to the project's GitLab Container Registry. Each image SHALL be tagged with the commit SHA. On the default branch the images SHALL additionally be tagged `latest`. Deployments SHALL reference the SHA tag, never `latest`.

#### Scenario: Default-branch build
- **WHEN** a commit is pushed to the default branch
- **THEN** both images exist in the registry under the commit's short SHA

#### Scenario: Merge-request build
- **WHEN** a pipeline runs for a merge request
- **THEN** images are built to verify the Dockerfile, but nothing is deployed

### Requirement: Build without privileged daemon
Image builds SHALL NOT require a privileged Docker-in-Docker service or access to a host Docker socket.

#### Scenario: Unprivileged runner
- **WHEN** the build job runs on a Kubernetes or Docker runner with privileged mode disabled
- **THEN** the images build and push successfully

### Requirement: Controlled deployment targets
The `deploy` stage SHALL be able to deploy to two targets, `local` and `openstack`, using only open-source tooling:
- The `openstack` deploy SHALL run only on the default branch and only after a manual approval.
- The `local` deploy targets a developer's own k3s or Minikube cluster. It SHALL exist only when enabled by a CI variable, SHALL be manual, and SHALL NOT block or fail the pipeline when no runner for that cluster is available.

#### Scenario: No local runner
- **WHEN** a default-branch pipeline runs in a project where local deployment is not enabled
- **THEN** no `local` deploy job is created and the pipeline can succeed

#### Scenario: Production approval
- **WHEN** a default-branch pipeline reaches the `deploy` stage
- **THEN** the `openstack` job waits for a manual trigger and records a GitLab environment named `production`

### Requirement: Secrets only from CI variables
Cluster credentials and registry tokens SHALL come from protected, masked CI/CD variables. They SHALL never appear in the repository, in images or in job logs.

#### Scenario: Secret scan
- **WHEN** the repository tree and built images are scanned for credentials
- **THEN** no cluster, registry or tracking credential is found

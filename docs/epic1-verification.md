# Epic 1 verification — `add-gitlab-ci-and-k8s-delivery`

Evidence for OpenSpec change `add-gitlab-ci-and-k8s-delivery`. `$SCRATCH` stands for a
throw-away directory outside the repository. `<repo>` is the repository root.

| Check | Where | Result |
|---|---|---|
| Lint, tests, training, API from a fresh export | [section 1](#1-end-to-end-check-from-a-fresh-export-task-92) | ✅ |
| Images: hadolint, size, UID, read-only run, healthcheck | tasks.md 6.1 note | ✅ trainer 392 MB, inference 411 MB |
| Helm lint, kubeconform, chart tests, local budget | tasks.md 7.1 note | ✅ 0.35 CPU / 576 MiB requests, 1,152 MiB limits, 1 GiB volume |
| CI jobs `lint`, `unit-tests`, `chart-lint` in containers (gitlab-ci-local + podman) | tasks.md 8.1 note | ✅ |
| CI `build-*` / `deploy-*` jobs | — | ⏳ need the GitLab registry and a cluster |
| Deployment on a local cluster | [section 3](#3-cluster-run-on-kind-epic-3-task-62) | ✅ on kind / Kubernetes v1.34.11 (Epic 3 task 6.2); on k3s v1.36.5 the deploy → train → serve flow ran in KServe mode (`docs/epic3-verification.md` section E) |
| Minikube path | `docs/epic3-verification.md` section C | ✅ Minikube v1.39.0 / Kubernetes v1.37.0, KServe mode (2026-11-12) |

Notes:
* `model_version` ends in `-nogit` in section 1 because the export has no `.git`
  directory. In images it ends with the build's commit SHA.
* The chart tests are skipped in section 1 (no `helm` on `PATH` there). They pass in the
  7.1 and 8.1 runs.

## 1. End-to-end check from a fresh export (task 9.2)

Run of 2026-11-09T16:04Z — base commit 22d8919 + uncommitted Epic 1 changes

Fresh export of the files Git would commit (105 files) into an empty directory, new virtualenv, Python 3.11, uv 0.10.9.

### Install from the lock file

```console
$ uv sync --frozen --all-extras -p 3.11
 + requests==2.34.2
 + rich==15.0.0
 + scikit-learn==1.7.2
 + scipy==1.17.1
 + sortedcontainers==2.4.0
 + starlette==1.10.0
 + threadpoolctl==3.8.0
 + tomli==2.5.0
 + tomli-w==1.2.0
 + truststore==0.10.5
 + typing-extensions==4.16.0
 + typing-inspection==0.4.5
 + tzdata==2026.6
 + urllib3==2.8.0
 + uvicorn==0.54.0
```

**exit 0** ✅

### flake8

```console
$ uv run --frozen flake8 src tests scripts
```

**exit 0** ✅

### black

```console
$ uv run --frozen black --check src tests scripts
All done! ✨ 🍰 ✨
28 files would be left unchanged.
```

**exit 0** ✅

### isort

```console
$ uv run --frozen isort --check-only src tests scripts
```

**exit 0** ✅

### Tests with coverage (helm absent here, so chart tests are skipped; they run in 7.1/8.1)

```console
$ uv run --frozen pytest --cov=src -q -p no:cacheprovider
.................................................                        [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.11.15-final-0 _______________

Name                       Stmts   Miss Branch BrPart  Cover   Missing
----------------------------------------------------------------------
src/common/config.py          98      3      8      1    96%   95, 155-156
src/training/pipeline.py      81      1      2      0    99%   60
----------------------------------------------------------------------
TOTAL                        550      4     60      1    99%

15 files skipped due to complete coverage.
Required test coverage of 80.0% reached. Total coverage: 99.18%
=========================== short test summary info ============================
100 passed, 21 skipped in 4.64s
```

**exit 0** ✅

### Train on the seed data

```console
$ uv run --frozen python -m training
2026-11-09 17:04:43,181 INFO training.pipeline: step load-data: done
2026-11-09 17:04:43,181 INFO training.pipeline: step validate: start
2026-11-09 17:04:43,182 INFO training.pipeline: step validate: done
2026-11-09 17:04:43,182 INFO training.pipeline: schema valid (12 columns)
2026-11-09 17:04:43,183 INFO training.pipeline: step features: start
2026-11-09 17:04:43,184 INFO training.pipeline: step features: done
2026-11-09 17:04:43,184 INFO training.pipeline: step split: start
2026-11-09 17:04:43,185 INFO training.pipeline: step split: done
2026-11-09 17:04:43,185 INFO training.pipeline: step fit: start
2026-11-09 17:04:43,192 INFO training.pipeline: step fit: done
2026-11-09 17:04:43,192 INFO training.pipeline: step evaluate: start
2026-11-09 17:04:43,196 INFO training.pipeline: step evaluate: done
2026-11-09 17:04:43,199 INFO training.pipeline: step write-outputs: start
2026-11-09 17:04:43,201 INFO training.pipeline: step write-outputs: done
2026-11-09 17:04:43,201 INFO training.pipeline: model 20261109T160443Z-nogit written to $SCRATCH/e2e-fresh/outputs: {'roc_auc': 0.824090427410809, 'f1': 0.7544910179640718, 'accuracy': 0.74375, 'n_test': 320}
```

**exit 0** ✅

### Metrics

```console
$ cat outputs/metrics.json
{
  "accuracy": 0.74375,
  "f1": 0.7544910179640718,
  "n_test": 320,
  "roc_auc": 0.824090427410809
}
```

**exit 0** ✅

### Readiness

```console
$ curl -sf http://127.0.0.1:18081/health/ready
{"status":"ok","model_version":"20261109T160443Z-nogit"}
```

**exit 0** ✅

### README curl example (POST /predict)

```console
$ curl -sf -X POST http://127.0.0.1:18081/predict -H content-type: application/json -d {
  "fixed_acidity": 7.4, "volatile_acidity": 0.7, "citric_acid": 0.0, "residual_sugar": 1.9,
  "chlorides": 0.076, "free_sulfur_dioxide": 11, "total_sulfur_dioxide": 34,
  "density": 0.9978, "ph": 3.51, "sulphates": 0.56, "alcohol": 9.4}
{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"20261109T160443Z-nogit"}
```

**exit 0** ✅

### Invalid input is rejected (expects HTTP 422)

```console
$ sh -c test "$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:18081/predict -H 'content-type: application/json' -d '{"alcohol": "abc"}')" = 422 && echo 422
422
```

**exit 0** ✅

### No training endpoint (expects HTTP 404)

```console
$ sh -c test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/train)" = 404 && echo 404
404
```

**exit 0** ✅

### openspec validate

```console
$ sh -c cd '<repo>' && openspec validate add-gitlab-ci-and-k8s-delivery
Change 'add-gitlab-ci-and-k8s-delivery' is valid
```

**exit 0** ✅

**Overall: all steps passed ✅**

## 2. Local k3s cluster (task 7.2)

*Skipped for now (owner decision, 2026-11-09):* the session had no readable kubeconfig, so this has not run yet. It needs read access to the k3s kubeconfig and `sudo` to import images
into k3s. The sequence to record here: `scripts/local-up.sh k3s` →
`scripts/deploy.sh local` → `scripts/run-training.sh local` → `/health/ready` 200,
then `/predict` and `kubectl top pods`.

## 3. Cluster run on kind (Epic 3 task 6.2)

Run of 2026-11-11 with the code of Epic 3 (`add-kserve-serving-and-inference-logging`), using the Deployment inference mode, which is the Epic 1–2 layout. Cluster: **kind v0.33.0, Kubernetes v1.34.11** (rootless podman 4.9.3, containerd 2.3.4). This is the verification cluster of Epic 3 (design.md decision 9). The owner's k3s still has no user-readable kubeconfig, so the **k3s** run itself remains pending.

The images were built with podman and loaded with `kind load image-archive`, the kind equivalent of `scripts/local-up.sh`, which supports only k3s and Minikube.

**Deploy, train, ready.** `scripts/deploy.sh local --set inference.mode=deployment` ran the first-install bootstrap: install, one training run (now `scripts/run-training.sh`, which also pins the champion), then the final upgrade. Exit 0.

```console
==> inference mode: deployment
==> namespace e2e-mlops
==> secret e2e-mlops-postgres exists, kept
==> secret e2e-mlops-s3 exists, kept
==> secret e2e-mlops-mlflow-admin exists, kept
==> secret e2e-mlops-mlflow-trainer exists, kept
==> secret e2e-mlops-mlflow-serving exists, kept
==> secret e2e-mlops-feature-log exists, kept
==> bootstrap: install without inference replicas, then train a first model
STATUS: deployed
REVISION: 1
Inference service: e2e-mlops-inference (NodePort), serving models:/wine-quality@champion
==> starting job/e2e-mlops-training-20261111134809 from cronjob.batch/e2e-mlops-training
==> evaluating registered version 1 for promotion
==> training job e2e-mlops-training-20261111134809 succeeded
==> rolling out champion version 1 (was the alias) to e2e-mlops (local)
STATUS: deployed
REVISION: 2
Inference service: e2e-mlops-inference (NodePort), serving models:/wine-quality/1
==> serving version 1
==> deploy localhost/e2e-mlops/{trainer,inference}:local to local (e2e-mlops/e2e-mlops)
==> keeping champion version 1
STATUS: deployed
REVISION: 3
Inference service: e2e-mlops-inference (NodePort), serving models:/wine-quality/1
```

**Readiness, `/predict`, restart, `kubectl top`:**

```console
$ curl -s -o /dev/null -w '%{http_code}\n' http://localhost:15000/api/2.0/mlflow/experiments/search?max_results=1
403
[exit 0]
$ curl -s -w ' %{http_code}\n' http://localhost:18080/health/ready
{"status":"ok","model_version":"wine-quality/1"} 200
[exit 0]
$ curl -s -w ' %{http_code}\n' -H 'content-type: application/json' http://localhost:18080/predict -d '{"fixed_acidity":7.4,"volatile_acidity":0.7,"citric_acid":0.0,"residual_sugar":1.9,"chlorides":0.076,"free_sulfur_dioxide":11.0,"total_sulfur_dioxide":34.0,"density":0.9978,"ph":3.51,"sulphates":0.56,"alcohol":9.4}'
{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"wine-quality/1"} 200
[exit 0]
$ kubectl -n e2e-mlops rollout restart deployment/e2e-mlops-inference && kubectl -n e2e-mlops rollout status deployment/e2e-mlops-inference --timeout=180s
deployment.apps/e2e-mlops-inference restarted
Waiting for deployment "e2e-mlops-inference" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "e2e-mlops-inference" rollout to finish: 1 old replicas are pending termination...
deployment "e2e-mlops-inference" successfully rolled out
[exit 0]
$ curl -s -w ' %{http_code}\n' -H 'content-type: application/json' http://localhost:18080/predict -d '{"fixed_acidity":7.4,"volatile_acidity":0.7,"citric_acid":0.0,"residual_sugar":1.9,"chlorides":0.076,"free_sulfur_dioxide":11.0,"total_sulfur_dioxide":34.0,"density":0.9978,"ph":3.51,"sulphates":0.56,"alcohol":9.4}'
{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"wine-quality/1"} 200
[exit 0]
$ kubectl -n e2e-mlops delete pod -l 'app.kubernetes.io/component in (tracking,postgres,seaweedfs)' --wait=true
pod "e2e-mlops-mlflow-68545c8c45-hsw7r" deleted from e2e-mlops namespace
pod "e2e-mlops-postgres-0" deleted from e2e-mlops namespace
[exit 0]
$ kubectl -n e2e-mlops get pods
NAME                                      READY   STATUS      RESTARTS   AGE
e2e-mlops-inference-7694984bbb-srslt      1/1     Running     0          40s
e2e-mlops-mlflow-68545c8c45-bjkft         0/1     Running     0          3s
e2e-mlops-postgres-0                      1/1     Running     0          2s
e2e-mlops-seaweedfs-0                     1/1     Running     0          9m36s
e2e-mlops-training-20261111134809-h82zt   0/1     Completed   0          7m29s
[exit 0]
$ kubectl -n e2e-mlops wait --for=condition=Ready pod -l 'app.kubernetes.io/component in (tracking,postgres,seaweedfs)' --timeout=300s
pod/e2e-mlops-mlflow-68545c8c45-bjkft condition met
pod/e2e-mlops-postgres-0 condition met
[exit 0]
# anonymous request with an allowed Host (the documented port-forward 5000:5000):
$ curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5000/api/2.0/mlflow/experiments/search?max_results=1
401
[exit 0]
$ kubectl -n e2e-mlops delete pod e2e-mlops-seaweedfs-0 --wait=true
pod "e2e-mlops-seaweedfs-0" deleted from e2e-mlops namespace
[exit 0]
$ kubectl -n e2e-mlops wait --for=condition=Ready pod -l 'app.kubernetes.io/component in (tracking,postgres,object-store)' --timeout=300s
pod/e2e-mlops-mlflow-68545c8c45-bjkft condition met
pod/e2e-mlops-postgres-0 condition met
pod/e2e-mlops-seaweedfs-0 condition met
[exit 0]
$ kubectl -n e2e-mlops get pods -o wide | awk '{print $1, $2, $3, $4, $5}'
NAME READY STATUS RESTARTS AGE
e2e-mlops-inference-7694984bbb-srslt 1/1 Running 0 2m2s
e2e-mlops-mlflow-68545c8c45-bjkft 1/1 Running 0 85s
e2e-mlops-postgres-0 1/1 Running 0 84s
e2e-mlops-seaweedfs-0 1/1 Running 0 7s
e2e-mlops-training-20261111134809-h82zt 0/1 Completed 0 8m51s
[exit 0]
# after deleting the mlflow, postgres and seaweedfs pods:
$ kubectl -n e2e-mlops exec -i deploy/e2e-mlops-inference -- python - < $SCRATCH/epic3/check_registry.py
champion -> wine-quality/1
metrics: ['accuracy', 'f1', 'log_loss', 'precision', 'recall', 'rmse', 'roc_auc']
run artifacts: ['confusion_matrix.json', 'data', 'metrics.json', 'schema.yaml']
artifact dirs: {'data': ['data/reference.csv', 'data/test.csv']}
versions: [1]
model downloaded through the server: ['MLmodel', 'conda.yaml', 'input_example.json', 'model.skops', 'python_env.yaml', 'registered_model_meta', 'requirements.txt', 'serving_input_example.json']
loaded: Pipeline
[exit 0]
$ kubectl top pods -n e2e-mlops   # idle, deployment mode
NAME                                   CPU(cores)   MEMORY(bytes)   
e2e-mlops-inference-7694984bbb-srslt   11m          290Mi           
e2e-mlops-mlflow-68545c8c45-bjkft      500m         440Mi           
e2e-mlops-postgres-0                   28m          28Mi            
e2e-mlops-seaweedfs-0                  6m           107Mi           
total memory in use: 865Mi (limits budget 3072Mi, requests budget 2048Mi)
```

All inference checks pass: `/health/ready` 200, a valid `/predict`, and the same version after a restart. The anonymous MLflow call is an Epic 2 check; see `docs/epic2-verification.md` section 4 for why the first one shows 403. The idle stack uses 865 Mi of memory, within the budget of 2 GiB requests and 3 GiB limits; MLflow was still at its CPU limit just after its restart.

**Bug found by this run, fixed in Epic 3:** the tracking bootstrap hook had no writable `/tmp`, so the first install failed (`BackoffLimitExceeded`). Details are in `docs/epic3-verification.md`.


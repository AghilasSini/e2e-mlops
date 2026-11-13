# Epic 2 verification — `add-mlflow-tracking-and-registry`

Evidence for OpenSpec change `add-mlflow-tracking-and-registry`. `$SCRATCH` stands for a
throw-away directory outside the repository. `<repo>` is the repository root.
Secret values were random per run and never printed. Every log was checked for them,
and none appeared.

| Check | Where | Result |
|---|---|---|
| Tracking, registration, promotion, serving from the registry: unit and integration tests | section 1, tasks 2.2–4.1 notes | ✅ 162 tests, 98.7 % coverage |
| Real MLflow server with basic-auth (local process): 401 anonymous, trainer promotes, serving can read but not promote | `tests/integration/test_mlflow_server.py` | ✅ |
| Real images in podman, read-only root filesystems: server bootstrap → trainer `train-and-promote` → inference serves `wine-quality/1` | section 2 | ✅ |
| Platform in podman: PostgreSQL 16.14 + SeaweedFS 4.50 + MLflow; artifacts in S3; data survives restart of all three | section 2 | ✅ |
| Helm chart: lint, kubeconform, 28 chart tests, local budget | tasks 5.2 note | ✅ 0.75 CPU / 1,344 MiB requests, 2,688 MiB limits, 3 GiB |
| CI test jobs in containers (gitlab-ci-local + podman) | tasks 6.1 note | ✅ |
| Secrets scripts (fake kubectl): create once, never rotate, nothing on the command line | tasks 5.3 note | ✅ |
| Integration check on a local cluster (task 5.4) | section 4 | ✅ on kind / Kubernetes v1.34.11 (Epic 3 task 6.2). On k3s v1.36.5 the bootstrap, training, promotion, registry alias and model download through the server ran in KServe mode (`docs/epic3-verification.md` section E); the anonymous-401 and pod-restart checks were not repeated there |

## 1. Local development flow from a fresh export (README "Local development")

Run of 2026-11-09T17:45Z, base commit 22d8919 + uncommitted Epic 1–2 changes; fresh export of 128 files, new virtualenv.

### Install from the lock file

```console
$ uv sync --frozen --all-extras -p 3.11
 + tomli-w==1.2.0
 + truststore==0.10.5
 + typing-extensions==4.16.0
 + typing-inspection==0.4.5
 + tzdata==2026.6
 + urllib3==2.8.0
 + uvicorn==0.54.0
 + wcwidth==0.9.4
 + werkzeug==3.1.9
 + wtforms==3.3.2
 + yarl==1.25.1
 + zipp==4.1.2
```

**exit 0** ✅

### Lint (flake8, black, isort)

```console
$ sh -c uv run --frozen flake8 src tests scripts && uv run --frozen black --check src tests scripts && uv run --frozen isort --check-only src tests scripts
All done! ✨ 🍰 ✨
39 files would be left unchanged.
```

**exit 0** ✅

### Tests with coverage (helm tests skipped: no helm on PATH here)

```console
$ uv run --frozen pytest --cov=src -q -p no:cacheprovider
src/common/config.py               118      2      8      0    98%   178-179
src/models/registry_manager.py     161      4     32      2    97%   103, 226->231, 280-282
src/serving/model.py               102      1     18      1    98%   121
src/training/pipeline.py            89      1      2      0    99%   73
src/training/tracking.py           104      2     14      0    98%   181-182
----------------------------------------------------------------------------
TOTAL                              908     10    118      3    99%

15 files skipped due to complete coverage.
Required test coverage of 80.0% reached. Total coverage: 98.73%
=========================== short test summary info ============================
162 passed, 28 skipped in 173.29s (0:02:53)
```

**exit 0** ✅

### Train, log and register (local MLflow store)

```console
$ uv run --frozen python -m training
2026-11-09 18:49:02,994 INFO training.pipeline: step evaluate: done
2026-11-09 18:49:02,997 INFO training.pipeline: step write-outputs: start
2026-11-09 18:49:02,999 INFO training.pipeline: step write-outputs: done
2026-11-09 18:49:02,999 INFO training.pipeline: model 20261109T174902Z-nogit written to $SCRATCH/e2e-fresh2/outputs: {'roc_auc': 0.824090427410809, 'f1': 0.7544910179640718, 'accuracy': 0.74375, 'precision': 0.7730061349693251, 'recall': 0.7368421052631579, 'log_loss': 0.5141945471519012, 'rmse': 0.4131045853106445, 'n_test': 320}
2026-11-09 18:49:03,002 INFO training.pipeline: step track-and-register: start
2026/11/09 18:49:03 INFO mlflow.utils.uv_utils: Detected uv project: found uv.lock and pyproject.toml in $SCRATCH/e2e-fresh2
Successfully registered model 'wine-quality'.
Created version '1' of model 'wine-quality'.
2026-11-09 18:49:04,186 INFO training.tracking: run c169b325b5e74f43a78cebc91a0f5a1d registered as wine-quality version 1
2026-11-09 18:49:04,187 INFO training.pipeline: step track-and-register: done
2026-11-09 18:49:04,187 INFO training.pipeline: step write-run-record: start
2026-11-09 18:49:04,187 INFO training.pipeline: step write-run-record: done
```

**exit 0** ✅

### Promote version 1

```console
$ uv run --frozen python -m models.registry_manager evaluate --version 1
2026-11-09 18:49:06,839 WARNING training.tracking: MLFLOW_TRACKING_URI is not set: recording the run in the local store $SCRATCH/e2e-fresh2/outputs/mlflow.db
2026-11-09 18:49:08,251 INFO __main__: decision: first champion: no version holds the champion alias
{"candidate": {"f1": 0.7544910179640718, "roc_auc": 0.824090427410809}, "candidate_version": "1", "champion": null, "champion_version": null, "decided_at": "2026-11-09T17:49:08Z", "dry_run": false, "model_name": "wine-quality", "promoted": true, "reason": "first champion: no version holds the champion alias", "thresholds": {"min_f1": 0.7, "min_improvement": 0.005, "min_roc_auc": 0.78}}
```

**exit 0** ✅

### Readiness (serves the champion)

```console
$ curl -sf http://127.0.0.1:18083/health/ready
{"status":"ok","model_version":"wine-quality/1"}
```

**exit 0** ✅

### README curl example

```console
$ curl -sf -X POST http://127.0.0.1:18083/predict -H content-type: application/json -d {"fixed_acidity": 7.4, "volatile_acidity": 0.7, "citric_acid": 0.0, "residual_sugar": 1.9, "chlorides": 0.076, "free_sulfur_dioxide": 11, "total_sulfur_dioxide": 34, "density": 0.9978, "ph": 3.51, "sulphates": 0.56, "alcohol": 9.4}
{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"wine-quality/1"}
```

**exit 0** ✅

### mlflow ui on the local store

```console
$ sh -c curl -sf http://127.0.0.1:15010/health && curl -sf 'http://127.0.0.1:15010/api/2.0/mlflow/registered-models/alias?name=wine-quality&alias=champion' | python3 -c 'import json,sys; print("champion version:", json.load(sys.stdin)["model_version"]["version"])'
OKchampion version: 1
```

**exit 0** ✅

### openspec validate

```console
$ sh -c cd '<repo>' && openspec validate add-mlflow-tracking-and-registry
Change 'add-mlflow-tracking-and-registry' is valid
```

**exit 0** ✅

**Overall: all steps passed ✅**

## 2. Platform and images in podman (no Kubernetes)

Run on 2026-11-09 with rootless podman 4.9.3. Every container ran with `--read-only`.

**Images and users.**
- Image sizes: trainer 419 MB, inference 420 MB, mlflow-server 752 MB.
- UIDs: 10001 for our images, 999 for PostgreSQL, 10001 for SeaweedFS.

**Object store (SeaweedFS).**
- The S3 bucket was created by the mlflow-server image's boto3.
- A wrong S3 key is rejected (`InvalidAccessKeyId`).

**MLflow server.**
- Healthy in about 12 s on PostgreSQL. Anonymous API requests get `401`.
- The bootstrap applied 4 grants. A second run was idempotent: it reset the passwords
  and re-granted.
- Memory: about 400 MB with the job runner off (`MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false`),
  against 1.78 GB with MLflow 3's default job runner. PostgreSQL used about 35 MB and
  SeaweedFS about 40 MB.

**Training and serving.**
- The trainer image's default command, `train-and-promote`, ran as `trainer`. It exited 0
  with `"promoted": true`.
- 12 objects landed in the bucket: run artifacts (`metrics.json`,
  `confusion_matrix.json`, `schema.yaml`, `reference.csv`, `test.csv`) and the model
  (`MLmodel`, `model.skops`, `requirements.txt`, …).
- The inference image, as `serving`, was ready with `model_version` `wine-quality/1`,
  and `/predict` returned `wine-quality/1`.
- As `serving`, `set_registered_model_alias` was refused with `PERMISSION_DENIED`.

**Restart.** After the PostgreSQL, SeaweedFS and MLflow containers were all removed and
recreated:
- champion = 1;
- 7 metrics and the run artifacts are present;
- the model downloads through the server.

## 3. Local k3s cluster (task 5.4)

*Skipped for now (owner decision, 2026-11-10): the implementation session had no readable kubeconfig.* The sequence to
record here:
1. `scripts/local-up.sh k3s`
2. `scripts/deploy.sh local`, which creates the Secrets, installs the platform, then
   trains and promotes;
3. anonymous `curl` to MLflow → 401;
4. run metrics and artifacts;
5. `champion` → `wine-quality/1`;
6. `/predict` → `wine-quality/1`;
7. delete the mlflow, postgres and seaweedfs pods, then re-check;
8. model download through the server;
9. `kubectl top pods`.

## 4. Cluster run on kind (Epic 3 task 6.2)

Run of 2026-11-11 with the code of Epic 3, using the Deployment inference mode (the Epic 2 layout), steps 1–8 of section 3. Cluster: **kind v0.33.0, Kubernetes v1.34.11** (rootless podman 4.9.3, containerd 2.3.4). This is the verification cluster of Epic 3 (design.md decision 9). The owner's k3s still has no user-readable kubeconfig, so the **k3s** run itself remains pending.

**1. Deploy, train and promote.** The first attempt failed: the bootstrap hook Job could not import MLflow's auth client, because it had a read-only root filesystem and no `/tmp` (`FileNotFoundError: No usable temporary directory found`). The podman runs of section 2 hid this, because `podman --read-only` mounts a tmpfs on `/tmp`. After the fix (a `/tmp` emptyDir on the hook, in Epic 3), `scripts/deploy.sh local --set inference.mode=deployment` exited 0:

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

**2–9.** Steps 3–9 below are numbered as in section 3. The registry was read from inside the inference pod as the `serving` user (read-only grants), so no credential left the cluster.

```console
$ kubectl -n e2e-mlops exec -i deploy/e2e-mlops-inference -- python - < check_registry.py
champion -> wine-quality/1
metrics: ['accuracy', 'f1', 'log_loss', 'precision', 'recall', 'rmse', 'roc_auc']
run artifacts: ['confusion_matrix.json', 'data', 'metrics.json', 'schema.yaml']
artifact dirs: {'data': ['data/reference.csv', 'data/test.csv']}
versions: [1]
model downloaded through the server: ['MLmodel', 'conda.yaml', 'input_example.json', 'model.skops', 'python_env.yaml', 'registered_model_meta', 'requirements.txt', 'serving_input_example.json']
loaded: Pipeline
```

- **3. Anonymous MLflow call → 401.** The first call below gets 403 because it went through local port 15000, and `localhost:15000` is not an allowed Host for MLflow. Its host check answers before authentication. Through the documented `port-forward … 5000:5000` the anonymous call gets **401**.
- **4. Run metrics and artifacts:** the 7 metrics and 5 artifacts (`confusion_matrix.json`, `metrics.json`, `schema.yaml`, `data/reference.csv`, `data/test.csv`).
- **5. `champion` → `wine-quality/1`.**
- **6. `/predict` → `wine-quality/1`**, also after an inference restart.
- **7. Persistence.** The mlflow, postgres and seaweedfs pods were deleted, then the registry check was run again: same run, artifacts and alias.
- **8. Model download through the server.** The model was downloaded through the MLflow server over the SeaweedFS S3 path, and loaded.
- **9. `kubectl top`:** 865 Mi in use at idle, within the budget.

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


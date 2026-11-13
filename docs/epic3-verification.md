# Epic 3 verification — `add-kserve-serving-and-inference-logging`

Evidence for OpenSpec change `add-kserve-serving-and-inference-logging`, task 6.1, and for the cluster checks that task 6.2 reuses. Sections A–B ran on kind (2026-11-11); C on Minikube, D in Knative mode on a kind test cluster, and E on the owner's k3s, after the owner's review (2026-11-12/13). `$SCRATCH` stands for a throw-away directory outside the repository, and `<repo>` for the repository root. Secret values were random per run and never printed. Every transcript below was scanned for them, with 0 hits.

## Cluster (sections A–B)

| Item | Value |
|---|---|
| Distribution | **kind v0.33.0** (Kubernetes in podman), as in design.md decision 9. The owner's k3s still has no user-readable kubeconfig. |
| Kubernetes | **v1.34.11** (`kindest/node:v1.34.11@sha256:44e222ee…`; kind v0.33 ships no 1.33 image), containerd 2.3.4, one node |
| Container engine | Rootless podman 4.9.3. The cluster was created inside `systemd-run --user --scope -p Delegate=yes`, so no sudo was needed. |
| CNI | kindnet, which enforces NetworkPolicies |
| Add-ons | cert-manager v1.21.3 and KServe v0.21.0 (Standard mode), installed by `scripts/kserve-up.sh local`. metrics-server v0.9.0, used only for `kubectl top`. |
| Tools | helm 3.22.1, kubectl 1.33 client |
| Images | Built with podman from the working tree and loaded with `kind load image-archive`. This replaces `scripts/local-up.sh`, which only supports k3s and Minikube. |

| Check (task 6.1) | Result |
|---|---|
| 1. `kserve-up.sh local`, images loaded, `deploy.sh local`. Upgrade from Epic 2: Deployment mode first, then the switch | ✅ after the fix to the bootstrap hook's `/tmp` (see A1) |
| 2. `run-training.sh local` rolls out the champion and the InferenceService becomes Ready | ✅ in the bootstrap run (A1) and in the fresh kserve install (B) |
| 3. `/predict` and `/v2/models/wine-quality/infer` through `svc/wine-quality-predictor` carry `wine-quality/1` | ✅ |
| 4. Feature rows within 10 s (1, 3 and 2 rows); the 422 stored unparsed | ✅ about 0.6 s each, after the fix to a schema-creation race (see A4) |
| 5. Logger scaled to 0: predictions still return 200 | ✅ 200 in about 10 ms |
| 6. A second identical training is rejected, and `serve-champion` says "already served" | ✅ no new Helm revision, same pod |
| 7. Promote v2 by hand, `serve-champion --version 2`, then `helm rollback` serves v1 again | ✅ |
| 8. `kubectl top` for every pod, against the budget | ✅ 811 Mi in use, plus about 125 Mi for the add-ons |
| Extra: a pod without the predictor label cannot reach the logger (NetworkPolicy) | ✅ timed out |
| Extra: fresh install directly in kserve mode (`NAMESPACE=e2e-fresh`) | ✅ 14 min on a loaded host |

## Defects found by these runs (fixed)

1. **The bootstrap hook had no writable `/tmp`** (Epic 2 chart). MLflow's auth client needs a temp directory when it is imported, and the hook Job's root filesystem is read-only. The podman runs of Epic 2 hid this, because `podman --read-only` mounts a tmpfs on `/tmp`. Fix: a 16 Mi `emptyDir` on `/tmp` in `templates/tracking-hooks.yaml`, tested by `test_hook_jobs_have_a_writable_tmp`.
2. **Race when creating the feature-log schema.** The database only appears after the logger has started, because Helm runs the hook after `--wait`, so the first events created the tables. A request and its response arrived together, both ran `CREATE TABLE`, and PostgreSQL rejected one of them (`pg_type_typname_nsp_index`), which lost the response event. Fix: `FeatureLogStore.ensure_schema` creates the tables once, under a lock, and treats a concurrent creation as success. Tested by `test_schema_created_once_under_concurrent_first_events`.

## A. Upgrade path (namespace `e2e-mlops`)

### A1. `deploy.sh local --set inference.mode=deployment` (Epic 2 layout, first install)

The first attempt failed with `job e2e-mlops-mlflow-bootstrap failed: BackoffLimitExceeded` (defect 1). After the fix:

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
{"candidate": {"f1": 0.7544910179640718, "roc_auc": 0.824090427410809}, "candidate_version": "1", "champion": null, "champion_version": null, "decided_at": "2026-11-11T13:49:31Z", "dry_run": false, "model_name": "wine-quality", "promoted": true, "reason": "first champion: no version holds the champion alias", "thresholds": {"min_f1": 0.7, "min_improvement": 0.005, "min_roc_auc": 0.78}}
{"champion_version": "1", "model_name": "wine-quality", "resolved_at": "2026-11-11T13:49:53Z"}
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

This is the bootstrap path, and it took 7 min 29 s:
1. install with zero inference replicas;
2. one training Job, whose last log line names the champion;
3. `serve-champion.sh` pins version 1;
4. the normal upgrade keeps that pin.

Registry checks, run from inside the inference pod as the `serving` user (read-only grants, so no credential leaves the cluster):

```console
champion -> wine-quality/1
metrics: ['accuracy', 'f1', 'log_loss', 'precision', 'recall', 'rmse', 'roc_auc']
run artifacts: ['confusion_matrix.json', 'data', 'metrics.json', 'schema.yaml']
artifact dirs: {'data': ['data/reference.csv', 'data/test.csv']}
versions: [1]
model downloaded through the server: ['MLmodel', 'conda.yaml', 'input_example.json', 'model.skops', 'python_env.yaml', 'registered_model_meta', 'requirements.txt', 'serving_input_example.json']
loaded: Pipeline
```

HTTP checks, restart and persistence (Epic 1 task 7.2 and Epic 2 task 5.4, also recorded in their verification documents):

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

The first anonymous call used local port 15000. Its `Host: localhost:15000` header is not in `MLFLOW_SERVER_ALLOWED_HOSTS`, so MLflow's host check answered 403 before authentication ran. With the documented `port-forward … 5000:5000`, the anonymous call gets **401**.

### A2. Switch to KServe: `deploy.sh local`

```console
==> inference mode: kserve
==> namespace e2e-mlops
==> secret e2e-mlops-postgres exists, kept
==> secret e2e-mlops-s3 exists, kept
==> secret e2e-mlops-mlflow-admin exists, kept
==> secret e2e-mlops-mlflow-trainer exists, kept
==> secret e2e-mlops-mlflow-serving exists, kept
==> secret e2e-mlops-feature-log exists, kept
==> deploy localhost/e2e-mlops/{trainer,inference}:local to local (e2e-mlops/e2e-mlops)
==> keeping champion version 1
STATUS: deployed
REVISION: 4
InferenceService wine-quality (Standard mode), serving models:/wine-quality/1
==> waiting for inferenceservice.serving.kserve.io/wine-quality (up to 300s)
deployment "wine-quality-predictor" successfully rolled out
==> wine-quality ready
NAME                                        READY   STATUS      RESTARTS   AGE
e2e-mlops-feature-logger-6bb6f6f6c4-bg6cb   1/1     Running     0          109s
e2e-mlops-mlflow-68545c8c45-bjkft           1/1     Running     0          4m14s
e2e-mlops-postgres-0                        1/1     Running     0          4m13s
e2e-mlops-seaweedfs-0                       1/1     Running     0          2m56s
e2e-mlops-training-20261111134809-h82zt     0/1     Completed   0          11m
wine-quality-predictor-5767b67c5c-cm4kp     2/2     Running     0          109s
```

What the upgrade changed:
- **Removed:** the Deployment-mode `e2e-mlops-inference` Deployment and Service.
- **Created:** the InferenceService `wine-quality` in Standard mode, with 2/2 containers: `kserve-container` and the injected `agent` (25m/64Mi requests, 100m/128Mi limits). Also the feature logger, its Service and its NetworkPolicy.
- **Bootstrap hook:** created the `feature_log` database and the `feature_logger` role.
- **Kept:** the pinned version 1.

### A3–A4. Predictions through KServe and the feature log

The first attempt lost the single call's response event (defect 2). That call's row had features but no prediction, and the agent logged `Sent with status code 503`. After the fix (logger image rebuilt, tables truncated):

```console
# feature_log database and role, created by the bootstrap hook:
feature_log|feature_logger|{feature_logger=CTc/feature_logger}
feature_logger|f|f
$ kubectl -n e2e-mlops port-forward svc/wine-quality-predictor 8080:80
$ curl -s -H 'content-type: application/json' localhost:8080/predict -d '<one wine>'
{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"wine-quality/1"} HTTP 200
  -> complete feature rows: 1, log entries: 1, visible after 647 ms (limit 10000 ms)
$ curl -s -H 'content-type: application/json' localhost:8080/predict -d '<3 instances>'
{"predictions":[{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"wine-quality/1"},{"prediction":0,"label":"not_good","probability":0.22481138181828822,"model_versi
  -> complete feature rows: 4, log entries: 2, visible after 605 ms (limit 10000 ms)
$ curl -s -H 'content-type: application/json' localhost:8080/v2/models/wine-quality/infer -d '<OIP v2, 2 rows>'
{"model_name":"wine-quality","model_version":"wine-quality/1","id":"450ffb99-a985-4bb2-9808-cf02a3eaf796","outputs":[{"name":"prediction","datatype":"INT64","shape":[2],"data":[0,1]},{"name":"probabil
  -> complete feature rows: 6, log entries: 3, visible after 605 ms (limit 10000 ms)
$ curl -s -H 'content-type: application/json' localhost:8080/predict -d '<{"alcohol": "abc"}>'
{"detail":[{"type":"missing","loc":["body","fixed_acidity"],"msg":"Field required","input":{"alcohol":"abc"}},{"type":"missing","loc":["body","volatile_acidity"],"msg":"Field required","input":{"alcoh
  -> complete feature rows: 6, log entries: 4, visible after 598 ms (limit 10000 ms)
$ select status, model_version, count(*) from inference_log group by 1, 2
parsed|wine-quality/1|3
unparsed|-|1
$ select event_id, row_index, alcohol, prediction, probability, model_version from inference_features order by received_at, row_index
1e7681e6|0|9.4|0|0.216|wine-quality/1
db569635|0|9.4|0|0.216|wine-quality/1
db569635|1|9.8|0|0.225|wine-quality/1
db569635|2|9.8|1|0.524|wine-quality/1
00ff3769|0|9.4|0|0.216|wine-quality/1
00ff3769|1|9.8|1|0.524|wine-quality/1
$ select request_body, response_body from inference_log where status = unparsed
{"alcohol":"abc"}|NULL
$ curl feature-logger /metrics
feature_log_events_total{outcome="stored"} 7
feature_log_events_total{outcome="dropped_empty"} 0
feature_log_events_total{outcome="dropped_not_prediction"} 0
feature_log_events_total{outcome="rejected"} 0
feature_log_events_total{outcome="error"} 0
```

### A5. NetworkPolicy, and the logger down

The probe pod's Python traceback is shortened to its last line.

```console
# NetworkPolicy: a pod without the predictor label cannot reach the logger
$ kubectl -n e2e-mlops run np-probe --rm -i --restart=Never --image=docker.io/library/python:3.11.15-slim-bookworm --image-pull-policy=IfNotPresent --command -- python -c "import urllib.request; urllib.request.urlopen('http://e2e-mlops-feature-logger/health/live', timeout=5); print('reachable')"
TimeoutError: timed out
urllib.error.URLError: <urlopen error timed out>
pod "np-probe" deleted from e2e-mlops namespace
pod e2e-mlops/np-probe terminated (Error)
[exit 1]
$ kubectl -n e2e-mlops scale deploy/e2e-mlops-feature-logger --replicas=0 && kubectl -n e2e-mlops wait --for=delete pod -l app.kubernetes.io/component=feature-logger --timeout=60s
deployment.apps/e2e-mlops-feature-logger scaled
pod/e2e-mlops-feature-logger-674cc469d8-pgmk9 condition met
[exit 0]
$ curl -s -w ' HTTP %{http_code} in %{time_total}s\n' -H 'content-type: application/json' localhost:8080/predict -d '{"fixed_acidity":7.4,"volatile_acidity":0.7,"citric_acid":0.0,"residual_sugar":1.9,"chlorides":0.076,"free_sulfur_dioxide":11.0,"total_sulfur_dioxide":34.0,"density":0.9978,"ph":3.51,"sulphates":0.56,"alcohol":9.4}'
{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"wine-quality/1"} HTTP 200 in 0.013175s
[exit 0]
$ curl -s -w ' HTTP %{http_code} in %{time_total}s\n' -H 'content-type: application/json' localhost:8080/predict -d '{"fixed_acidity":7.4,"volatile_acidity":0.7,"citric_acid":0.0,"residual_sugar":1.9,"chlorides":0.076,"free_sulfur_dioxide":11.0,"total_sulfur_dioxide":34.0,"density":0.9978,"ph":3.51,"sulphates":0.56,"alcohol":9.4}'
{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"wine-quality/1"} HTTP 200 in 0.008430s
[exit 0]
$ curl -s -w ' HTTP %{http_code} in %{time_total}s\n' -H 'content-type: application/json' localhost:8080/predict -d '{"fixed_acidity":7.4,"volatile_acidity":0.7,"citric_acid":0.0,"residual_sugar":1.9,"chlorides":0.076,"free_sulfur_dioxide":11.0,"total_sulfur_dioxide":34.0,"density":0.9978,"ph":3.51,"sulphates":0.56,"alcohol":9.4}'
{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"wine-quality/1"} HTTP 200 in 0.009227s
[exit 0]
$ curl -s -w ' HTTP %{http_code}\n' -H 'content-type: application/json' localhost:8080/v2/health/ready
{"ready":true} HTTP 200
[exit 0]
$ kubectl -n e2e-mlops logs deploy/wine-quality-predictor -c agent --tail=4
{"severity":"INFO","timestamp":"2026-11-11T14:02:30.854811651Z","caller":"logger/worker.go:165","message":"Send did not return an HTTP response: Post \"http://e2e-mlops-feature-logger.e2e-mlops.svc.cluster.local/\": dial tcp 10.96.241.225:80: connect: connection refused"}
{"severity":"INFO","timestamp":"2026-11-11T14:02:30.854882489Z","caller":"logger/worker.go:165","message":"Send did not return an HTTP response: Post \"http://e2e-mlops-feature-logger.e2e-mlops.svc.cluster.local/\": dial tcp 10.96.241.225:80: connect: connection refused"}
{"severity":"INFO","timestamp":"2026-11-11T14:02:31.884568239Z","caller":"logger/worker.go:165","message":"Send did not return an HTTP response: Post \"http://e2e-mlops-feature-logger.e2e-mlops.svc.cluster.local/\": dial tcp 10.96.241.225:80: connect: connection refused"}
{"severity":"INFO","timestamp":"2026-11-11T14:02:32.908482488Z","caller":"logger/worker.go:165","message":"Send did not return an HTTP response: Post \"http://e2e-mlops-feature-logger.e2e-mlops.svc.cluster.local/\": dial tcp 10.96.241.225:80: connect: connection refused"}
[exit 0]
$ kubectl -n e2e-mlops scale deploy/e2e-mlops-feature-logger --replicas=1 && kubectl -n e2e-mlops rollout status deploy/e2e-mlops-feature-logger --timeout=120s
deployment.apps/e2e-mlops-feature-logger scaled
Waiting for deployment "e2e-mlops-feature-logger" rollout to finish: 0 of 1 updated replicas are available...
deployment "e2e-mlops-feature-logger" successfully rolled out
[exit 0]
```

### A6. Second identical training

```console
==> starting job/e2e-mlops-training-20261111140304 from cronjob.batch/e2e-mlops-training
==> evaluating registered version 2 for promotion
{"candidate": {"f1": 0.7544910179640718, "roc_auc": 0.824090427410809}, "candidate_version": "2", "champion": {"f1": 0.7544910179640718, "roc_auc": 0.824090427410809}, "champion_version": "1", "decided_at": "2026-11-11T14:04:21Z", "dry_run": false, "model_name": "wine-quality", "promoted": false, "reason": "did not beat champion v1: roc_auc 0.8241 < 0.8241 + 0.005", "thresholds": {"min_f1": 0.7, "min_improvement": 0.005, "min_roc_auc": 0.78}}
{"champion_version": "1", "model_name": "wine-quality", "resolved_at": "2026-11-11T14:04:53Z"}
==> training job e2e-mlops-training-20261111140304 succeeded
==> version 1 already served by e2e-mlops: nothing to do
helm revision before 4 after 4; predictor pod before pod/wine-quality-predictor-5767b67c5c-cm4kp after pod/wine-quality-predictor-5767b67c5c-cm4kp
```

### A7. Champion rollout and rollback

A first attempt rolled out and rolled back correctly (revisions 5 and 6), but my transcript script sent a malformed request body, so the step was repeated:

```console
# state before: release revision 6 (rollback test above) serves version 1
$ helm history e2e-mlops -n e2e-mlops --max 3
REVISION	UPDATED                 	STATUS    	CHART          	APP VERSION	DESCRIPTION     
4       	Wed Nov 11 14:58:00 2026	superseded	e2e-mlops-0.1.0	0.2.0      	Upgrade complete
5       	Wed Nov 11 15:05:20 2026	superseded	e2e-mlops-0.1.0	0.2.0      	Upgrade complete
6       	Wed Nov 11 15:06:51 2026	deployed  	e2e-mlops-0.1.0	0.2.0      	Rollback to 4   
[exit 0]
# promote version 2 by hand (admin, inside the MLflow pod)
$ kubectl -n e2e-mlops exec -i deploy/e2e-mlops-mlflow -- python - < $SCRATCH/epic3/promote_v2.py
champion -> 2
[exit 0]
$ <repo>/scripts/serve-champion.sh local --version 2 | grep '^==>'
==> rolling out champion version 2 (was 1) to e2e-mlops (local)
==> waiting for inferenceservice.serving.kserve.io/wine-quality (up to 300s)
==> wine-quality ready
==> serving version 2
[exit 0]
$ curl -s -w ' HTTP %{http_code}\n' -H 'content-type: application/json' localhost:8080/predict --data-binary @wine.json
{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"wine-quality/2"} HTTP 200
[exit 0]
$ helm rollback e2e-mlops -n e2e-mlops --wait
Rollback was a success! Happy Helming!
[exit 0]
$ <repo>/scripts/wait-inference.sh
==> waiting for inferenceservice.serving.kserve.io/wine-quality (up to 300s)
Waiting for deployment "wine-quality-predictor" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "wine-quality-predictor" rollout to finish: 1 old replicas are pending termination...
deployment "wine-quality-predictor" successfully rolled out
==> wine-quality ready
[exit 0]
$ curl -s -w ' HTTP %{http_code}\n' -H 'content-type: application/json' localhost:8080/predict --data-binary @wine.json
{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"wine-quality/1"} HTTP 200
[exit 0]
$ helm history e2e-mlops -n e2e-mlops --max 4
REVISION	UPDATED                 	STATUS    	CHART          	APP VERSION	DESCRIPTION     
5       	Wed Nov 11 15:05:20 2026	superseded	e2e-mlops-0.1.0	0.2.0      	Upgrade complete
6       	Wed Nov 11 15:06:51 2026	superseded	e2e-mlops-0.1.0	0.2.0      	Rollback to 4   
7       	Wed Nov 11 15:07:54 2026	superseded	e2e-mlops-0.1.0	0.2.0      	Upgrade complete
8       	Wed Nov 11 15:09:24 2026	deployed  	e2e-mlops-0.1.0	0.2.0      	Rollback to 6   
[exit 0]
# registry back in line with what is served (champion -> 1)
$ kubectl -n e2e-mlops exec -i deploy/e2e-mlops-mlflow -- python - < restore_v1.py
champion -> 1
```

`helm rollback --wait` does not wait for the InferenceService, because it is a custom resource, so run `scripts/wait-inference.sh` after it. Rolling back the serving release does not move the registry alias. Above, the alias was set back to version 1 by hand.

### A8. `kubectl top`, whole stack idle (kserve mode)

```console
$ kubectl top pods -n e2e-mlops --containers
POD                                         NAME               CPU(cores)   MEMORY(bytes)   
e2e-mlops-feature-logger-674cc469d8-hpvbb   feature-logger     3m           55Mi            
e2e-mlops-mlflow-68545c8c45-bjkft           mlflow             4m           414Mi           
e2e-mlops-postgres-0                        postgres           25m          61Mi            
e2e-mlops-seaweedfs-0                       seaweedfs          7m           99Mi            
wine-quality-predictor-5767b67c5c-7r82g     agent              1m           9Mi             
wine-quality-predictor-5767b67c5c-7r82g     kserve-container   4m           172Mi           
$ kubectl top pods -n kserve --containers
POD                                          NAME              CPU(cores)   MEMORY(bytes)   
kserve-controller-manager-759fb6554d-pxnnz   kube-rbac-proxy   1m           11Mi            
kserve-controller-manager-759fb6554d-pxnnz   manager           4m           38Mi            
$ kubectl top pods -n cert-manager --containers
POD                                        NAME                      CPU(cores)   MEMORY(bytes)   
cert-manager-74579dbc9d-5kzgq              cert-manager-controller   1m           27Mi            
cert-manager-cainjector-74bbb6bc7c-5nb4v   cert-manager-cainjector   2m           31Mi            
cert-manager-webhook-5bfcf7f558-tl52v      cert-manager-webhook      1m           18Mi            
# running pods in e2e-mlops (as admitted, incl. the injected agent): cpu requests 0.475, memory requests 992Mi, memory limits 1856Mi
# e2e-mlops memory in use: 811Mi
```

The running pods are admitted at 0.475 CPU / 992 Mi requests / 1,856 Mi limits, including the injected agent. That is within the chart budget of 1 CPU / 2 GiB / 3 GiB. With the training CronJob and the hook Jobs, the chart total is 0.825 CPU / 1,504 Mi / 3,008 Mi (task 5.1). The add-ons use about 125 Mi and request 328 Mi (limit: 512 Mi).

## B. Fresh install directly in kserve mode (namespace `e2e-fresh`)

```console
==> inference mode: kserve
==> namespace e2e-fresh
==> secret e2e-mlops-postgres created
==> secret e2e-mlops-s3 created
==> secret e2e-mlops-mlflow-admin created
==> secret e2e-mlops-mlflow-trainer created
==> secret e2e-mlops-mlflow-serving created
==> secret e2e-mlops-feature-log created
==> bootstrap: install without inference replicas, then train a first model
REVISION: 1
InferenceService wine-quality (Standard mode), serving models:/wine-quality@champion
==> starting job/e2e-mlops-training-20261111141352 from cronjob.batch/e2e-mlops-training
==> evaluating registered version 1 for promotion
{"champion_version": "1", "model_name": "wine-quality", "resolved_at": "2026-11-11T14:15:35Z"}
==> training job e2e-mlops-training-20261111141352 succeeded
==> rolling out champion version 1 (was the alias) to e2e-mlops (local)
REVISION: 2
InferenceService wine-quality (Standard mode), serving models:/wine-quality/1
==> waiting for inferenceservice.serving.kserve.io/wine-quality (up to 300s)
==> wine-quality ready
==> serving version 1
==> deploy localhost/e2e-mlops/{trainer,inference}:local to local (e2e-fresh/e2e-mlops)
==> keeping champion version 1
REVISION: 3
InferenceService wine-quality (Standard mode), serving models:/wine-quality/1
==> waiting for inferenceservice.serving.kserve.io/wine-quality (up to 300s)
==> wine-quality ready
{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"wine-quality/1"} 200
```

This took 14 min 14 s. The second stack ran next to the first one, with 660 MB of host memory free. The MLflow container restarted 3 times on startup-probe failures before it became ready: its 500m CPU limit makes MLflow start slowly under load. It recovered without intervention. The namespace was deleted afterwards.

## C. Minikube (2026-11-12)

| Item | Value |
|---|---|
| Distribution | Minikube v1.39.0, KVM2 driver, Kubernetes **v1.37.0**, 2 CPUs / 4 GB / 20 GB as started by `local-up.sh minikube --start` |
| CNI | Minikube's default `bridge`, which does **not** enforce NetworkPolicies |
| Flow | exactly the README: `local-up.sh minikube --start` → `kserve-up.sh local` → `deploy.sh local` (fresh install) |

| Step | Result |
|---|---|
| `local-up.sh minikube --start` (start, build, pull, `minikube image load`) | ✅ 4 min 51 s |
| `kserve-up.sh local` | ✅ 1 min 4 s |
| `deploy.sh local`, first install: bootstrap, training, champion v1 rolled out | ✅ 8 min 7 s, Helm revisions 1–3 |
| `/predict` (single and batch of 3), `/v2/models/wine-quality/infer` carry `wine-quality/1`; invalid body → 422 | ✅ |
| Feature log: +6 feature rows and +4 entries; the 422 stored `unparsed` | ✅ within 1.4 s |
| Logger scaled to 0: prediction still 200 | ✅ in 20 ms |
| NetworkPolicy | ⚠️ not enforced: an unlabelled pod reached the logger. Expected with `bridge`; the README suggests `minikube start --cni=calico` |
| `kubectl top`, e2e-mlops | 828 Mi in use (MLflow 445, predictor 179 + agent 18, PostgreSQL 91, SeaweedFS 50, logger 44) |

The cluster was deleted afterwards.

## D. Knative mode and scale-to-zero (kind test cluster, 2026-11-12)

There is still no OpenStack cluster, so the OpenStack serving path was exercised on a throw-away kind cluster (Kubernetes v1.34.11) with Knative installed by hand, as the README's OpenStack prerequisite says:

| Item | Value |
|---|---|
| Knative | Serving **1.21.3** (`serving-crds.yaml` `44a6e765…`, `serving-core.yaml` `dc94f877…`) and Kourier **1.21.1** (`kourier.yaml` `136bf8b5…`), SHA-256 of the release manifests |
| Add-ons | `scripts/kserve-up.sh openstack`: found Knative and Kourier, installed cert-manager and KServe in 34 s |
| Release | local values (local images, in-cluster MLflow) with `--set inference.kserve.deploymentMode=Knative`, `autoscaling.minReplicas=0`, `maxReplicas=3`, `metric=concurrency`, `target=5`, i.e. the OpenStack serving settings |
| Test-only patch | `config-deployment`: `registries-skipping-tag-resolving: kind.local,ko.local,dev.local,localhost,index.docker.io`. Knative resolves image tags to digests and parses `localhost/e2e-mlops/...` as a Docker Hub path (see Findings). On OpenStack the images come from the GitLab registry, which Knative can resolve. |

```console
$ scripts/serve-champion.sh local --version 1 | grep '^==>'
==> rolling out champion version 1 (was the alias) to e2e-mlops (local)
==> waiting for inferenceservice.serving.kserve.io/wine-quality (up to 300s)
==> wine-quality ready
==> serving version 1
$ kubectl -n e2e-mlops get isvc wine-quality -o jsonpath='{...deploymentMode} {.status.deploymentMode} {.spec.predictor.minReplicas}-{.spec.predictor.maxReplicas}'
Knative Knative 0-3
# revision wine-quality-predictor-00006 annotations: class kpa, metric concurrency, min-scale 0, max-scale 3 (target 5)
# predictor pod containers: kserve-container, queue-proxy, agent
$ kubectl -n kourier-system port-forward svc/kourier-internal 8090:80   # requests carry Host: wine-quality-predictor.e2e-mlops.svc.cluster.local
$ curl ... localhost:8090/predict --data-binary @wine.json
{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"wine-quality/1"} HTTP 200
$ curl ... localhost:8090/v2/models/wine-quality/infer -d '<2 rows>'
{"model_name":"wine-quality","model_version":"wine-quality/1",...,"outputs":[{"name":"prediction","datatype":"INT64","shape":[2],"data":[0,1]},...
$ select status, model_version, count(*) from inference_log group by 1,2
parsed|wine-quality/1|2
# idle: polling the predictor pods every 10 s
# predictor pods running after 61 s idle: 0
# cold start: one request while no predictor pod runs
$ curl ... --max-time 300 localhost:8090/predict --data-binary @wine.json
{"prediction":0,...,"model_version":"wine-quality/1"} HTTP 200 in 15.628572s
$ curl ... localhost:8090/predict --data-binary @wine.json
{"prediction":0,...,"model_version":"wine-quality/1"} HTTP 200 in 0.009152s
$ select count(*) from inference_log where status = 'parsed'
4      # the cold-start request was logged too
```

| Check | Result |
|---|---|
| `kserve-up.sh openstack` detects Knative + Kourier and installs | ✅ 34 s |
| Rollout in Knative mode, KPA with the chart's autoscaling settings | ✅ revision 00006: concurrency 5, 0–3 replicas |
| Predict and OIP through Kourier carry the version and are logged | ✅ |
| Scale to zero when idle | ✅ 0 pods after 61 s |
| Cold start | ✅ 200 in 15.6 s; warm 9 ms |
| Failed rollout is rolled back (before the tag-resolution patch) | ✅ `version 1 did not become ready; release rolled back to the previous version`, exit 1 |
| Knative's own footprint | knative-serving 162 Mi, kourier-system 36 Mi |

The first deploy attempts hit two problems, kept in the transcripts: the **pod-level `securityContext` rejection** (defect 3 below, fixed) and the tag resolution of local images (test-only patch). The deploy that followed ran out of time in its training Job (MLflow OOM, see Findings), so v1 was rolled out with `serve-champion.sh --version 1`. The cluster was deleted afterwards.

## E. k3s (2026-11-13)

| Item | Value |
|---|---|
| Distribution | k3s **v1.36.5+k3s1** on the development machine (narses), containerd 2.3.4, Traefik disabled, the bundled metrics-server, local-path and CoreDNS |
| CNI | flannel with k3s's network-policy controller, which enforces NetworkPolicies |
| kubeconfig | copied to `~/.kube/config` by the owner (README steps 1–2) |
| Images | **local registry**: `local-up.sh k3s` pushes our four images to `registry:2.8.3` (`sha256:a3d8aaa6…`) on `127.0.0.1:5000`, run by rootless podman; k3s pulls them over plain HTTP with no `registries.yaml`. Third-party images were pulled by k3s from their public registries. |

The registry replaced the first design, `sudo k3s ctr images import`. The imports did not reach the kubelet in three attempts (the `k8s.io` namespace, then k3s's auto-import directory with a restart) and the cause could not be read without root. The registry needs no root at all, which also suits students.

| Step | Result |
|---|---|
| `local-up.sh k3s` (build + push, warm build cache) | ✅ 1 min 22 s, no sudo |
| `kserve-up.sh local` | ✅ 50 s (after a k3s restart, see Findings) |
| `deploy.sh local`, first install, picks `localhost:5000/e2e-mlops` by itself | ✅ 8 min 35 s, Helm revisions 1–3 |
| `/predict` (single and batch of 3), OIP carry `wine-quality/1`; invalid body → 422 | ✅ |
| Feature log: +6 feature rows, +4 entries; the 422 stored `unparsed` | ✅ within 1.4 s |
| NetworkPolicy: an unlabelled pod cannot reach the logger | ✅ blocked |
| Logger scaled to 0: prediction still 200 | ✅ in 10 ms |
| Second identical training: not promoted, `version 1 already served`, no new revision, same pod | ✅ 1 min 51 s |
| Promote v2 by hand, `serve-champion --version 2` → v2; `helm rollback` + `wait-inference.sh` → v1 | ✅ revisions 4 and 5 |
| `kubectl top`, e2e-mlops | 771 Mi in use (MLflow 404, predictor 185 + agent 10, PostgreSQL 70, logger 53, SeaweedFS 48) |
| `kubectl top`, add-ons | cert-manager 20 + 43 + 15 Mi, KServe controller 45 Mi; the registry container 7 Mi |

```console
==> deploy localhost:5000/e2e-mlops/{trainer,inference}:local to local (e2e-mlops/e2e-mlops)
==> keeping champion version 1
$ scripts/serve-champion.sh local --version 2 | grep '^==>'
==> rolling out champion version 2 (was 1) to e2e-mlops (local)
==> wine-quality ready
==> serving version 2
{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"wine-quality/2"} HTTP 200
$ helm rollback e2e-mlops -n e2e-mlops --wait && scripts/wait-inference.sh
==> wine-quality ready
{"prediction":0,"label":"not_good","probability":0.21633330693303876,"model_version":"wine-quality/1"} HTTP 200
5       	Fri Nov 13 09:25:02 2026	deployed  	e2e-mlops-0.1.0	0.2.0      	Rollback to 3
```

The release is left installed on k3s for the owner's review.

## Defects found by runs C–E (fixed)

3. **Knative rejected the predictor pod.** Knative's admission webhook refuses a pod-level `securityContext` unless the cluster enables `kubernetes.podspec-securitycontext`, so every `deploy.sh openstack` on a stock Knative would have failed. Fix: `templates/inferenceservice.yaml` sets no pod `securityContext`; the pod settings (`runAsNonRoot`, `runAsUser`, `runAsGroup`, `seccompProfile`) move into the container's, merged with the existing container settings, in both modes. `fsGroup` has no container equivalent and is not needed (`/tmp` is an `emptyDir`). KServe's logger sidecar copies the container settings. Tested by `test_predictor_runs_the_inference_image_hardened`; the reference manifest was regenerated and passes kubeconform.

4. **MLflow was OOMKilled at its 640 Mi limit.** On k3s it happened twice during the first install, just after the first promotion (it restarted by itself and the deploy succeeded). On the Knative test cluster it happened during a third consecutive training, and that training Job missed its 900 s deadline. Cause: the container runs two Python processes, the `mlflow server` launcher (246 Mi resident, it only supervises) and the uvicorn worker that serves (275 Mi). Idle use is 427 Mi and the measured peak `memory.peak` was 639 Mi. Raising the limit does not fit the local budget (64 Mi of headroom). Fix (owner decision, 2026-11-13): `scripts/mlflow_server_exec.py` runs the unchanged `mlflow server` CLI (store migrations, admin bootstrap, security settings, worker environment), but its final spawn of uvicorn becomes an `exec`, so uvicorn replaces the launcher. With one worker uvicorn serves in-process: one Python process, PID 1, which also receives SIGTERM directly. If MLflow's job runner is enabled, which needs the parent, the launcher changes nothing. `scripts/mlflow-server.sh` calls it; the image copies it to `/opt/mlflow`. Tests: unit tests of the launcher plus a contract test that MLflow 3.17.2's `_run_server` still spawns `python -m uvicorn` through `mlflow.server._exec_cmd` (`tests/scripts/test_mlflow_server_exec.py`), and `test_server_is_one_process` in the integration test of the real server.

   Measured on k3s after `deploy.sh local` (revision 6), then a third training, the v2 rollout, `helm rollback` and the alias restore:

   ```console
   1 258 Mi /opt/venv/bin/python -m uvicorn --log-config .../uvicorn_log_config.yaml --host 0.0.0.0 --port 5000 ...
   cgroup current=289Mi peak=318Mi                       # after the upgrade
   anonymous: 401  health: OK
   mlflow cgroup after the load: current=326Mi peak=548Mi (limit 640Mi)
   restarts=0
   ```

   Idle memory went from 427 Mi to about 290 Mi, and the peak under the same load from 639 Mi (killed) to 548 Mi. `memory.peak` includes reclaimable page cache, so the real margin is larger than 92 Mi. The limit is unchanged.
5. **A rebuilt image did not replace the running one on k3s.** `local-up.sh` reuses the `local` tag, and with `imagePullPolicy: IfNotPresent` a restarted pod kept the copy k3s had cached. Fix: when `deploy.sh local` uses the local registry (`localhost:5000/...`), it sets `image.pullPolicy=Always`, a cheap check against localhost on each pod start; `serve-champion.sh` keeps it through `--reuse-values`. This is how the new MLflow image above was rolled out. (A redeploy whose chart values do not change still restarts nothing; `kubectl rollout restart` the affected Deployment.)

## Findings (not fixed)

1. **Knative cannot resolve `localhost/...` images.** It parses them as Docker Hub paths when it resolves tags to digests. This only affects a local test of Knative mode; OpenStack pulls from the GitLab registry.
2. **k3s once stopped serving newly created CRDs.** After a k3s restart at 06:12, the cert-manager CRDs were Established but every read of their resources timed out (no watch cache was ever created), and a test CRD never appeared in discovery; built-in types and older CRDs answered normally. A further `systemctl restart k3s` cleared it, and the KServe CRDs created afterwards were served at once. The k3s journal was not read. Recorded in the README's troubleshooting table.

## Not run

- **OpenStack itself:** there is still no OpenStack cluster. Knative mode was exercised on a kind test cluster (section D); the GitLab registry, external object store, ingress and `deploy.sh openstack` with CI variables are not verified.
- **`sudo k3s ctr images import`:** replaced by the local registry (section E).

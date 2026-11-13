"""Rendered-manifest checks for deploy/helm/e2e-mlops (skipped when helm is absent).

Enforces the cluster-deployment spec: hardened workloads, optional components, the
local resource budget for a modest machine, the tracking platform, and registry-based
model hand-off (no volume shared between training and inference). Epic 3: inference as
a KServe InferenceService or a Deployment (model-serving spec), and the feature logger
(inference-logging spec).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "deploy" / "helm" / "e2e-mlops"
KSERVE_LOCAL = REPO_ROOT / "deploy" / "kserve" / "values-kserve-local.yaml"
HELM = os.environ.get("HELM_BIN") or shutil.which("helm")

# Local budget (Epic 1 design decision 11 / spec "Local resource budget").
MAX_CPU_REQUESTS = 1.0
MAX_MEMORY_REQUESTS = 2 * 1024**3
MAX_MEMORY_LIMITS = 3 * 1024**3
MAX_VOLUMES = 5 * 1024**3

pytestmark = pytest.mark.skipif(HELM is None, reason="helm binary not available")

_SUFFIXES = {
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
    "k": 1000,
    "M": 1000**2,
    "G": 1000**3,
    "T": 1000**4,
}
OPENSTACK_EXTERNAL = ("objectStore.external.endpoint=https://s3.example.org",)
DEPLOYMENT_MODE = "inference.mode=deployment"


def cpu(value: str | int | float) -> float:
    text = str(value)
    return float(text[:-1]) / 1000 if text.endswith("m") else float(text)


def memory(value: str | int) -> int:
    text = str(value)
    for suffix, factor in _SUFFIXES.items():
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * factor)
    return int(text)


def render(values_file: str | None = "values-local.yaml", *sets: str) -> list[dict[str, Any]]:
    cmd = [HELM, "template", "e2e-mlops", str(CHART), "--namespace", "e2e-mlops"]
    if values_file:
        cmd += ["-f", str(CHART / values_file)]
    for item in sets:
        cmd += ["--set", item]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    return [doc for doc in yaml.safe_load_all(out) if doc]


def pod_specs(docs: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any], int]]:
    """(owner, pod spec, replica count) for every workload in the release.

    An InferenceService's predictor counts as one pod spec at its maximum replica count
    (KServe adds the logger sidecar at admission: see :func:`logger_sidecar`).
    """
    found = []
    for doc in docs:
        kind = doc["kind"]
        if kind == "InferenceService":
            predictor = doc["spec"]["predictor"]
            found.append((doc, predictor, predictor.get("maxReplicas", 1)))
        elif kind in ("Deployment", "StatefulSet"):
            found.append((doc, doc["spec"]["template"]["spec"], doc["spec"].get("replicas", 1)))
        elif kind == "Job":
            found.append((doc, doc["spec"]["template"]["spec"], 1))
        elif kind == "CronJob":
            found.append((doc, doc["spec"]["jobTemplate"]["spec"]["template"]["spec"], 1))
    return found


def containers(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return spec.get("initContainers", []) + spec["containers"]


def logger_sidecar() -> dict[str, Any]:
    """The `agent` container KServe injects next to a predictor that has a logger,
    sized by scripts/kserve-up.sh from deploy/kserve/values-kserve-local.yaml."""
    sidecar = yaml.safe_load(KSERVE_LOCAL.read_text())["loggerSidecar"]
    return {
        "name": "agent",
        "resources": {
            "requests": {"cpu": sidecar["cpuRequest"], "memory": sidecar["memoryRequest"]},
            "limits": {"cpu": sidecar["cpuLimit"], "memory": sidecar["memoryLimit"]},
        },
    }


def budget_containers(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return containers(spec) + ([logger_sidecar()] if "logger" in spec else [])


def inference_pod(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """The inference pod spec, whichever mode rendered it."""
    for doc in docs:
        if doc["kind"] == "InferenceService":
            return doc["spec"]["predictor"]
    return named(docs, "Deployment", "-inference")["spec"]["template"]["spec"]


def kinds(docs: list[dict[str, Any]]) -> list[str]:
    return sorted(d["kind"] for d in docs)


def named(docs: list[dict[str, Any]], kind: str, suffix: str) -> dict[str, Any]:
    return next(d for d in docs if d["kind"] == kind and d["metadata"]["name"].endswith(suffix))


def env_of(container: dict[str, Any]) -> dict[str, Any]:
    return {e["name"]: e.get("value", e.get("valueFrom")) for e in container.get("env", [])}


def volume_bytes(docs: list[dict[str, Any]]) -> int:
    total = sum(
        memory(d["spec"]["resources"]["requests"]["storage"])
        for d in docs
        if d["kind"] == "PersistentVolumeClaim"
    )
    for d in docs:
        if d["kind"] == "StatefulSet":
            for claim in d["spec"].get("volumeClaimTemplates", []):
                total += d["spec"].get("replicas", 1) * memory(
                    claim["spec"]["resources"]["requests"]["storage"]
                )
    return total


@pytest.mark.parametrize(
    ("values_file", "sets"),
    [("values-local.yaml", ()), ("values-openstack.yaml", OPENSTACK_EXTERNAL)],
)
def test_every_container_is_non_root_with_limits(values_file: str, sets: tuple[str, ...]) -> None:
    workloads = pod_specs(render(values_file, *sets))
    assert workloads
    for owner, spec, _ in workloads:
        pod_sc = spec.get("securityContext", {})
        for c in containers(spec):
            sc = c.get("securityContext", {})
            where = f"{owner['kind']}/{owner['metadata']['name']}:{c['name']}"
            assert sc.get("runAsNonRoot", pod_sc.get("runAsNonRoot")) is True, where
            assert sc.get("allowPrivilegeEscalation") is False, where
            assert sc.get("readOnlyRootFilesystem") is True, where
            assert sc.get("capabilities", {}).get("drop") == ["ALL"], where
            for kind in ("requests", "limits"):
                assert {"cpu", "memory"} <= set(c["resources"].get(kind, {})), where
        assert spec.get("automountServiceAccountToken") is False


# Scenarios: Budget check / Full local stack / Full local stack with logging
@pytest.mark.parametrize("sets", [(), (DEPLOYMENT_MODE,)], ids=["kserve", "deployment"])
def test_local_profile_fits_the_budget(sets: tuple[str, ...]) -> None:
    docs = render("values-local.yaml", *sets)
    assert ("InferenceService" in kinds(docs)) == (not sets)
    cpu_requests = mem_requests = mem_limits = 0.0
    for owner, spec, replicas in pod_specs(docs):
        assert replicas == 1, f"{owner['metadata']['name']} must run one replica locally"
        for c in budget_containers(spec):
            cpu_requests += replicas * cpu(c["resources"]["requests"]["cpu"])
            mem_requests += replicas * memory(c["resources"]["requests"]["memory"])
            mem_limits += replicas * memory(c["resources"]["limits"]["memory"])
    volumes = volume_bytes(docs)
    totals = (
        f"cpu requests={cpu_requests:.2f}, memory requests={mem_requests / 1024**2:.0f}Mi, "
        f"memory limits={mem_limits / 1024**2:.0f}Mi, volumes={volumes / 1024**3:.1f}Gi"
    )
    assert cpu_requests <= MAX_CPU_REQUESTS, totals
    assert mem_requests <= MAX_MEMORY_REQUESTS, totals
    assert mem_limits <= MAX_MEMORY_LIMITS, totals
    assert volumes <= MAX_VOLUMES, totals


def test_all_components_render_locally() -> None:
    names = {(d["kind"], d["metadata"]["name"]) for d in render("values-local.yaml")}
    assert {
        ("InferenceService", "wine-quality"),
        ("Deployment", "e2e-mlops-feature-logger"),
        ("Service", "e2e-mlops-feature-logger"),
        ("NetworkPolicy", "e2e-mlops-feature-logger"),
        ("Deployment", "e2e-mlops-mlflow"),
        ("StatefulSet", "e2e-mlops-postgres"),
        ("StatefulSet", "e2e-mlops-seaweedfs"),
        ("CronJob", "e2e-mlops-training"),
        ("Job", "e2e-mlops-bucket-init"),
        ("Job", "e2e-mlops-mlflow-bootstrap"),
    } <= names


def test_training_can_be_disabled() -> None:
    docs = render("values-local.yaml", "training.enabled=false")
    assert "CronJob" not in kinds(docs)
    assert "Deployment" in kinds(docs)


@pytest.mark.parametrize("mode", ["kserve", "deployment"])
def test_inference_can_be_disabled(mode: str) -> None:
    docs = render("values-local.yaml", "inference.enabled=false", f"inference.mode={mode}")
    assert not any(d["metadata"]["name"].endswith("-inference") for d in docs)
    assert "InferenceService" not in kinds(docs)
    assert not any("feature-logger" in d["metadata"]["name"] for d in docs)


def test_tracking_can_be_disabled_with_training() -> None:
    docs = render(
        "values-local.yaml",
        "tracking.enabled=false",
        "training.enabled=false",
        "featureLog.enabled=false",
    )
    assert {"StatefulSet", "Job", "CronJob"}.isdisjoint(kinds(docs))
    assert not any("mlflow" in d["metadata"]["name"] for d in docs)


def test_training_requires_tracking() -> None:
    with pytest.raises(subprocess.CalledProcessError) as exc:
        render("values-local.yaml", "tracking.enabled=false")
    assert "requires tracking.enabled" in exc.value.stderr


# Scenario: External object store
def test_external_object_store_renders_no_seaweedfs() -> None:
    docs = render("values-openstack.yaml", *OPENSTACK_EXTERNAL)
    assert not any("seaweedfs" in d["metadata"]["name"] for d in docs)
    mlflow = named(docs, "Deployment", "-mlflow")["spec"]["template"]["spec"]["containers"][0]
    assert env_of(mlflow)["MLFLOW_S3_ENDPOINT_URL"] == "https://s3.example.org"


def test_external_mode_requires_endpoint() -> None:
    with pytest.raises(subprocess.CalledProcessError) as exc:
        render("values-openstack.yaml", "objectStore.external.endpoint=")
    assert "objectStore.external.endpoint is required" in exc.value.stderr


def test_unknown_object_store_mode_is_rejected() -> None:
    with pytest.raises(subprocess.CalledProcessError):
        render("values-local.yaml", "objectStore.mode=cloud")


# Scenario: Train, promote, serve (no shared volume)
@pytest.mark.parametrize("sets", [(), (DEPLOYMENT_MODE,)], ids=["kserve", "deployment"])
def test_no_volume_shared_between_training_and_inference(sets: tuple[str, ...]) -> None:
    docs = render("values-local.yaml", *sets)
    inference = inference_pod(docs)
    cron = named(docs, "CronJob", "-training")["spec"]["jobTemplate"]["spec"]["template"]["spec"]

    def claims(spec: dict[str, Any]) -> set[str]:
        return {
            v["persistentVolumeClaim"]["claimName"]
            for v in spec.get("volumes", [])
            if "persistentVolumeClaim" in v
        }

    assert claims(inference) == set() and claims(cron) == set()
    assert "affinity" not in inference and "affinity" not in cron
    assert "PersistentVolumeClaim" not in kinds(docs)  # the Epic 1 model PVC is gone


@pytest.mark.parametrize("sets", [(), (DEPLOYMENT_MODE,)], ids=["kserve", "deployment"])
def test_training_and_inference_use_mlflow_with_own_credentials(sets: tuple[str, ...]) -> None:
    docs = render("values-local.yaml", *sets)
    inference = inference_pod(docs)
    trainer = named(docs, "CronJob", "-training")["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    inf_env, tr_env = env_of(inference["containers"][0]), env_of(trainer["containers"][0])
    assert inf_env["MODEL_URI"] == "models:/wine-quality@champion"
    for env in (inf_env, tr_env):
        assert env["MLFLOW_TRACKING_URI"] == "http://e2e-mlops-mlflow:5000"
    assert inf_env["MLFLOW_TRACKING_PASSWORD"]["secretKeyRef"]["name"] == "e2e-mlops-mlflow-serving"
    assert tr_env["MLFLOW_TRACKING_PASSWORD"]["secretKeyRef"]["name"] == "e2e-mlops-mlflow-trainer"
    assert trainer["containers"][0]["image"] == "localhost/e2e-mlops/trainer:local"


# Spec "Secrets from Kubernetes Secrets" / tracking-platform credentials
def test_credentials_only_by_secret_reference() -> None:
    sensitive = ("PASSWORD", "SECRET", "ACCESS_KEY", "TOKEN", "SIGNING_KEY")
    for _, spec, _ in pod_specs(render("values-local.yaml")):
        for c in containers(spec):
            for env in c.get("env", []):
                if any(word in env["name"].upper() for word in sensitive):
                    assert "secretKeyRef" in env.get("valueFrom", {}), env["name"]
    mlflow = named(render(), "Deployment", "-mlflow")["spec"]["template"]["spec"]["containers"][0]
    uri = env_of(mlflow)["MLFLOW_BACKEND_STORE_URI"]
    assert "$(PG_PASSWORD)" in uri and uri.startswith("postgresql+psycopg2://")


def test_tracking_server_wiring() -> None:
    docs = render("values-local.yaml")
    mlflow = named(docs, "Deployment", "-mlflow")["spec"]["template"]["spec"]["containers"][0]
    env = env_of(mlflow)
    assert mlflow["image"] == "localhost/e2e-mlops/mlflow-server:local"
    assert env["MLFLOW_ARTIFACTS_DESTINATION"] == "s3://mlflow-artifacts"
    assert env["MLFLOW_S3_ENDPOINT_URL"] == "http://e2e-mlops-seaweedfs:8333"
    hosts = env["MLFLOW_SERVER_ALLOWED_HOSTS"].split(",")
    assert {"e2e-mlops-mlflow:5000", "e2e-mlops-mlflow.e2e-mlops.svc.cluster.local:5000"} <= set(
        hosts
    )
    assert named(docs, "Service", "-mlflow")["spec"]["type"] == "ClusterIP"


def test_hooks_order_bucket_before_users() -> None:
    docs = render("values-local.yaml")
    bucket = named(docs, "Job", "-bucket-init")["metadata"]["annotations"]
    users = named(docs, "Job", "-mlflow-bootstrap")["metadata"]["annotations"]
    for hook in (bucket, users):
        assert hook["helm.sh/hook"] == "post-install,post-upgrade"
    assert int(bucket["helm.sh/hook-weight"]) < int(users["helm.sh/hook-weight"])


def test_openstack_skips_bucket_creation_and_uses_registry() -> None:
    docs = render(
        "values-openstack.yaml",
        *OPENSTACK_EXTERNAL,
        "image.repository=registry.example.org/group/e2e-mlops",
        "image.tag=abc1234",
        DEPLOYMENT_MODE,
    )
    assert not any(d["metadata"]["name"].endswith("-bucket-init") for d in docs)
    deployment = named(docs, "Deployment", "-inference")
    pod = deployment["spec"]["template"]["spec"]
    assert pod["imagePullSecrets"] == [{"name": "gitlab-registry"}]
    assert pod["containers"][0]["image"] == "registry.example.org/group/e2e-mlops/inference:abc1234"
    assert deployment["spec"]["replicas"] == 2
    assert "strategy" not in deployment["spec"]  # rolling update again: no RWO volume


def test_mlflow_ingress_requires_tls() -> None:
    docs = render(
        "values-openstack.yaml",
        *OPENSTACK_EXTERNAL,
        "tracking.ingress.enabled=true",
        "tracking.ingress.host=mlflow.example.org",
    )
    ingress = named(docs, "Ingress", "-mlflow")
    assert ingress["spec"]["tls"][0]["hosts"] == ["mlflow.example.org"]


@pytest.mark.parametrize(
    ("sets", "port"), [((), 8080), ((DEPLOYMENT_MODE,), "http")], ids=["kserve", "deployment"]
)
def test_probes_bound_to_health_endpoints(sets: tuple[str, ...], port: Any) -> None:
    [container] = inference_pod(render("values-local.yaml", *sets))["containers"]
    assert container["livenessProbe"]["httpGet"] == {"path": "/health/live", "port": port}
    assert container["readinessProbe"]["httpGet"] == {"path": "/health/ready", "port": port}


def test_training_job_is_a_suspended_template() -> None:
    cron = named(render(), "CronJob", "-training")
    assert cron["spec"]["suspend"] is True


@pytest.mark.parametrize(("value", "expected"), [("100m", 0.1), ("1", 1.0), (2, 2.0), ("1.5", 1.5)])
def test_cpu_parser(value, expected) -> None:
    assert cpu(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("192Mi", 192 * 1024**2), ("1Gi", 1024**3), ("500M", 500 * 1000**2), (1024, 1024)],
)
def test_memory_parser(value, expected) -> None:
    assert memory(value) == expected


# ------------------------------------------------------------------ Epic 3: serving mode
def isvc(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return next(d for d in docs if d["kind"] == "InferenceService")


# Scenario: Deployment fallback / Serving through KServe
@pytest.mark.parametrize("values_file", ["values-local.yaml", "values-openstack.yaml"])
def test_each_mode_renders_the_right_kind(values_file: str) -> None:
    kserve = render(values_file, *OPENSTACK_EXTERNAL, "inference.ingress.host=wine.example.org")
    assert isvc(kserve)["metadata"]["name"] == "wine-quality"
    assert not any(d["metadata"]["name"].endswith("-inference") for d in kserve)
    deployment = render(values_file, *OPENSTACK_EXTERNAL, DEPLOYMENT_MODE)
    assert "InferenceService" not in kinds(deployment)
    assert named(deployment, "Deployment", "-inference")
    assert named(deployment, "Service", "-inference")
    assert not any("feature-logger" in d["metadata"]["name"] for d in deployment)


def test_default_values_render_without_kserve() -> None:
    docs = render(None)
    assert "InferenceService" not in kinds(docs)
    assert named(docs, "Deployment", "-inference")


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(subprocess.CalledProcessError) as exc:
        render("values-local.yaml", "inference.mode=serverless")
    assert "inference.mode must be deployment or kserve" in exc.value.stderr


# Scenario: Local mode / Local replicas
def test_local_runs_standard_mode_with_one_replica() -> None:
    service = isvc(render("values-local.yaml"))
    annotations = service["metadata"]["annotations"]
    assert annotations["serving.kserve.io/deploymentMode"] == "Standard"  # RawDeployment
    assert annotations["serving.kserve.io/autoscalerClass"] == "hpa"
    predictor = service["spec"]["predictor"]
    assert predictor["minReplicas"] == predictor["maxReplicas"] == 1
    assert (predictor["scaleMetric"], predictor["scaleTarget"]) == ("cpu", 80)


# Scenario: OpenStack mode / Idle service on OpenStack (rendered side)
def test_openstack_runs_knative_mode_scaling_to_zero() -> None:
    service = isvc(render("values-openstack.yaml", *OPENSTACK_EXTERNAL))
    annotations = service["metadata"]["annotations"]
    assert annotations["serving.kserve.io/deploymentMode"] == "Knative"  # Serverless
    assert "serving.kserve.io/autoscalerClass" not in annotations
    predictor = service["spec"]["predictor"]
    assert (predictor["minReplicas"], predictor["maxReplicas"]) == (0, 3)
    assert (predictor["scaleMetric"], predictor["scaleTarget"]) == ("concurrency", 5)


def test_predictor_runs_the_inference_image_hardened() -> None:
    predictor = isvc(render("values-local.yaml"))["spec"]["predictor"]
    [container] = predictor["containers"]
    assert container["name"] == "kserve-container"
    assert container["image"] == "localhost/e2e-mlops/inference:local"
    assert container["ports"] == [{"containerPort": 8080, "protocol": "TCP"}]
    assert predictor["serviceAccountName"] == "e2e-mlops"
    assert predictor["automountServiceAccountToken"] is False
    # Knative rejects a pod-level securityContext (feature kubernetes.podspec-securitycontext
    # is off by default): found on a Knative cluster, so the settings are per container.
    assert "securityContext" not in predictor
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
        "runAsNonRoot": True,
        "runAsUser": 10001,
        "runAsGroup": 10001,
        "seccompProfile": {"type": "RuntimeDefault"},
    }


# Scenario: New champion rolled out / Pinned version survives (rendered side)
@pytest.mark.parametrize("sets", [(), (DEPLOYMENT_MODE,)], ids=["kserve", "deployment"])
def test_model_uri_pinned_when_champion_version_set(sets: tuple[str, ...]) -> None:
    def model_uri(*extra: str) -> str:
        [container] = inference_pod(render("values-local.yaml", *sets, *extra))["containers"]
        return env_of(container)["MODEL_URI"]

    assert model_uri() == "models:/wine-quality@champion"
    assert model_uri("inference.championVersion=5") == "models:/wine-quality/5"
    assert model_uri("inference.championVersion=12") == "models:/wine-quality/12"


@pytest.mark.parametrize("version", ["abc", "0", "1.5", "-2"])
def test_invalid_champion_version_is_rejected(version: str) -> None:
    with pytest.raises(subprocess.CalledProcessError) as exc:
        subprocess.run(
            [
                HELM,
                "template",
                "e2e-mlops",
                str(CHART),
                "-f",
                str(CHART / "values-local.yaml"),
                "--set-string",
                f"inference.championVersion={version}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    assert "championVersion must be a registry version number" in exc.value.stderr


# ------------------------------------------------------------------ Epic 3: feature log
def test_predictor_logs_every_call_to_the_feature_logger() -> None:
    docs = render("values-local.yaml")
    logger = isvc(docs)["spec"]["predictor"]["logger"]
    assert logger == {
        "mode": "all",
        "url": "http://e2e-mlops-feature-logger.e2e-mlops.svc.cluster.local/",
    }
    service = named(docs, "Service", "-feature-logger")
    assert service["spec"]["type"] == "ClusterIP" and service["spec"]["ports"][0]["port"] == 80


def test_feature_log_can_be_disabled() -> None:
    docs = render("values-local.yaml", "featureLog.enabled=false")
    assert "logger" not in isvc(docs)["spec"]["predictor"]
    assert not any("feature-logger" in d["metadata"]["name"] for d in docs)
    bootstrap = named(docs, "Job", "-mlflow-bootstrap")["spec"]["template"]["spec"]
    assert "FEATURE_LOG_PASSWORD" not in env_of(bootstrap["containers"][0])


def test_feature_log_requires_tracking() -> None:
    with pytest.raises(subprocess.CalledProcessError) as exc:
        render("values-local.yaml", "tracking.enabled=false", "training.enabled=false")
    assert "featureLog.enabled requires tracking.enabled" in exc.value.stderr


# Scenario: Separate credentials
def test_feature_logger_has_only_its_own_credentials() -> None:
    docs = render("values-local.yaml")
    pod = named(docs, "Deployment", "-feature-logger")["spec"]["template"]["spec"]
    [container] = pod["containers"]
    env = env_of(container)
    assert container["image"] == "localhost/e2e-mlops/feature-logger:local"
    assert env["FEATURE_LOG_DB_PASSWORD"]["secretKeyRef"] == {
        "name": "e2e-mlops-feature-log",
        "key": "password",
    }
    assert env["FEATURE_LOG_DATABASE_URL"] == (
        "postgresql+psycopg2://$(FEATURE_LOG_DB_USER)@e2e-mlops-postgres:5432/feature_log"
    )
    secrets = {
        e["valueFrom"]["secretKeyRef"]["name"]
        for e in container.get("env", [])
        if "secretKeyRef" in e.get("valueFrom", {})
    }
    assert secrets == {"e2e-mlops-feature-log"}  # no MLflow, S3 or PostgreSQL admin Secret
    assert not any(k.startswith(("MLFLOW_", "AWS_", "S3_")) for k in env)
    assert "volumes" not in pod or all("secret" not in v for v in pod["volumes"])


def test_feature_logger_probes_do_not_wait_for_the_hook() -> None:
    pod = named(render("values-local.yaml"), "Deployment", "-feature-logger")
    [container] = pod["spec"]["template"]["spec"]["containers"]
    # Helm waits for readiness before the post-install hook creates the database.
    assert container["readinessProbe"]["httpGet"]["path"] == "/health/live"


def test_only_predictor_pods_reach_the_feature_logger() -> None:
    policy = named(render("values-local.yaml"), "NetworkPolicy", "-feature-logger")
    assert policy["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == (
        "feature-logger"
    )
    [rule] = policy["spec"]["ingress"]
    assert rule["from"] == [
        {"podSelector": {"matchLabels": {"serving.kserve.io/inferenceservice": "wine-quality"}}}
    ]
    assert rule["ports"] == [{"protocol": "TCP", "port": 8080}]
    docs = render("values-local.yaml", "featureLog.networkPolicy.enabled=false")
    assert "NetworkPolicy" not in kinds(docs)


def test_bootstrap_hook_creates_the_feature_log_database() -> None:
    hook = named(render("values-local.yaml"), "Job", "-mlflow-bootstrap")
    env = env_of(hook["spec"]["template"]["spec"]["containers"][0])
    assert env["PGHOST"] == "e2e-mlops-postgres" and env["PGPORT"] == "5432"
    assert env["POSTGRES_ADMIN_PASSWORD"]["secretKeyRef"]["name"] == "e2e-mlops-postgres"
    assert env["FEATURE_LOG_PASSWORD"]["secretKeyRef"]["name"] == "e2e-mlops-feature-log"
    assert env["FEATURE_LOG_USERNAME"]["secretKeyRef"]["key"] == "username"
    assert env["FEATURE_LOG_DATABASE"] == "feature_log"
    deployment_mode = named(
        render("values-local.yaml", DEPLOYMENT_MODE), "Job", "-mlflow-bootstrap"
    )
    assert "FEATURE_LOG_PASSWORD" not in env_of(
        deployment_mode["spec"]["template"]["spec"]["containers"][0]
    )


def test_logger_sidecar_is_counted_in_the_budget() -> None:
    predictor = isvc(render("values-local.yaml"))["spec"]["predictor"]
    names = [c["name"] for c in budget_containers(predictor)]
    assert names == ["kserve-container", "agent"]
    assert logger_sidecar()["resources"]["limits"]["memory"] == "128Mi"


def test_hook_jobs_have_a_writable_tmp() -> None:
    # Found on the first real cluster run (Epic 3 task 6.2): MLflow's auth client needs a
    # temp directory and the root filesystem is read-only.
    hook = named(render("values-local.yaml"), "Job", "-mlflow-bootstrap")["spec"]["template"]
    [container] = hook["spec"]["containers"]
    assert {"name": "tmp", "mountPath": "/tmp"} in container["volumeMounts"]
    assert any(v["name"] == "tmp" and "emptyDir" in v for v in hook["spec"]["volumes"])

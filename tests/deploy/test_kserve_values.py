"""Serving add-on values (deploy/kserve/): pinned versions and the local footprint.

Spec model-serving, "Local serving add-ons footprint": every add-on container
declares requests and limits, summed memory requests <= 512 MiB, logger sidecar
<= 64 MiB requested / 128 MiB limit. Needs no helm: the values files list every
container the add-on charts run (checked against the rendered charts in task 1.1).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.deploy.test_chart import cpu, memory

ADDONS = Path(__file__).resolve().parents[2] / "deploy" / "kserve"
MAX_ADDON_MEMORY_REQUESTS = 512 * 1024**2

# Every container the charts run, as the values path of its `resources`.
CERT_MANAGER_CONTAINERS = ("", "webhook", "cainjector", "startupapicheck")
KSERVE_CONTAINERS = ("kserve.controller", "kserve.controller.rbacProxy")


def load(name: str) -> dict[str, Any]:
    return yaml.safe_load((ADDONS / name).read_text())


def versions() -> dict[str, str]:
    pairs = (
        line.split("=", 1)
        for line in (ADDONS / "versions.env").read_text().splitlines()
        if line and not line.startswith("#")
    )
    return {key: value for key, value in pairs}


def lookup(values: dict[str, Any], path: str) -> dict[str, Any]:
    node = values
    for part in filter(None, path.split(".")):
        node = node[part]
    return node["resources"]


def addon_resources(target: str) -> dict[str, dict[str, Any]]:
    cm = load(f"values-cert-manager-{target}.yaml")
    ks = load(f"values-kserve-{target}.yaml")
    found = {f"cert-manager:{p or 'controller'}": lookup(cm, p) for p in CERT_MANAGER_CONTAINERS}
    found.update({f"kserve:{p}": lookup(ks, p) for p in KSERVE_CONTAINERS})
    return found


@pytest.mark.parametrize("target", ["local", "openstack"])
def test_every_addon_container_has_requests_and_limits(target: str) -> None:
    for where, resources in addon_resources(target).items():
        for kind in ("requests", "limits"):
            assert {"cpu", "memory"} <= set(resources.get(kind, {})), f"{target} {where}"
            assert cpu(resources["requests"]["cpu"]) <= cpu(resources["limits"]["cpu"]), where


# Scenario: Add-on values check
def test_local_addon_memory_requests_within_512mi() -> None:
    total = sum(memory(r["requests"]["memory"]) for r in addon_resources("local").values())
    assert total <= MAX_ADDON_MEMORY_REQUESTS, f"{total / 1024**2:.0f}Mi"


@pytest.mark.parametrize("target", ["local", "openstack"])
def test_logger_sidecar_is_small(target: str) -> None:
    sidecar = load(f"values-kserve-{target}.yaml")["loggerSidecar"]
    assert memory(sidecar["memoryRequest"]) <= 64 * 1024**2
    assert memory(sidecar["memoryLimit"]) <= 128 * 1024**2
    assert cpu(sidecar["cpuRequest"]) <= cpu(sidecar["cpuLimit"])


def test_deployment_modes() -> None:
    # Standard = RawDeployment, Knative = Serverless (renamed in KServe 0.15).
    assert load("values-kserve-local.yaml")["kserve"]["controller"]["deploymentMode"] == "Standard"
    openstack = load("values-kserve-openstack.yaml")["kserve"]["controller"]
    assert openstack["deploymentMode"] == "Knative"


def test_local_images_are_never_pulled_again() -> None:
    controller = load("values-kserve-local.yaml")["kserve"]["controller"]
    assert controller["imagePullPolicy"] == "IfNotPresent"


def test_versions_and_image_list_agree() -> None:
    pinned = versions()
    images = [
        line.strip()
        for line in (ADDONS / "images.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    cm_version, ks_version = pinned["CERT_MANAGER_VERSION"], pinned["KSERVE_VERSION"]
    assert cm_version in pinned["CERT_MANAGER_CHART_URL"]
    for key in ("KSERVE_CRD_CHART_URL", "KSERVE_RESOURCES_CHART_URL"):
        assert f"/{ks_version}/" in pinned[key]
    for key in (
        "CERT_MANAGER_CHART_SHA256",
        "KSERVE_CRD_CHART_SHA256",
        "KSERVE_RESOURCES_CHART_SHA256",
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", pinned[key]), key
    for part in ("controller", "webhook", "cainjector", "startupapicheck"):
        assert f"quay.io/jetstack/cert-manager-{part}:{cm_version}" in images
    for target in ("local", "openstack"):
        ks = load(f"values-kserve-{target}.yaml")["kserve"]
        assert f"{ks['controller']['image']}:{ks['controller']['tag']}" in images
        assert f"{ks['agent']['image']}:{ks['agent']['tag']}" in images
        assert ks["controller"]["rbacProxyImage"] in images
        assert ks["controller"]["tag"] == ks["agent"]["tag"] == ks_version

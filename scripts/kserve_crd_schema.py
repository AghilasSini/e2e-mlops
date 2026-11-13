"""Write kubeconform JSON schemas for KServe's InferenceService, from the pinned CRD.

    python scripts/kserve_crd_schema.py <out-dir> [--chart <kserve-crd .tgz>]

Without ``--chart``, downloads the kserve-crd chart pinned in deploy/kserve/versions.env
and checks its SHA-256. Writes ``inferenceservice_<version>.json`` for every served
version of the CRD, made strict like kubeconform's ``openapi2jsonschema``: objects that
declare properties reject unknown ones (unless the CRD preserves unknown fields), and
``x-kubernetes-int-or-string`` accepts both types. Use with::

    kubeconform -strict -schema-location default \\
      -schema-location '<out-dir>/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSIONS = REPO_ROOT / "deploy" / "kserve" / "versions.env"
CRD_MEMBER = "kserve-crd/templates/serving.kserve.io_inferenceservices.yaml"


def pinned() -> dict[str, str]:
    pairs = (
        line.split("=", 1)
        for line in VERSIONS.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    )
    return {key.strip(): value.strip() for key, value in pairs}


def download_chart() -> bytes:
    versions = pinned()
    url, sha256 = versions["KSERVE_CRD_CHART_URL"], versions["KSERVE_CRD_CHART_SHA256"]
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - pinned URL
        payload = response.read()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != sha256:
        raise SystemExit(f"checksum mismatch for {url}: expected {sha256}, got {digest}")
    return payload


def strict(node: Any) -> Any:
    """Recursively apply kubeconform's strict conventions to an OpenAPI v3 schema."""
    if isinstance(node, list):
        return [strict(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: strict(value) for key, value in node.items()}
    if out.pop("x-kubernetes-int-or-string", False):
        out.pop("type", None)
        out["oneOf"] = [{"type": "string"}, {"type": "integer"}]
    preserve = out.pop("x-kubernetes-preserve-unknown-fields", False)
    if "properties" in out and "additionalProperties" not in out and not preserve:
        out["additionalProperties"] = False
    return out


def schemas(crd: dict[str, Any]) -> dict[str, dict[str, Any]]:
    kind = crd["spec"]["names"]["kind"].lower()
    found = {}
    for version in crd["spec"]["versions"]:
        if not version.get("served", True):
            continue
        schema = strict(version["schema"]["openAPIV3Schema"])
        # apiVersion/kind/metadata are always allowed on a top-level resource.
        schema.setdefault("properties", {}).update(
            {"apiVersion": {"type": "string"}, "kind": {"type": "string"}, "metadata": {}}
        )
        schema["$schema"] = "http://json-schema.org/draft-07/schema#"
        found[f"{kind}_{version['name']}.json"] = schema
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--chart", type=Path, help="local kserve-crd chart archive")
    args = parser.parse_args(argv)
    payload = args.chart.read_bytes() if args.chart else download_chart()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        member = archive.extractfile(CRD_MEMBER)
        if member is None:
            raise SystemExit(f"{CRD_MEMBER} not found in the chart")
        crd = yaml.safe_load(member)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, schema in schemas(crd).items():
        (args.out_dir / name).write_text(json.dumps(schema))
        print(f"wrote {args.out_dir / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

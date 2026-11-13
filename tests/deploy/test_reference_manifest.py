"""deploy/kserve/inferenceservice.yaml must equal what the chart renders (spec
model-serving "Reference manifest"). Regenerate it with:

    python tests/deploy/test_reference_manifest.py --write
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "deploy" / "helm" / "e2e-mlops"
REFERENCE = REPO_ROOT / "deploy" / "kserve" / "inferenceservice.yaml"
HELM = os.environ.get("HELM_BIN") or shutil.which("helm")
HEADER = """\
# Reference InferenceService: the chart rendered with the local values. Generated, do
# not edit; CI fails when it differs from the chart. Regenerate with
#   python tests/deploy/test_reference_manifest.py --write
"""


def rendered() -> str:
    out = subprocess.run(
        [
            HELM,
            "template",
            "e2e-mlops",
            str(CHART),
            "--namespace",
            "e2e-mlops",
            "-f",
            str(CHART / "values-local.yaml"),
            "--show-only",
            "templates/inferenceservice.yaml",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return HEADER + out


@pytest.mark.skipif(HELM is None, reason="helm binary not available")
def test_reference_manifest_is_up_to_date() -> None:
    relative = REFERENCE.relative_to(REPO_ROOT)
    assert REFERENCE.exists(), f"{relative} is missing: python {Path(__file__).name} --write"
    assert REFERENCE.read_text() == rendered(), (
        f"{relative} is stale: the InferenceService template changed. "
        f"Regenerate it with `python tests/deploy/test_reference_manifest.py --write`."
    )


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"] or HELM is None:
        sys.exit("usage: python tests/deploy/test_reference_manifest.py --write (needs helm)")
    REFERENCE.write_text(rendered())
    print(f"wrote {REFERENCE.relative_to(REPO_ROOT)}")

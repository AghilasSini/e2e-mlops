"""Real KServe v0.21.0 logger events captured in task 1.2 (tests/feature_log/fixtures/)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_events() -> dict[str, dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for path in sorted(FIXTURES.glob("kserve-*.json")):
        for event in json.loads(path.read_text())["events"]:
            events[event["name"]] = event
    return events


@pytest.fixture(scope="session")
def captured() -> dict[str, dict[str, Any]]:
    """Events by name, e.g. ``predict-batch-request``, ``oip-infer-response``."""
    return load_events()

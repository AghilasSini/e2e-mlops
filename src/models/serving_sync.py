"""Resolve the registry champion to the exact version the serving release should pin.

Usage::

    python -m models.serving_sync resolve

Prints one JSON line, for example::

    {"champion_version": "5", "model_name": "wine-quality", "resolved_at": "2026-11-11T10:00:00Z"}

and exits 0. Exits 1 when no version holds the champion alias, and 2 when the registry
cannot be reached or answers with an error. Nothing is printed on stdout in either case.

``train-and-promote`` ends with this command, so the training Job's *last log line* names
the champion. ``scripts/serve-champion.sh --from-job`` reads that line and pins the
version in the Helm release. The MLflow server is only reachable inside the cluster,
which is why the version is resolved by the Job.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from common.logging import configure_logging, get_logger
from models.registry_manager import build_manager

log = get_logger(__name__)


def resolve(env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """The champion as ``{"champion_version", "model_name", "resolved_at"}``, or ``None``."""
    manager = build_manager(env)
    champion = manager.champion()
    if champion is None:
        return None
    return {
        "champion_version": str(champion.version),
        "model_name": manager.model_name,
        "resolved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="python -m models.serving_sync")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("resolve", help="print the champion's exact version as JSON")
    parser.parse_args(argv)
    try:
        champion = resolve(env)
    except Exception:  # noqa: BLE001 - unreachable registry, bad config, ...
        log.exception("could not resolve the champion")
        return 2
    if champion is None:
        log.error("no version holds the champion alias: nothing to serve")
        return 1
    print(json.dumps(champion, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

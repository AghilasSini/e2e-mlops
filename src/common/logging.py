"""Process-wide logging setup: one stream handler, level from ``LOG_LEVEL``."""

from __future__ import annotations

import logging
import os
import sys

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str | None = None) -> None:
    """Configure the root logger once. Safe to call several times."""
    root = logging.getLogger()
    resolved = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    root.setLevel(resolved)
    if not any(getattr(h, "_e2e_mlops", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler._e2e_mlops = True  # type: ignore[attr-defined]
        root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

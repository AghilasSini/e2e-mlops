"""Loads the model once and serves predictions from memory.

``MODEL_URI`` can be:

* ``models:/<name>@<alias>`` (default ``models:/wine-quality@champion``): the version
  holding the alias in the MLflow registry at ``MLFLOW_TRACKING_URI`` (or the local
  development store when unset). ``model_version`` is reported as ``<name>/<version>``.
* ``models:/<name>/<version>``: that exact registered version.
* a local ``.joblib`` path (or ``file://`` URI), for offline development.

When the alias or file does not exist yet, the service stays alive but not ready
(503), and a restart picks up a model created later.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import joblib
import pandas as pd

from common.logging import get_logger
from data.features import FEATURE_COLUMNS

log = get_logger(__name__)
VERSION_FILE = "model_version"
_REGISTRY_URI = re.compile(r"^models:/(?P<name>[^/@]+)(?:@(?P<alias>[^/]+)|/(?P<version>\d+))$")


class ModelUnavailableError(RuntimeError):
    """No model is loaded: the service is alive but not ready."""


@dataclass(frozen=True)
class Prediction:
    prediction: int
    probability: float

    @property
    def label(self) -> str:
        return "good" if self.prediction == 1 else "not_good"


def _local_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme in ("", "file"):
        return Path(parsed.path if parsed.scheme else uri)
    raise ValueError(f"unsupported MODEL_URI scheme '{parsed.scheme}' (expected a file path)")


class ModelStore:
    """Holds at most one model, loaded from ``uri`` by :meth:`load`."""

    def __init__(self, uri: str, env: Mapping[str, str] | None = None) -> None:
        self.uri = uri
        self._env = os.environ if env is None else env
        self._model = None
        self._version: str | None = None
        self._lock = threading.Lock()
        self.load_count = 0
        self._last_attempt: float | None = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    @property
    def version(self) -> str | None:
        return self._version

    def load(self) -> bool:
        """Load the model; return ``False`` (and stay not-ready) if it does not exist yet."""
        with self._lock:
            self._last_attempt = time.monotonic()
            match = _REGISTRY_URI.match(self.uri)
            if match:
                return self._load_from_registry(**match.groupdict())
            if self.uri.startswith("models:/"):
                raise ValueError(f"unsupported registry URI '{self.uri}'")
            path = _local_path(self.uri)
            if not path.is_file():
                log.warning("no model at %s; service not ready", path)
                return False
            self._model = joblib.load(path)
            self.load_count += 1
            version_file = path.with_name(VERSION_FILE)
            self._version = (
                version_file.read_text(encoding="utf-8").strip()
                if version_file.is_file()
                else path.name
            )
            log.info("loaded model %s from %s", self._version, path)
            return True

    def _load_from_registry(self, name: str, alias: str | None, version: str | None) -> bool:
        import mlflow.sklearn
        from mlflow import MlflowClient
        from mlflow.exceptions import MlflowException

        from common.config import load_settings
        from training.tracking import resolve_target

        target = resolve_target(self._env, load_settings(env=self._env).output_dir)
        client = MlflowClient(tracking_uri=target.uri, registry_uri=target.uri)
        try:
            if alias is not None:
                version = str(client.get_model_version_by_alias(name, alias).version)
            else:
                client.get_model_version(name, version)
        except MlflowException as exc:
            if exc.error_code in ("RESOURCE_DOES_NOT_EXIST", "INVALID_PARAMETER_VALUE"):
                log.warning("no model for %s in %s; service not ready", self.uri, target.uri)
                return False
            raise
        mlflow.set_tracking_uri(target.uri)
        mlflow.set_registry_uri(target.uri)
        # Load the resolved version, not the alias: a promotion that lands while we load
        # must not mix one version's number with another version's weights.
        self._model = mlflow.sklearn.load_model(f"models:/{name}/{version}")
        self.load_count += 1
        self._version = f"{name}/{version}"
        log.info("loaded model %s (%s) from %s", self._version, self.uri, target.uri)
        return True

    def ensure_loaded(self, min_interval: float = 30.0) -> bool:
        """Ready, or retry loading when the last attempt is older than ``min_interval``.

        Lets a pod that started before the registry (or before a champion existed)
        become ready without a restart. A loaded model is never reloaded.
        """
        if self.ready:
            return True
        if self._last_attempt is not None and time.monotonic() - self._last_attempt < min_interval:
            return False
        try:
            return self.load()
        except Exception:  # noqa: BLE001 - stay not-ready; the next probe retries
            log.exception("model load from %s failed", self.uri)
            return False

    def predict(self, rows: list[list[float]]) -> list[Prediction]:
        if self._model is None:
            raise ModelUnavailableError("model not loaded")
        frame = pd.DataFrame(rows, columns=list(FEATURE_COLUMNS))
        probabilities = self._model.predict_proba(frame)[:, 1]
        return [Prediction(prediction=int(p >= 0.5), probability=float(p)) for p in probabilities]

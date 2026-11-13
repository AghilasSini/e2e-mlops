"""MLflow tracking for training runs.

Every run records parameters and lineage, the seven test-set metrics, evaluation and
data artifacts, and the model itself. The model is registered as a new version of the
configured registered model, which does *not* change the ``champion`` alias:
promotion is a separate step (``python -m models.registry_manager``).

Where runs go:

* ``MLFLOW_TRACKING_URI`` set: that server (credentials from
  ``MLFLOW_TRACKING_USERNAME`` / ``MLFLOW_TRACKING_PASSWORD``). If it is unreachable
  or refuses the credentials, :func:`connect` raises before any model is fitted.
* not set: a local SQLite store inside the (git-ignored) output directory, with a
  warning naming it. This needs the full ``mlflow`` package (dev environment).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from common.logging import get_logger
from data.features import FEATURE_COLUMNS
from training.evaluate import METRIC_NAMES, write_json

log = get_logger(__name__)

ENV_TRACKING_URI = "MLFLOW_TRACKING_URI"
RUN_FILE = "run.json"
MODEL_ARTIFACT = "model"
# Plain CSV: 12 numeric columns, ~1,600 rows; avoids pyarrow (150 MB) in the trainer image.
REFERENCE_ARTIFACT = "data/reference.csv"
TEST_ARTIFACT = "data/test.csv"

# Fail fast instead of MLflow's default 7 retries with exponential backoff
# (minutes) when the server is down. Explicit environment values still win.
_HTTP_DEFAULTS = {"MLFLOW_HTTP_REQUEST_MAX_RETRIES": "2", "MLFLOW_HTTP_REQUEST_TIMEOUT": "15"}


class TrackingError(RuntimeError):
    """The tracking server is unreachable, refuses access, or logging failed."""


@dataclass(frozen=True)
class TrackingTarget:
    uri: str
    local: bool


@dataclass(frozen=True)
class RunRecord:
    """What a training run produced; written to ``<output_dir>/run.json``."""

    run_id: str
    experiment_id: str
    model_name: str
    model_version: str
    model_uri: str
    tracking_uri: str

    def write(self, output_dir: Path) -> Path:
        path = output_dir / RUN_FILE
        write_json(asdict(self), path)
        return path

    @classmethod
    def read(cls, path: Path) -> RunRecord:
        return cls(**json.loads(path.read_text(encoding="utf-8")))


def resolve_target(env: Mapping[str, str], output_dir: Path) -> TrackingTarget:
    uri = env.get(ENV_TRACKING_URI)
    if uri:
        return TrackingTarget(uri=uri, local=False)
    store = output_dir / "mlflow.db"
    log.warning("%s is not set: recording the run in the local store %s", ENV_TRACKING_URI, store)
    return TrackingTarget(uri=f"sqlite:///{store}", local=True)


def connect(target: TrackingTarget, experiment_name: str, output_dir: Path) -> str:
    """Point MLflow at ``target``, check it answers, and return the experiment id."""
    import mlflow
    from mlflow import MlflowClient

    for key, value in _HTTP_DEFAULTS.items():
        os.environ.setdefault(key, value)
    if target.local:
        output_dir.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(target.uri)
    mlflow.set_registry_uri(target.uri)
    try:
        client = MlflowClient(tracking_uri=target.uri, registry_uri=target.uri)
        client.search_experiments(max_results=1)  # preflight: reachable + authorized
        experiment = client.get_experiment_by_name(experiment_name)
        if experiment is not None:
            return experiment.experiment_id
        options = {}
        if target.local:
            options["artifact_location"] = (output_dir / "mlartifacts").resolve().as_uri()
        return client.create_experiment(experiment_name, **options)
    except Exception as exc:  # noqa: BLE001 - any client failure means "cannot track"
        raise TrackingError(
            f"tracking server {target.uri} is unreachable or refused access: {exc}"
        ) from exc


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def log_training_run(
    *,
    target: TrackingTarget,
    experiment_id: str,
    model_name: str,
    run_name: str,
    model: Any,
    params: Mapping[str, Any],
    metrics: Mapping[str, float | int],
    confusion: Mapping[str, Any],
    schema_path: Path,
    train: pd.DataFrame,
    test: pd.DataFrame,
) -> RunRecord:
    """Log one training run and register its model as a new version (no alias change)."""
    import mlflow
    import mlflow.sklearn
    from mlflow.models import infer_signature

    features = train.loc[:, list(FEATURE_COLUMNS)]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            write_json(dict(metrics), staging / "metrics.json")
            write_json(dict(confusion), staging / "confusion_matrix.json")
            (staging / "data").mkdir()
            train.to_csv(staging / REFERENCE_ARTIFACT, index=False)
            test.to_csv(staging / TEST_ARTIFACT, index=False)

            with mlflow.start_run(experiment_id=experiment_id, run_name=run_name) as run:
                mlflow.log_params({k: str(v) for k, v in params.items()})
                mlflow.log_metrics({name: float(metrics[name]) for name in METRIC_NAMES})
                mlflow.log_artifact(str(staging / "metrics.json"))
                mlflow.log_artifact(str(staging / "confusion_matrix.json"))
                mlflow.log_artifact(str(schema_path))
                mlflow.log_artifacts(str(staging / "data"), artifact_path="data")
                info = mlflow.sklearn.log_model(
                    model,
                    name=MODEL_ARTIFACT,
                    signature=infer_signature(features, model.predict(features)),
                    input_example=features.head(3),
                    registered_model_name=model_name,
                    pip_requirements=mlflow.sklearn.get_default_pip_requirements(
                        include_skops=True
                    ),
                )
                version = str(info.registered_model_version)
                client = mlflow.MlflowClient()
                for key in ("data_sha256", "git_sha", "image_tag"):
                    client.set_model_version_tag(model_name, version, key, str(params[key]))
                record = RunRecord(
                    run_id=run.info.run_id,
                    experiment_id=experiment_id,
                    model_name=model_name,
                    model_version=version,
                    model_uri=f"models:/{model_name}/{version}",
                    tracking_uri=target.uri,
                )
    except Exception as exc:  # noqa: BLE001 - surface as one tracking failure
        raise TrackingError(f"logging the run to {target.uri} failed: {exc}") from exc
    log.info("run %s registered as %s version %s", record.run_id, model_name, record.model_version)
    return record

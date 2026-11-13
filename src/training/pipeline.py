"""End-to-end training run.

validate → split → tracking preflight → fit → evaluate → write outputs → log to MLflow
and register a new model version → write ``run.json``.

Run it with ``python -m training``. The process exits 0 only when every step succeeds.
The tracking server is checked *before* fitting, so an unreachable server stops the run
early and nothing is registered. Local outputs (model, metrics, version) are written
only after evaluation succeeds; a failed run never leaves a model behind.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

import joblib

from common.config import Settings, load_settings
from common.logging import configure_logging, get_logger
from data.features import build_dataset
from data.schema import load_schema
from data.split import split_dataset
from data.validation import load_raw, validate
from training.evaluate import confusion, evaluate, write_metrics
from training.tracking import (
    RunRecord,
    connect,
    file_sha256,
    log_training_run,
    resolve_target,
)
from training.train import fit_model

log = get_logger(__name__)
T = TypeVar("T")


class StepError(RuntimeError):
    """A pipeline step failed; ``step`` names it."""

    def __init__(self, step: str, cause: BaseException) -> None:
        super().__init__(f"step '{step}' failed: {cause}")
        self.step = step


@dataclass(frozen=True)
class TrainingResult:
    model_path: Path
    metrics_path: Path
    version_path: Path
    model_version: str
    metrics: dict[str, float | int]
    run: RunRecord
    run_path: Path


def _git_sha(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return out.stdout.strip() or "nogit"
    except (OSError, subprocess.SubprocessError):
        return "nogit"


def model_version(root: Path, env: Mapping[str, str], now: datetime | None = None) -> str:
    """``$MODEL_VERSION`` if set, else ``<UTC timestamp>-<git sha>``."""
    if env.get("MODEL_VERSION"):
        return env["MODEL_VERSION"]
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{env.get('GIT_SHA') or _git_sha(root)}"


def _step(name: str, action: Callable[[], T]) -> T:
    log.info("step %s: start", name)
    try:
        result = action()
    except Exception as exc:  # noqa: BLE001 - re-raised with the step name
        raise StepError(name, exc) from exc
    log.info("step %s: done", name)
    return result


def run(settings: Settings, env: Mapping[str, str] | None = None) -> TrainingResult:
    env = os.environ if env is None else env
    params = settings.params
    target = params.target.name

    schema = _step("load-schema", lambda: load_schema(settings.schema_path))
    raw = _step("load-data", lambda: load_raw(settings.data_path))
    report = _step("validate", lambda: validate(raw, schema))
    log.info("%s", report.summary())
    dataset = _step("features", lambda: build_dataset(raw, schema, params.target))
    train, test = _step("split", lambda: split_dataset(dataset, target, params.split))
    tracking = resolve_target(env, settings.output_dir)
    experiment_id = _step(
        "tracking-preflight",
        lambda: connect(tracking, settings.tracking.experiment_name, settings.output_dir),
    )
    model = _step("fit", lambda: fit_model(train, target, params.model, params.split.random_state))
    metrics = _step("evaluate", lambda: evaluate(model, test, target))
    version = model_version(settings.root, env)

    def _write() -> None:
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, settings.model_path)
        write_metrics(metrics, settings.metrics_path)
        settings.version_path.write_text(version + "\n", encoding="utf-8")

    _step("write-outputs", _write)
    log.info("model %s written to %s: %s", version, settings.output_dir, metrics)

    lineage = {
        "target_threshold": params.target.threshold,
        "test_size": params.split.test_size,
        "random_state": params.split.random_state,
        "alpha": params.model.alpha,
        "l1_ratio": params.model.l1_ratio,
        "C": 1.0 / params.model.alpha,
        "max_iter": params.model.max_iter,
        "data_sha256": file_sha256(settings.data_path),
        "n_train": len(train),
        "n_test": len(test),
        "git_sha": env.get("GIT_SHA") or _git_sha(settings.root),
        "image_tag": env.get("IMAGE_TAG") or "local",
        "model_version_label": version,
    }
    record = _step(
        "track-and-register",
        lambda: log_training_run(
            target=tracking,
            experiment_id=experiment_id,
            model_name=settings.tracking.model_name,
            run_name=version,
            model=model,
            params=lineage,
            metrics=metrics,
            confusion=confusion(model, test, target),
            schema_path=settings.schema_path,
            train=train,
            test=test,
        ),
    )
    run_path = _step("write-run-record", lambda: record.write(settings.output_dir))
    return TrainingResult(
        model_path=settings.model_path,
        metrics_path=settings.metrics_path,
        version_path=settings.version_path,
        model_version=version,
        metrics=metrics,
        run=record,
        run_path=run_path,
    )


def main(env: Mapping[str, str] | None = None) -> int:
    configure_logging()
    try:
        run(load_settings(env=env), env=env)
    except StepError as exc:
        log.error("training failed at %s", exc, exc_info=exc.__cause__)
        return 1
    except Exception:  # noqa: BLE001 - configuration errors before any step
        log.exception("training failed before the pipeline started")
        return 1
    return 0

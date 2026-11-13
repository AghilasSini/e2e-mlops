"""Typed configuration for training and serving.

Settings come from three versioned YAML files (``config/config.yaml``, ``params.yaml``
and ``schema.yaml``) plus a few environment overrides for paths. Credentials are
never read from these files.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ENV_APP_ROOT = "APP_ROOT"
ENV_DATA_PATH = "DATA_PATH"
ENV_SCHEMA_PATH = "SCHEMA_PATH"
ENV_OUTPUT_DIR = "OUTPUT_DIR"
ENV_MODEL_URI = "MODEL_URI"


class ConfigError(ValueError):
    """Raised when a configuration file is missing or malformed."""


@dataclass(frozen=True)
class TargetParams:
    source_column: str
    name: str
    threshold: int


@dataclass(frozen=True)
class SplitParams:
    test_size: float
    random_state: int


@dataclass(frozen=True)
class ModelParams:
    alpha: float
    l1_ratio: float
    max_iter: int


@dataclass(frozen=True)
class PromotionParams:
    min_roc_auc: float
    min_f1: float
    min_improvement: float


@dataclass(frozen=True)
class Params:
    target: TargetParams
    split: SplitParams
    model: ModelParams
    promotion: PromotionParams


@dataclass(frozen=True)
class TrackingSettings:
    experiment_name: str
    model_name: str
    champion_alias: str = "champion"
    previous_alias: str = "previous-champion"


@dataclass(frozen=True)
class Settings:
    root: Path
    data_path: Path
    schema_path: Path
    output_dir: Path
    model_file: str
    metrics_file: str
    version_file: str
    model_uri: str
    params: Params
    tracking: TrackingSettings

    @property
    def model_path(self) -> Path:
        return self.output_dir / self.model_file

    @property
    def metrics_path(self) -> Path:
        return self.output_dir / self.metrics_file

    @property
    def version_path(self) -> Path:
        return self.output_dir / self.version_file


def read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML mapping, with a clear error when the file is absent or not a mapping."""
    if not path.is_file():
        raise ConfigError(f"configuration file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        content = yaml.safe_load(fh)
    if not isinstance(content, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    return content


def _section(content: Mapping[str, Any], key: str, source: Path) -> Mapping[str, Any]:
    value = content.get(key)
    if not isinstance(value, Mapping):
        raise ConfigError(f"{source}: missing section '{key}'")
    return value


def _resolve(root: Path, value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def load_params(path: Path) -> Params:
    raw = read_yaml(path)
    try:
        target = _section(raw, "target", path)
        split = _section(raw, "split", path)
        model = _section(raw, "model", path)
        promotion = _section(raw, "promotion", path)
        return Params(
            target=TargetParams(
                source_column=str(target["source_column"]),
                name=str(target["name"]),
                threshold=int(target["threshold"]),
            ),
            split=SplitParams(
                test_size=float(split["test_size"]),
                random_state=int(split["random_state"]),
            ),
            model=ModelParams(
                alpha=float(model["alpha"]),
                l1_ratio=float(model["l1_ratio"]),
                max_iter=int(model.get("max_iter", 5000)),
            ),
            promotion=PromotionParams(
                min_roc_auc=float(promotion["min_roc_auc"]),
                min_f1=float(promotion["min_f1"]),
                min_improvement=float(promotion["min_improvement"]),
            ),
        )
    except KeyError as exc:
        raise ConfigError(f"{path}: missing key {exc}") from exc


def load_settings(
    root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """Load settings from ``<root>/config/config.yaml`` and ``<root>/params.yaml``.

    ``root`` defaults to ``$APP_ROOT`` or the current directory. ``DATA_PATH``,
    ``SCHEMA_PATH``, ``OUTPUT_DIR`` and ``MODEL_URI`` override the file values.
    """
    env = os.environ if env is None else env
    root = Path(root or env.get(ENV_APP_ROOT) or Path.cwd()).resolve()

    config_path = root / "config" / "config.yaml"
    raw = read_yaml(config_path)
    data = _section(raw, "data", config_path)
    output = _section(raw, "output", config_path)
    serving = raw.get("serving") or {}

    try:
        data_path = _resolve(root, env.get(ENV_DATA_PATH) or data["raw_path"])
        schema_path = _resolve(root, env.get(ENV_SCHEMA_PATH) or data["schema_path"])
        output_dir = _resolve(root, env.get(ENV_OUTPUT_DIR) or output["dir"])
        model_file = str(output["model_file"])
        metrics_file = str(output["metrics_file"])
        version_file = str(output["version_file"])
    except KeyError as exc:
        raise ConfigError(f"{config_path}: missing key {exc}") from exc

    tracking = _section(raw, "tracking", config_path)
    registry = _section(raw, "registry", config_path)
    try:
        tracking_settings = TrackingSettings(
            experiment_name=str(tracking["experiment_name"]),
            model_name=str(registry["model_name"]),
            champion_alias=str(registry.get("champion_alias", "champion")),
            previous_alias=str(registry.get("previous_alias", "previous-champion")),
        )
    except KeyError as exc:
        raise ConfigError(f"{config_path}: missing key {exc}") from exc

    model_uri = env.get(ENV_MODEL_URI) or str(serving.get("model_uri") or "")
    if not model_uri:
        model_uri = str(output_dir / model_file)

    return Settings(
        root=root,
        data_path=data_path,
        schema_path=schema_path,
        output_dir=output_dir,
        model_file=model_file,
        metrics_file=metrics_file,
        version_file=version_file,
        model_uri=model_uri,
        params=load_params(root / "params.yaml"),
        tracking=tracking_settings,
    )

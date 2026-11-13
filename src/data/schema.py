"""Declarative description of the raw dataset, loaded from ``schema.yaml``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.config import ConfigError, read_yaml

SUPPORTED_DTYPES = frozenset({"float64", "int64"})


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    dtype: str
    nullable: bool = False
    min: float | None = None
    max: float | None = None


@dataclass(frozen=True)
class DatasetSchema:
    columns: tuple[ColumnSpec, ...]
    target_column: str

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def feature_names(self) -> list[str]:
        """Raw names of the model inputs: every column except the target."""
        return [c.name for c in self.columns if c.name != self.target_column]


def _column(name: str, spec: Mapping[str, Any], source: Path) -> ColumnSpec:
    dtype = str(spec.get("dtype", ""))
    if dtype not in SUPPORTED_DTYPES:
        raise ConfigError(f"{source}: column '{name}' has unsupported dtype '{dtype}'")
    return ColumnSpec(
        name=name,
        dtype=dtype,
        nullable=bool(spec.get("nullable", False)),
        min=None if spec.get("min") is None else float(spec["min"]),
        max=None if spec.get("max") is None else float(spec["max"]),
    )


def load_schema(path: Path) -> DatasetSchema:
    raw = read_yaml(path)
    columns = raw.get("columns")
    if not isinstance(columns, Mapping) or not columns:
        raise ConfigError(f"{path}: 'columns' must be a non-empty mapping")
    specs = tuple(_column(str(name), spec or {}, path) for name, spec in columns.items())
    target = str(raw.get("target_column", ""))
    if target not in {c.name for c in specs}:
        raise ConfigError(f"{path}: target_column '{target}' is not a declared column")
    return DatasetSchema(columns=specs, target_column=target)

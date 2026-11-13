"""Schema validation of the raw dataset.

Every problem is collected into a report first, so one run tells the user everything
that is wrong. :func:`validate` then raises if the report holds any problem: an invalid
dataset never reaches the split or the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from pandas.api import types as ptypes

from data.schema import ColumnSpec, DatasetSchema


@dataclass(frozen=True)
class Issue:
    column: str
    problem: str

    def __str__(self) -> str:
        return f"{self.column}: {self.problem}"


@dataclass
class ValidationReport:
    columns: dict[str, list[str]] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.issues

    @property
    def invalid_columns(self) -> list[str]:
        return sorted({i.column for i in self.issues})

    def add(self, column: str, problem: str) -> None:
        self.issues.append(Issue(column, problem))
        self.columns.setdefault(column, []).append(problem)

    def summary(self) -> str:
        if self.valid:
            return f"schema valid ({len(self.columns)} columns)"
        return "schema invalid: " + "; ".join(str(i) for i in self.issues)


class SchemaValidationError(ValueError):
    """Raised when a dataset does not match its schema."""

    def __init__(self, report: ValidationReport) -> None:
        super().__init__(report.summary())
        self.report = report


def _dtype_ok(series: pd.Series, expected: str) -> bool:
    if expected == "int64":
        return ptypes.is_integer_dtype(series) and not ptypes.is_bool_dtype(series)
    # float64 columns also accept integer data (lossless widening).
    return ptypes.is_numeric_dtype(series) and not ptypes.is_bool_dtype(series)


def _check_column(series: pd.Series, spec: ColumnSpec, report: ValidationReport) -> None:
    report.columns.setdefault(spec.name, [])
    if not _dtype_ok(series, spec.dtype):
        report.add(spec.name, f"expected dtype {spec.dtype}, found {series.dtype}")
        return  # range checks are meaningless on the wrong type
    nulls = int(series.isna().sum())
    if nulls and not spec.nullable:
        report.add(spec.name, f"{nulls} null value(s)")
    if spec.min is not None and (series < spec.min).any():
        report.add(spec.name, f"value(s) below minimum {spec.min:g}")
    if spec.max is not None and (series > spec.max).any():
        report.add(spec.name, f"value(s) above maximum {spec.max:g}")


def check(df: pd.DataFrame, schema: DatasetSchema) -> ValidationReport:
    """Return a report of every mismatch between ``df`` and ``schema`` (never raises)."""
    report = ValidationReport()
    present = set(df.columns)
    for spec in schema.columns:
        if spec.name not in present:
            report.add(spec.name, "missing column")
        else:
            _check_column(df[spec.name], spec, report)
    declared = set(schema.column_names)
    for name in df.columns:
        if name not in declared:
            report.add(str(name), "unexpected column")
    return report


def validate(df: pd.DataFrame, schema: DatasetSchema) -> ValidationReport:
    """Like :func:`check`, but raise :class:`SchemaValidationError` on any problem."""
    report = check(df, schema)
    if not report.valid:
        raise SchemaValidationError(report)
    return report


def load_raw(path: Path) -> pd.DataFrame:
    """Read the raw CSV. Columns keep the dtypes pandas infers, so validation can judge them."""
    if not path.is_file():
        raise FileNotFoundError(f"dataset not found: {path}")
    return pd.read_csv(path)

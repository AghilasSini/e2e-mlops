from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from common.config import ConfigError
from data.schema import load_schema
from data.validation import SchemaValidationError, check, load_raw, validate


@pytest.fixture(scope="module")
def schema(repo_root: Path):
    return load_schema(repo_root / "schema.yaml")


@pytest.fixture()
def seed(repo_root: Path) -> pd.DataFrame:
    return load_raw(repo_root / "data" / "raw" / "winequality-red.csv")


# Scenario: Dataset matches schema
def test_seed_dataset_is_valid(seed, schema) -> None:
    report = validate(seed, schema)
    assert report.valid
    assert set(report.columns) == set(schema.column_names)
    assert all(problems == [] for problems in report.columns.values())


# Scenario: Missing column
def test_missing_column_names_it(seed, schema) -> None:
    with pytest.raises(SchemaValidationError) as exc:
        validate(seed.drop(columns=["chlorides"]), schema)
    assert exc.value.report.invalid_columns == ["chlorides"]
    assert "chlorides: missing column" in str(exc.value)


# Regression for audit PIPE-01: the old validator let the *last* column decide the
# result, so a wrong middle column (schema "clorides" vs CSV "chlorides") still passed.
def test_middle_column_mismatch_fails_even_if_last_column_is_fine(seed, schema) -> None:
    renamed = seed.rename(columns={"chlorides": "clorides"})
    assert renamed.columns[-1] == schema.column_names[-1]  # last column still correct
    report = check(renamed, schema)
    assert not report.valid
    assert report.invalid_columns == ["chlorides", "clorides"]


# Scenario: Unexpected column or dtype
def test_extra_column_is_reported(seed, schema) -> None:
    with pytest.raises(SchemaValidationError) as exc:
        validate(seed.assign(color="red"), schema)
    assert exc.value.report.invalid_columns == ["color"]


def test_non_numeric_values_are_a_dtype_error(seed, schema) -> None:
    bad = seed.astype({"alcohol": object})
    bad.loc[0, "alcohol"] = "abc"
    report = check(bad, schema)
    assert report.columns["alcohol"] == ["expected dtype float64, found object"]


def test_each_offending_column_is_named(seed, schema) -> None:
    bad = seed.drop(columns=["density"]).assign(extra=1)
    bad["pH"] = bad["pH"].astype(str)
    report = check(bad, schema)
    assert report.invalid_columns == ["density", "extra", "pH"]


def test_float_target_is_a_dtype_error(seed, schema) -> None:
    report = check(seed.astype({"quality": "float64"}), schema)
    assert report.invalid_columns == ["quality"]


def test_integer_data_is_accepted_for_float_columns(seed, schema) -> None:
    report = check(
        seed.assign(**{"free sulfur dioxide": seed["free sulfur dioxide"].round().astype(int)}),
        schema,
    )
    assert report.valid


def test_nulls_are_rejected(seed, schema) -> None:
    bad = seed.copy()
    bad.loc[[3, 7], "sulphates"] = np.nan
    report = check(bad, schema)
    assert report.columns["sulphates"] == ["2 null value(s)"]


def test_range_limits_are_enforced(seed, schema) -> None:
    bad = seed.copy()
    bad.loc[0, "citric acid"] = -0.1
    bad.loc[1, "quality"] = 11
    report = check(bad, schema)
    assert report.columns["citric acid"] == ["value(s) below minimum 0"]
    assert report.columns["quality"] == ["value(s) above maximum 10"]


def test_summary_text(seed, schema) -> None:
    assert check(seed, schema).summary() == "schema valid (12 columns)"


def test_load_raw_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_raw(tmp_path / "nope.csv")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("columns: {}\ntarget_column: q\n", "non-empty"),
        ("columns: {a: {dtype: str}}\ntarget_column: a\n", "unsupported dtype"),
        ("columns: {a: {dtype: int64}}\ntarget_column: b\n", "target_column"),
    ],
)
def test_malformed_schema_is_rejected(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "schema.yaml"
    path.write_text(content)
    with pytest.raises(ConfigError, match=message):
        load_schema(path)


def test_feature_names_exclude_target(schema) -> None:
    assert "quality" not in schema.feature_names
    assert len(schema.feature_names) == 11

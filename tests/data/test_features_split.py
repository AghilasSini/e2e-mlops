from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from common.config import SplitParams, TargetParams, load_settings
from data.features import (
    FEATURE_COLUMNS,
    build_dataset,
    build_features,
    build_target,
    snake_case,
)
from data.schema import load_schema
from data.split import split_dataset
from data.validation import load_raw

TARGET = TargetParams(source_column="quality", name="is_good", threshold=6)


@pytest.fixture(scope="module")
def schema(repo_root: Path):
    return load_schema(repo_root / "schema.yaml")


@pytest.fixture(scope="module")
def dataset(repo_root: Path, schema) -> pd.DataFrame:
    raw = load_raw(repo_root / "data" / "raw" / "winequality-red.csv")
    return build_dataset(raw, schema, TARGET)


# Scenario: Threshold boundary
def test_threshold_boundary() -> None:
    labels = build_target(pd.DataFrame({"quality": [5, 6, 7]}), TARGET)
    assert labels.tolist() == [0, 1, 1]
    assert labels.name == "is_good"


def test_threshold_is_configurable() -> None:
    strict = TargetParams(source_column="quality", name="is_good", threshold=7)
    assert build_target(pd.DataFrame({"quality": [6, 7]}), strict).tolist() == [0, 1]


def test_raw_quality_is_not_a_feature(dataset: pd.DataFrame) -> None:
    assert "quality" not in dataset.columns
    assert list(dataset.columns) == [*FEATURE_COLUMNS, "is_good"]


def test_feature_names_match_schema(schema) -> None:
    assert tuple(snake_case(n) for n in schema.feature_names) == FEATURE_COLUMNS


def test_features_are_float(dataset: pd.DataFrame) -> None:
    assert all(dataset[c].dtype == "float64" for c in FEATURE_COLUMNS)


def test_schema_without_a_model_feature_is_rejected(repo_root: Path, tmp_path: Path) -> None:
    text = (repo_root / "schema.yaml").read_text().replace("  alcohol:", "  ethanol:")
    (tmp_path / "schema.yaml").write_text(text)
    raw = load_raw(repo_root / "data" / "raw" / "winequality-red.csv").rename(
        columns={"alcohol": "ethanol"}
    )
    with pytest.raises(ValueError, match="alcohol"):
        build_features(raw, load_schema(tmp_path / "schema.yaml"))


# Scenario: Identical reruns
def test_split_is_byte_identical_across_runs(dataset: pd.DataFrame) -> None:
    params = SplitParams(test_size=0.2, random_state=42)
    first = split_dataset(dataset, "is_good", params)
    second = split_dataset(dataset.copy(), "is_good", params)
    for a, b in zip(first, second):
        assert a.to_csv(index=False).encode() == b.to_csv(index=False).encode()


def test_different_seed_changes_split(dataset: pd.DataFrame) -> None:
    a, _ = split_dataset(dataset, "is_good", SplitParams(0.2, 42))
    b, _ = split_dataset(dataset, "is_good", SplitParams(0.2, 7))
    assert not a.equals(b)


def test_split_sizes(dataset: pd.DataFrame) -> None:
    train, test = split_dataset(dataset, "is_good", SplitParams(0.2, 42))
    assert len(train) + len(test) == len(dataset) == 1599
    assert len(test) == 320


# Scenario: Class balance preserved
@pytest.mark.parametrize("seed", [0, 1, 42, 123])
def test_class_ratio_preserved(dataset: pd.DataFrame, seed: int) -> None:
    train, test = split_dataset(dataset, "is_good", SplitParams(0.2, seed))
    overall = dataset["is_good"].mean()
    assert abs(train["is_good"].mean() - overall) <= 0.02
    assert abs(test["is_good"].mean() - overall) <= 0.02


def test_settings_feed_the_split(repo_root: Path, dataset: pd.DataFrame) -> None:
    settings = load_settings(root=repo_root, env={})
    train, test = split_dataset(dataset, settings.params.target.name, settings.params.split)
    assert len(test) == round(len(dataset) * settings.params.split.test_size)

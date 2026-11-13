from __future__ import annotations

from pathlib import Path

import pytest

from serving.model import ModelStore, ModelUnavailableError, Prediction


def test_missing_model_is_not_ready(tmp_path: Path) -> None:
    store = ModelStore(str(tmp_path / "absent.joblib"))
    assert store.load() is False
    assert not store.ready
    with pytest.raises(ModelUnavailableError):
        store.predict([[0.0] * 11])


def test_loads_model_and_version(trained_model_dir: Path) -> None:
    store = ModelStore(f"file://{trained_model_dir / 'model.joblib'}")
    assert store.load()
    assert store.ready
    assert store.version == "test-model-1"
    [result] = store.predict([[7.4, 0.7, 0.0, 1.9, 0.076, 11.0, 34.0, 0.9978, 3.51, 0.56, 9.4]])
    assert result.prediction in (0, 1)
    assert 0.0 <= result.probability <= 1.0


def test_version_falls_back_to_file_name(trained_model_dir: Path, tmp_path: Path) -> None:
    lone = tmp_path / "lone.joblib"
    lone.write_bytes((trained_model_dir / "model.joblib").read_bytes())
    store = ModelStore(str(lone))
    store.load()
    assert store.version == "lone.joblib"


def test_unsupported_scheme(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="scheme"):
        ModelStore("s3://bucket/model.joblib").load()


@pytest.mark.parametrize(("value", "label"), [(1, "good"), (0, "not_good")])
def test_prediction_label(value: int, label: str) -> None:
    assert Prediction(prediction=value, probability=0.5).label == label


def test_ensure_loaded_retries_after_interval(trained_model_dir: Path, tmp_path: Path) -> None:
    target = tmp_path / "model.joblib"
    store = ModelStore(str(target))
    assert store.ensure_loaded(min_interval=0) is False  # nothing there yet
    target.write_bytes((trained_model_dir / "model.joblib").read_bytes())
    assert store.ensure_loaded(min_interval=3600) is False  # too soon to retry
    assert store.ensure_loaded(min_interval=0) is True  # retry picks it up
    assert store.ensure_loaded(min_interval=0) is True
    assert store.load_count == 1  # never reloaded once loaded


def test_ensure_loaded_survives_load_errors(tmp_path: Path) -> None:
    broken = tmp_path / "model.joblib"
    broken.write_bytes(b"not a model")
    store = ModelStore(str(broken))
    assert store.ensure_loaded(min_interval=0) is False
    assert not store.ready

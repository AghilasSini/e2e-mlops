from __future__ import annotations

import pytest
from pydantic import ValidationError

from data.features import FEATURE_COLUMNS
from serving.schemas import BatchRequest, PredictResponse, WineFeatures

VALID = {
    "fixed_acidity": 7.4,
    "volatile_acidity": 0.7,
    "citric_acid": 0,
    "residual_sugar": 1.9,
    "chlorides": 0.076,
    "free_sulfur_dioxide": 11,
    "total_sulfur_dioxide": 34,
    "density": 0.9978,
    "ph": 3.51,
    "sulphates": 0.56,
    "alcohol": 9.4,
}


def _error_fields(exc: ValidationError) -> dict[str, str]:
    return {str(e["loc"][-1]): e["type"] for e in exc.errors()}


def test_valid_input_keeps_feature_order() -> None:
    features = WineFeatures.model_validate(VALID)
    assert features.as_row() == [float(VALID[name]) for name in FEATURE_COLUMNS]


def test_fields_match_model_features() -> None:
    assert tuple(WineFeatures.model_fields) == FEATURE_COLUMNS


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        WineFeatures.model_validate({**VALID, "quality": 5})
    assert _error_fields(exc.value) == {"quality": "extra_forbidden"}


def test_non_numeric_value_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        WineFeatures.model_validate({**VALID, "alcohol": "abc"})
    assert list(_error_fields(exc.value)) == ["alcohol"]


@pytest.mark.parametrize("value", ["9.4", True, float("nan"), float("inf"), None])
def test_only_finite_json_numbers_are_accepted(value) -> None:
    with pytest.raises(ValidationError):
        WineFeatures.model_validate({**VALID, "alcohol": value})


def test_negative_value_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        WineFeatures.model_validate({**VALID, "sulphates": -0.1})
    assert _error_fields(exc.value) == {"sulphates": "greater_than_equal"}


def test_missing_fields_are_each_reported() -> None:
    with pytest.raises(ValidationError) as exc:
        WineFeatures.model_validate({"alcohol": "abc"})
    fields = _error_fields(exc.value)
    assert fields.pop("alcohol") != "missing"
    assert set(fields) == set(FEATURE_COLUMNS) - {"alcohol"}
    assert set(fields.values()) == {"missing"}


def test_batch_requires_at_least_one_instance() -> None:
    with pytest.raises(ValidationError):
        BatchRequest.model_validate({"instances": []})
    assert len(BatchRequest.model_validate({"instances": [VALID, VALID]}).instances) == 2


def test_response_bounds() -> None:
    with pytest.raises(ValidationError):
        PredictResponse(prediction=1, label="good", probability=1.5, model_version="v")

"""Request and response contract of the inference API.

Feature names are the snake_case names of :data:`data.features.FEATURE_COLUMNS`.
JSON numbers only: strings, booleans, NaN/inf, negative values and unknown fields
are rejected with HTTP 422 before the model is called.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from data.features import FEATURE_COLUMNS

NonNegative = Annotated[float, Field(ge=0, strict=True, allow_inf_nan=False)]
#: Most rows one request may carry (JSON API batch and OIP v2 infer).
MAX_BATCH_SIZE = 1000


class WineFeatures(BaseModel):
    """The 11 physicochemical measurements of one wine."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "fixed_acidity": 7.4,
                "volatile_acidity": 0.7,
                "citric_acid": 0.0,
                "residual_sugar": 1.9,
                "chlorides": 0.076,
                "free_sulfur_dioxide": 11.0,
                "total_sulfur_dioxide": 34.0,
                "density": 0.9978,
                "ph": 3.51,
                "sulphates": 0.56,
                "alcohol": 9.4,
            }
        },
    )

    fixed_acidity: NonNegative
    volatile_acidity: NonNegative
    citric_acid: NonNegative
    residual_sugar: NonNegative
    chlorides: NonNegative
    free_sulfur_dioxide: NonNegative
    total_sulfur_dioxide: NonNegative
    density: NonNegative
    ph: NonNegative
    sulphates: NonNegative
    alcohol: NonNegative

    def as_row(self) -> list[float]:
        return [float(getattr(self, name)) for name in FEATURE_COLUMNS]


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instances: list[WineFeatures] = Field(min_length=1, max_length=MAX_BATCH_SIZE)


class PredictResponse(BaseModel):
    prediction: Literal[0, 1]
    label: Literal["good", "not_good"]
    probability: float = Field(ge=0, le=1, description="Probability that the wine is good")
    model_version: str


class BatchResponse(BaseModel):
    predictions: list[PredictResponse]
    model_version: str


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    model_version: str | None = None

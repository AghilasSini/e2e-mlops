"""Model feature names, importable without pandas (used by the feature logger)."""

from __future__ import annotations

#: The 11 physicochemical features, snake_case, in model order.
FEATURE_COLUMNS: tuple[str, ...] = (
    "fixed_acidity",
    "volatile_acidity",
    "citric_acid",
    "residual_sugar",
    "chlorides",
    "free_sulfur_dioxide",
    "total_sulfur_dioxide",
    "density",
    "ph",
    "sulphates",
    "alcohol",
)

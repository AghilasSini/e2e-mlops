"""Reproducible, stratified train/test split."""

from __future__ import annotations

import pandas as pd
from sklearn.model_selection import train_test_split

from common.config import SplitParams


def split_dataset(
    dataset: pd.DataFrame, target_name: str, params: SplitParams
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split on a fixed seed, stratified on the target, so reruns are identical."""
    train, test = train_test_split(
        dataset,
        test_size=params.test_size,
        random_state=params.random_state,
        stratify=dataset[target_name],
        shuffle=True,
    )
    return train.reset_index(drop=True), test.reset_index(drop=True)

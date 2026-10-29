"""Scikit-learn model: feature preprocessing plus a gradient-boosted regressor."""

import logging
from typing import Any

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

logger = logging.getLogger(__name__)


def build_pipeline(X: pd.DataFrame, params: dict[str, Any]) -> Pipeline:
    """Build an unfitted preprocessing + regression pipeline for the columns of ``X``.

    Preprocessing lives inside the pipeline so it is fitted on the training split
    only and persisted with the model; inference reuses it as-is instead of
    refitting a scaler on the batch it is scoring.

    Args:
        X: Training features; their dtypes decide which columns are scaled
            (numeric) and which are one-hot encoded (bool/category/object).
        params: Keyword arguments for ``HistGradientBoostingRegressor``.

    Returns:
        An unfitted ``Pipeline`` with ``preprocess`` and ``regressor`` steps.
    """
    continuous_cols = X.select_dtypes(include='number').columns.tolist()
    categorical_cols = X.select_dtypes(include=['bool', 'category', 'object']).columns.tolist()

    # Columns of any other dtype (e.g. datetimes) are dropped by the transformer
    ignored = sorted(set(X.columns) - set(continuous_cols) - set(categorical_cols))
    if ignored:
        logger.warning(f'Columns ignored by the model (unsupported dtype): {ignored}')

    preprocessor = ColumnTransformer(
        [
            ('num', StandardScaler(), continuous_cols),
            # Dense output: HistGradientBoostingRegressor does not accept sparse input
            ('cat', OneHotEncoder(handle_unknown='ignore', sparse_output=False), categorical_cols),
        ]
    )
    return Pipeline([('preprocess', preprocessor), ('regressor', HistGradientBoostingRegressor(**params))])

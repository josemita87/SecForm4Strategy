"""Scikit-learn model: feature preprocessing plus a gradient-boosted regressor."""

import logging
from typing import TYPE_CHECKING

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

if TYPE_CHECKING:
    from typing import Any

    import pandas as pd

logger = logging.getLogger(__name__)


def build_pipeline(X: pd.DataFrame, params: dict[str, Any]) -> Pipeline:
    """Build an unfitted preprocessing + regression pipeline over the columns of ``X``, by position.

    Preprocessing lives inside the pipeline so it is fitted on the training split
    only and persisted with the model. Columns are addressed by position, so the
    fitted pipeline scores plain arrays whose columns follow ``X``'s order, with no
    DataFrame needed at inference.

    Args:
        X: Training features; their dtypes decide which columns are scaled
            (numeric) and which are one-hot encoded (bool/category/object).
        params: Keyword arguments for ``HistGradientBoostingRegressor``.

    Returns:
        An unfitted ``Pipeline`` with ``preprocess`` and ``regressor`` steps.
    """
    position = {col: i for i, col in enumerate(X.columns)}
    continuous_cols = [position[col] for col in X.select_dtypes(include='number').columns]
    categorical_cols = [position[col] for col in X.select_dtypes(include=['bool', 'category', 'object']).columns]

    # Columns of any other dtype (e.g. datetimes) are dropped by the transformer
    ignored = sorted(set(X.columns) - set(X.columns[continuous_cols + categorical_cols]))
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

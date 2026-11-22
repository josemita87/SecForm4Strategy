"""Scikit-learn model: feature preprocessing plus a gradient-boosted classifier of short winners."""

import logging
from typing import TYPE_CHECKING, Any

from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import numpy as np
    import numpy.typing as npt
    import pandas as pd

logger = logging.getLogger(__name__)


def build_pipeline(X: pd.DataFrame, params: dict[str, Any]) -> Pipeline:
    """Build an unfitted preprocessing + classification pipeline over the columns of ``X``, by position.

    Preprocessing lives inside the pipeline so it is fitted on the training split
    only and persisted with the model. Columns are addressed by position, so the
    fitted pipeline scores plain arrays whose columns follow ``X``'s order, with no
    DataFrame needed at inference. With ``early_stopping``, each fit holds out a random
    ``validation_fraction`` of its rows (drawn with ``random_state``) to decide when to stop.

    Args:
        X: Training features; their dtypes decide which columns are scaled
            (numeric) and which are one-hot encoded (bool/category/object).
        params: Keyword arguments for ``HistGradientBoostingClassifier``.

    Returns:
        An unfitted ``Pipeline`` with ``preprocess`` and ``classifier`` steps.
    """
    position = {col: i for i, col in enumerate(X.columns)}
    continuous_cols = [position[col] for col in X.select_dtypes(include='number').columns]
    categorical_cols = [position[col] for col in X.select_dtypes(include=['bool', 'category', 'object']).columns]

    # Columns of any other dtype (e.g. datetimes) are dropped by the transformer
    if ignored := sorted(set(X.columns) - set(X.columns[continuous_cols + categorical_cols])):
        logger.warning(f'Columns ignored by the model (unsupported dtype): {ignored}')

    preprocessor = ColumnTransformer(
        [
            ('num', StandardScaler(), continuous_cols),
            # Dense output: HistGradientBoostingClassifier does not accept sparse input
            ('cat', OneHotEncoder(handle_unknown='ignore', sparse_output=False), categorical_cols),
        ]
    )
    return Pipeline([('preprocess', preprocessor), ('classifier', HistGradientBoostingClassifier(**params))])


def fold_probabilities(
    pipeline: Pipeline,
    params: Mapping[str, object],
    X: npt.NDArray[Any],
    y: npt.NDArray[np.bool_],
    folds: Sequence[tuple[npt.NDArray[np.intp], npt.NDArray[np.intp]]],
) -> tuple[npt.NDArray[np.float64], ...]:
    """Refit the pipeline with ``params`` on each fold's training rows and predict its validation rows.

    With a fixed ``random_state`` each refit equals the grid search's fit of the same parameters on that fold.

    Args:
        pipeline: The unfitted pipeline the grid search started from.
        params: Pipeline parameters to set, such as ``GridSearchCV.best_params_``.
        X: Development features.
        y: Development labels, True for a short winner.
        folds: (train positions, validation positions) pairs.

    Returns:
        Each fold's predicted probability of a short winner per validation row, in fold order.
    """
    return tuple(
        clone(pipeline).set_params(**params).fit(X[train], y[train]).predict_proba(X[val])[:, 1] for train, val in folds
    )

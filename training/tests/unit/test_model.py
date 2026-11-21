"""The pipeline scales numeric columns, one-hot encodes categorical ones and fits with grid parameters.

Under test:
    training/src/model.py
"""

import numpy as np
import pandas as pd
from model import build_pipeline

ROWS = 200
RNG = np.random.default_rng(0)
X = pd.DataFrame(
    {
        'size': RNG.normal(size=ROWS),
        'is_officer': RNG.integers(0, 2, ROWS).astype(bool),
        'role': RNG.choice(['ceo', 'cfo', 'director'], ROWS),
    }
)
Y = X['size'] * 0.01 + RNG.normal(scale=0.001, size=ROWS)
PARAMS = {
    'max_iter': 50,
    'early_stopping': True,
    'validation_fraction': 0.1,
    'loss': 'absolute_error',
    'learning_rate': 0.1,
    'max_leaf_nodes': 15,
    'min_samples_leaf': 20,
    'l2_regularization': 1.0,
    'random_state': 0,
}


class TestBuildPipeline:
    """The fitted pipeline scores plain arrays in the training column order."""

    def test_the_regressor_takes_the_given_parameters(self) -> None:
        regressor = build_pipeline(X, PARAMS).named_steps['regressor']
        assert {name: regressor.get_params()[name] for name in PARAMS} == PARAMS

    def test_it_fits_and_predicts_arrays(self) -> None:
        pipeline = build_pipeline(X, PARAMS).fit(X.to_numpy(), Y.to_numpy())
        assert pipeline.predict(X.to_numpy()).shape == (ROWS,)

    def test_the_same_seed_gives_the_same_predictions(self) -> None:
        first, second = (build_pipeline(X, PARAMS).fit(X.to_numpy(), Y.to_numpy()) for _ in range(2))
        np.testing.assert_array_equal(first.predict(X.to_numpy()), second.predict(X.to_numpy()))

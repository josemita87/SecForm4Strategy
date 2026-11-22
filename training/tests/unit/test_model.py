"""The pipeline scales numeric columns, one-hot encodes categorical ones and classifies with grid parameters.

Under test:
    training/src/model.py: build_pipeline, fold_probabilities
"""

import numpy as np
import pandas as pd
from model import build_pipeline, fold_probabilities

ROWS = 200
RNG = np.random.default_rng(0)
X = pd.DataFrame(
    {
        'size': RNG.normal(size=ROWS),
        'is_officer': RNG.integers(0, 2, ROWS).astype(bool),
        'role': RNG.choice(['ceo', 'cfo', 'director'], ROWS),
    }
)
Y = (X['size'] + RNG.normal(scale=0.5, size=ROWS)).to_numpy() > 0.5
PARAMS = {
    'max_iter': 50,
    'early_stopping': True,
    'validation_fraction': 0.1,
    'class_weight': 'balanced',
    'learning_rate': 0.1,
    'max_leaf_nodes': 15,
    'min_samples_leaf': 20,
    'l2_regularization': 1.0,
    'random_state': 0,
}


class TestBuildPipeline:
    """The fitted pipeline scores plain arrays in the training column order."""

    def test_the_classifier_takes_the_given_parameters(self) -> None:
        classifier = build_pipeline(X, PARAMS).named_steps['classifier']
        assert {name: classifier.get_params()[name] for name in PARAMS} == PARAMS

    def test_it_fits_and_predicts_probabilities_of_arrays(self) -> None:
        proba = build_pipeline(X, PARAMS).fit(X.to_numpy(), Y).predict_proba(X.to_numpy())
        assert proba.shape == (ROWS, 2)
        np.testing.assert_allclose(proba.sum(axis=1), 1.0)

    def test_the_same_seed_gives_the_same_probabilities(self) -> None:
        first, second = (build_pipeline(X, PARAMS).fit(X.to_numpy(), Y) for _ in range(2))
        np.testing.assert_array_equal(first.predict_proba(X.to_numpy()), second.predict_proba(X.to_numpy()))


class TestFoldProbabilities:
    """Each fold is refitted on its training rows and predicts its validation rows."""

    FOLDS = ((np.arange(0, 100), np.arange(100, 150)), (np.arange(0, 150), np.arange(150, 200)))

    def test_one_probability_per_validation_row(self) -> None:
        proba = fold_probabilities(build_pipeline(X, PARAMS), {}, X.to_numpy(), Y, self.FOLDS)
        assert [len(fold) for fold in proba] == [50, 50]
        assert all(((fold >= 0) & (fold <= 1)).all() for fold in proba)

    def test_it_matches_a_fit_on_the_folds_training_rows(self) -> None:
        train, val = self.FOLDS[0]
        params = {'classifier__learning_rate': 0.03}
        expected = build_pipeline(X, {**PARAMS, 'learning_rate': 0.03}).fit(X.to_numpy()[train], Y[train])
        np.testing.assert_array_equal(
            fold_probabilities(build_pipeline(X, PARAMS), params, X.to_numpy(), Y, self.FOLDS)[0],
            expected.predict_proba(X.to_numpy()[val])[:, 1],
        )

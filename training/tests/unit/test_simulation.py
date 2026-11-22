"""The model and its baselines short the rows predicted below the traded threshold.

Under test:
    training/src/simulation.py
"""

import pandas as pd
from secform4strategy_clients.records import Scores
from simulation import Shorts, evaluate, threshold_sweep

Y_TRUE = pd.Series([-0.10, 0.04, -0.02, 0.01])
PRED = pd.Series([-0.08, -0.03, -0.01, 0.02])


class TestEvaluate:
    """Each predictor is scored on error and on the rows it predicts below the threshold."""

    def test_only_rows_below_the_threshold_are_shorted(self) -> None:
        scores = evaluate(Y_TRUE, {'model': PRED}, -0.05)['model']
        assert scores.n_shorts == 1
        assert scores.short_return == 0.10

    def test_a_negative_prediction_above_the_threshold_is_not_shorted(self) -> None:
        assert evaluate(Y_TRUE, {'model': PRED}, -0.02)['model'].n_shorts == 2

    def test_no_shorts_capture_nothing(self) -> None:
        scores = evaluate(Y_TRUE, {'zero': pd.Series(0.0, index=Y_TRUE.index)}, -0.05)['zero']
        assert scores == Scores(mae=float(Y_TRUE.abs().mean()), short_return=0.0, n_shorts=0)

    def test_every_predictor_is_scored(self) -> None:
        assert evaluate(Y_TRUE, {'model': PRED, 'zero': PRED * 0}, 0.0).keys() == {'model', 'zero'}


class TestThresholdSweep:
    """Lower thresholds take fewer shorts."""

    def test_each_threshold_reports_its_shorts(self) -> None:
        assert threshold_sweep(Y_TRUE, PRED, (0.0, -0.05, -0.10)) == {
            0.0: Shorts(short_return=float(-Y_TRUE[[0, 1, 2]].mean()), n_shorts=3),
            -0.05: Shorts(short_return=0.10, n_shorts=1),
            -0.10: Shorts(short_return=0.0, n_shorts=0),
        }

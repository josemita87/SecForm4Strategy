"""Rows at or above the probability cutoff are shorted; the cutoff is chosen on the folds by median return.

Under test:
    training/src/simulation.py
"""

import numpy as np
import pandas as pd
import pytest
from secform4strategy_clients.records import CutoffShorts
from simulation import choose_cutoff, cutoff_sweep, evaluate, fold_cutoffs, ticker_history, trimmed_mean

RETURNS = pd.Series([-0.10, 0.04, -0.06, 0.01])
PROBA = pd.Series([0.9, 0.6, 0.4, 0.1])


class TestEvaluate:
    """Each predictor is scored on its ranking and on the rows at or above the cutoff."""

    def test_rows_at_or_above_the_cutoff_are_shorted(self) -> None:
        scores = evaluate(RETURNS, -0.05, {'model': PROBA}, 0.6)['model']
        assert (scores.n_shorts, scores.short_return) == (2, pytest.approx(0.03))
        assert (scores.median_return, scores.precision) == (pytest.approx(0.03), 0.5)

    def test_a_perfect_ranking_scores_one(self) -> None:
        scores = evaluate(RETURNS, -0.05, {'model': PROBA.where(RETURNS < -0.05, 0.0)}, 0.5)['model']
        assert (scores.average_precision, scores.roc_auc) == (1.0, 1.0)

    def test_a_constant_scores_the_base_rate_and_never_shorts_above_it(self) -> None:
        scores = evaluate(RETURNS, -0.05, {'base_rate': pd.Series(0.3, index=RETURNS.index)}, 0.5)['base_rate']
        assert (scores.average_precision, scores.roc_auc) == (0.5, 0.5)
        assert (scores.n_shorts, scores.short_return, scores.median_return, scores.precision) == (0, 0.0, None, None)

    def test_one_class_leaves_the_ranking_scores_undefined(self) -> None:
        scores = evaluate(RETURNS, -0.50, {'model': PROBA}, 0.5)['model']
        assert (scores.average_precision, scores.roc_auc) == (None, None)

    def test_every_predictor_is_scored(self) -> None:
        assert evaluate(RETURNS, -0.05, {'model': PROBA, 'base_rate': PROBA * 0}, 0.5).keys() == {'model', 'base_rate'}


class TestTrimmedMean:
    """The trimmed mean drops the same share from each end."""

    def test_an_outlier_is_cut(self) -> None:
        assert trimmed_mean(np.array([*[0.01] * 18, -2.0, 240.0]), 0.05) == pytest.approx(0.01)

    def test_too_few_values_cut_nothing(self) -> None:
        assert trimmed_mean(np.array([1.0, 2.0, 6.0]), 0.05) == 3.0


class TestCutoffSweep:
    """Higher cutoffs take fewer shorts."""

    def test_each_cutoff_reports_its_shorts(self) -> None:
        assert cutoff_sweep(RETURNS.to_numpy(), PROBA.to_numpy(), (0.4, 0.9, 0.95)) == (
            CutoffShorts(0.4, 3, pytest.approx(0.04), pytest.approx(0.06)),
            CutoffShorts(0.9, 1, pytest.approx(0.10), pytest.approx(0.10)),
            CutoffShorts(0.95, 0, None, None),
        )


class TestChooseCutoff:
    """The qualifying cutoff with the best median captured return over the folds wins."""

    #: Two folds; the rows above 0.7 fall hardest, but only one per fold
    FOLD_RETURNS = (np.array([-0.20, -0.08, -0.06, 0.05]), np.array([-0.30, -0.07, 0.02, 0.04]))
    FOLD_PROBA = (np.array([0.8, 0.6, 0.5, 0.2]), np.array([0.9, 0.6, 0.5, 0.1]))

    def test_the_best_median_wins(self) -> None:
        candidates = fold_cutoffs(self.FOLD_RETURNS, self.FOLD_PROBA, (0.5, 0.6, 0.7), 1)
        assert [c.median_return for c in candidates] == pytest.approx([0.075, 0.14, 0.25])
        assert choose_cutoff(candidates) == 0.7

    def test_a_fold_with_too_few_shorts_disqualifies_a_cutoff(self) -> None:
        candidates = fold_cutoffs(self.FOLD_RETURNS, self.FOLD_PROBA, (0.5, 0.6, 0.7), 2)
        assert (candidates[2].fewest_shorts, candidates[2].median_return) == (1, None)
        assert choose_cutoff(candidates) == 0.6

    def test_no_qualifying_cutoff_gives_none(self) -> None:
        assert choose_cutoff(fold_cutoffs(self.FOLD_RETURNS, self.FOLD_PROBA, (0.7, 0.95), 2)) is None

    def test_a_tie_goes_to_the_lowest_cutoff(self) -> None:
        returns, proba = (np.array([-0.1, -0.1]),), (np.array([0.9, 0.9]),)
        assert choose_cutoff(fold_cutoffs(returns, proba, (0.5, 0.8), 1)) == 0.5


class TestTickerHistory:
    """A row sees the share of short winners among its ticker's labels known strictly before its acceptance."""

    LABELS = pd.DataFrame(
        {
            'ticker': ['AAA', 'AAA', 'AAA', 'BBB'],
            'accepted_at': pd.to_datetime(['2024-01-01', '2024-01-02', '2024-03-01', '2024-01-01'], utc=True),
            'label_known_at': pd.to_datetime(['2024-02-01', '2024-02-02', '2024-04-01', '2024-02-01'], utc=True),
            'pct_change': [-0.10, 0.02, -0.20, -0.30],
        }
    )

    def test_only_labels_known_before_acceptance_count(self) -> None:
        rows = pd.DataFrame(
            {
                'ticker': ['AAA', 'AAA', 'AAA', 'CCC'],
                'accepted_at': pd.to_datetime(
                    ['2024-02-01 00:00', '2024-02-01 12:00', '2024-05-01 00:00', '2024-05-01 00:00'], utc=True
                ),
            },
            index=[10, 11, 12, 13],
        )
        shares = ticker_history(self.LABELS, rows, 'pct_change', -0.05)
        assert shares.index.tolist() == [10, 11, 12, 13]
        assert shares.tolist()[1:3] == [1.0, pytest.approx(2 / 3)]
        assert shares.iloc[[0, 3]].isna().all()

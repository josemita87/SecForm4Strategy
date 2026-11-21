"""The holdout and walk-forward folds respect time order, and no training label is known after its evaluation starts.

Under test:
    training/src/validation.py
"""

from itertools import pairwise

import numpy as np
import pandas as pd
import pytest
from constants import Col
from validation import describe_folds, holdout_split, walk_forward_folds

DAY0 = pd.Timestamp('2024-01-01', tz='UTC')


def rows(accepted_days: list[int], lag_days: int | list[int] = 1) -> pd.DataFrame:
    """Rows accepted on the given days after DAY0, each label known ``lag_days`` later."""
    accepted = DAY0 + pd.to_timedelta(accepted_days, unit='D')
    lags = pd.to_timedelta(lag_days if isinstance(lag_days, list) else [lag_days] * len(accepted_days), unit='D')
    return pd.DataFrame({Col.ACCEPTED_AT: accepted, Col.LABEL_KNOWN_AT: accepted + lags, 'row': range(len(accepted))})


def day(n: int) -> pd.Timestamp:
    return DAY0 + pd.Timedelta(days=n)


class TestHoldoutSplit:
    """The last rows by count are held out; development keeps only labels known before the holdout starts."""

    def test_boundary_is_the_acceptance_of_the_first_holdout_row(self) -> None:
        split = holdout_split(rows(list(range(10)), lag_days=0), 0.2)
        assert split.holdout_start == day(8)
        assert split.holdout['row'].tolist() == [8, 9]

    def test_rows_are_sorted_and_reindexed(self) -> None:
        split = holdout_split(rows([9, 3, 7, 1, 5, 0, 8, 2, 6, 4], lag_days=0), 0.3)
        assert split.development[Col.ACCEPTED_AT].is_monotonic_increasing
        assert split.holdout[Col.ACCEPTED_AT].is_monotonic_increasing
        assert split.development.index.equals(pd.RangeIndex(len(split.development)))
        assert split.holdout.index.equals(pd.RangeIndex(len(split.holdout)))

    def test_development_is_purged_against_the_holdout(self) -> None:
        split = holdout_split(rows(list(range(10)), lag_days=3), 0.2)
        assert (split.development[Col.LABEL_KNOWN_AT] < split.holdout_start).all()
        assert split.development['row'].tolist() == [0, 1, 2, 3, 4]

    def test_ties_at_the_boundary_all_go_to_the_holdout(self) -> None:
        split = holdout_split(rows([0, 1, 2, 3, 4, 5, 5, 5, 6, 7], lag_days=0), 0.3)
        assert split.holdout_start == day(5)
        assert split.holdout['row'].tolist() == [5, 6, 7, 8, 9]
        assert split.development['row'].tolist() == [0, 1, 2, 3, 4]

    @pytest.mark.parametrize('ratio', [0.0, 1.0, -0.1, 1.5])
    def test_ratio_outside_the_open_unit_interval_is_rejected(self, ratio: float) -> None:
        with pytest.raises(ValueError, match='holdout_ratio'):
            holdout_split(rows(list(range(10))), ratio)

    def test_empty_development_is_rejected(self) -> None:
        with pytest.raises(ValueError, match='0 development'):
            holdout_split(rows(list(range(10)), lag_days=100), 0.2)

    def test_no_rows_is_rejected(self) -> None:
        with pytest.raises(ValueError, match='No rows'):
            holdout_split(rows([]), 0.2)


class TestWalkForwardFolds:
    """Folds validate on contiguous later blocks and train only on labels known before each block starts."""

    def test_blocks_are_near_equal_and_in_time_order(self) -> None:
        folds = walk_forward_folds(rows(list(range(12)), lag_days=0), 3)
        assert [val.tolist() for _, val in folds] == [[3, 4, 5], [6, 7, 8], [9, 10, 11]]

    def test_validation_windows_are_contiguous_and_disjoint_up_to_the_end(self) -> None:
        development = rows(list(range(23)), lag_days=2)
        vals = np.concatenate([val for _, val in walk_forward_folds(development, 4)])
        assert vals.tolist() == list(range(vals[0], len(development)))

    def test_every_train_label_is_known_before_its_validation_starts(self) -> None:
        development = rows(list(range(20)), lag_days=[1, 5, 2, 9, 3] * 4)
        for train, val in walk_forward_folds(development, 3):
            start = development[Col.ACCEPTED_AT].iloc[val[0]]
            assert (development[Col.LABEL_KNOWN_AT].iloc[train] < start).all()

    def test_purging_drops_rows_whose_label_lands_in_the_window(self) -> None:
        folds = walk_forward_folds(rows(list(range(12)), lag_days=2), 3)
        assert [train.tolist() for train, _ in folds] == [[0], [0, 1, 2, 3], [0, 1, 2, 3, 4, 5, 6]]

    def test_train_sets_expand(self) -> None:
        folds = walk_forward_folds(rows(list(range(30)), lag_days=1), 4)
        trains = [set(train.tolist()) for train, _ in folds]
        assert all(earlier < later for earlier, later in pairwise(trains))

    def test_ties_never_straddle_a_fold_boundary(self) -> None:
        development = rows([0, 1, 2, 2, 2, 3, 4, 5, 5, 6, 7, 8], lag_days=0)
        for _, val in walk_forward_folds(development, 2):
            accepted = development[Col.ACCEPTED_AT]
            outside = accepted.drop(index=val)
            assert not outside.isin(accepted.iloc[val]).any()

    def test_positions_are_integer_arrays(self) -> None:
        train, val = walk_forward_folds(rows(list(range(6)), lag_days=0), 1)[0]
        assert train.dtype.kind == 'i'
        assert val.dtype.kind == 'i'

    def test_fewer_than_one_fold_is_rejected(self) -> None:
        with pytest.raises(ValueError, match='n_folds'):
            walk_forward_folds(rows(list(range(10))), 0)

    def test_a_fold_without_training_rows_names_the_fold(self) -> None:
        with pytest.raises(ValueError, match=r'Fold 1 .* 0 train'):
            walk_forward_folds(rows(list(range(6)), lag_days=10), 2)

    def test_a_fold_without_validation_rows_names_the_fold(self) -> None:
        with pytest.raises(ValueError, match=r'Fold 1 .* 0 validation'):
            walk_forward_folds(rows([0, 1, 1, 1, 1, 2], lag_days=0), 2)


def test_describe_folds_gives_one_line_per_fold() -> None:
    development = rows(list(range(12)), lag_days=0)
    assert describe_folds(development, walk_forward_folds(development, 2)) == [
        'fold 1: train 4 rows, validate 4 rows from 2024-01-05 to 2024-01-08',
        'fold 2: train 8 rows, validate 4 rows from 2024-01-09 to 2024-01-12',
    ]

"""Time-ordered validation: a purged holdout plus walk-forward folds over the rows before it.

Rows are ordered by acceptance. The last rows form a holdout, scored once. The rows before it are cut into
contiguous blocks; fold k validates on block k and trains on every earlier row (an expanding window). Purging
holds everywhere: a row trains only if its label was known before the period it is evaluated against starts,
so no training label overlaps the future it is scored on.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
from constants import Col

if TYPE_CHECKING:
    import numpy.typing as npt
    import pandas as pd


class Split(NamedTuple):
    """Development rows (purged against the holdout) and the holdout rows, both sorted by acceptance."""

    development: pd.DataFrame
    holdout: pd.DataFrame
    holdout_start: pd.Timestamp


def holdout_split(data: pd.DataFrame, holdout_ratio: float) -> Split:
    """Split off the last ``holdout_ratio`` of rows as the holdout and purge the development rows against it.

    The boundary is the acceptance time of the first holdout row, so rows sharing it all go to the holdout.

    Args:
        data: Rows with acceptance and label-known timestamps, in any order.
        holdout_ratio: Share of rows, by count, held out at the end.

    Returns:
        Development rows whose label was known before the holdout starts, the holdout rows, and the boundary;
        both frames sorted by acceptance with a fresh RangeIndex.

    Raises:
        ValueError: If the ratio is outside (0, 1), or the development or holdout rows end up empty.
    """
    if not 0 < holdout_ratio < 1:
        raise ValueError(f'holdout_ratio must be in (0, 1), got {holdout_ratio}')
    if data.empty:
        raise ValueError('No rows to split')
    data = data.sort_values(Col.ACCEPTED_AT, kind='stable')
    holdout_start = data[Col.ACCEPTED_AT].iloc[int(len(data) * (1 - holdout_ratio))]
    holdout = data[data[Col.ACCEPTED_AT] >= holdout_start].reset_index(drop=True)
    development = data[data[Col.LABEL_KNOWN_AT] < holdout_start].reset_index(drop=True)
    if development.empty or holdout.empty:
        raise ValueError(
            f'Holdout from {holdout_start} leaves {len(development)} development and {len(holdout)} holdout rows'
        )
    return Split(development, holdout, holdout_start)


def walk_forward_folds(
    development: pd.DataFrame, n_folds: int
) -> list[tuple[npt.NDArray[np.intp], npt.NDArray[np.intp]]]:
    """Expanding-window, purged walk-forward folds over the development rows, usable as GridSearchCV's ``cv``.

    The rows are cut into ``n_folds + 1`` blocks of near-equal count at acceptance timestamps, so rows sharing a
    timestamp never straddle a boundary. Fold k validates on block k and trains on the rows whose label was known
    before block k starts.

    Args:
        development: Rows sorted by acceptance with a RangeIndex, as ``holdout_split`` returns them.
        n_folds: Number of folds; the first block only ever trains.

    Returns:
        One (train positions, validation positions) pair per fold, in time order.

    Raises:
        ValueError: If ``n_folds`` is below 1, or any fold has no training or no validation rows.
    """
    if n_folds < 1:
        raise ValueError(f'n_folds must be at least 1, got {n_folds}')
    accepted, label_known = development[Col.ACCEPTED_AT], development[Col.LABEL_KNOWN_AT]
    starts = [accepted.iloc[len(development) * block // (n_folds + 1)] for block in range(1, n_folds + 1)]
    ends = [*accepted.searchsorted(starts[1:], side='left'), len(development)]
    folds = []
    for fold, (start, end) in enumerate(zip(starts, ends, strict=True), start=1):
        train = np.flatnonzero((label_known < start).to_numpy())
        val = np.arange(accepted.searchsorted(start, side='left'), end, dtype=np.intp)
        if not len(train) or not len(val):
            raise ValueError(
                f'Fold {fold} from {start} has {len(train)} train and {len(val)} validation rows; too little data'
            )
        folds.append((train, val))
    return folds


def describe_folds(
    development: pd.DataFrame, folds: Sequence[tuple[npt.NDArray[np.intp], npt.NDArray[np.intp]]]
) -> list[str]:
    """One log line per fold: its train and validation sizes and the validation date range.

    Args:
        development: The rows the folds index into.
        folds: (train positions, validation positions) pairs, as ``walk_forward_folds`` returns them.

    Returns:
        A line per fold, such as ``fold 1: train 120 rows, validate 40 rows from 2023-01-03 to 2023-06-30``.
    """
    accepted = development[Col.ACCEPTED_AT]
    return [
        f'fold {fold}: train {len(train)} rows, validate {len(val)} rows '
        f'from {accepted.iloc[val[0]].date()} to {accepted.iloc[val[-1]].date()}'
        for fold, (train, val) in enumerate(folds, start=1)
    ]

"""Live performance on the paper account: signals, shorts placed and filled, their P&L, hit rate and calibration.

Read-only maths over the live ``predictions`` records, the Alpaca orders and open positions, and the RT4 labels;
``measure.py`` reads them and prints the report. A row is a short winner when its RT4 return is below the label
return saved with the model that scored it, as in training.
"""

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import pandas as pd
from constants import Col

if TYPE_CHECKING:
    from secform4strategy_clients.records import BrokerOrder, BrokerPosition


class Trade(NamedTuple):
    """One short a live prediction placed, marked at its position's current price."""

    row_key: str
    symbol: str
    probability: float
    status: str
    qty: float | None
    fill_price: float | None
    mark: float | None
    #: ``(fill_price - mark) * qty``: a short gains when the price falls; None until filled and marked
    pnl: float | None
    #: ``pnl`` per dollar shorted
    pnl_pct: float | None


class Bucket(NamedTuple):
    """Labelled rows whose probability falls in ``[low, high)``, and how many were short winners."""

    low: float
    high: float
    rows: int
    mean_probability: float
    winner_share: float


class LivePerformance(NamedTuple):
    """The live path's results so far; a rate is None while no row it counts is labelled.

    ``unrecorded_orders`` counts the account's sell orders that no live prediction names, such as orders placed
    before the ``predictions`` feature group existed.
    """

    scored: int
    signals: int
    orders: int
    filled: int
    unrecorded_orders: int
    total_pnl: float
    trades: tuple[Trade, ...]
    labelled: int
    labelled_signals: int
    hit_rate: float | None
    base_rate: float | None
    calibration: tuple[Bucket, ...]


def latest_predictions(predictions: pd.DataFrame) -> pd.DataFrame:
    """Return each row key's latest prediction: a row rescored by a later run or a new model counts once.

    Args:
        predictions: Records of the ``predictions`` feature group.

    Returns:
        One record per row key.
    """
    return predictions.sort_values(Col.SCORED_AT).drop_duplicates(Col.ROW_KEY, keep='last').reset_index(drop=True)


def short_pnl(fill_price: float | None, mark: float | None, qty: float | None) -> tuple[float | None, float | None]:
    """Return a short's P&L and its P&L per dollar shorted, or None for both until it is filled and marked.

    Args:
        fill_price: The average fill price.
        mark: The current price.
        qty: The shares shorted.

    Returns:
        The P&L and the P&L per dollar shorted.
    """
    if fill_price is None or mark is None or qty is None:
        return None, None
    return (fill_price - mark) * qty, (fill_price - mark) / fill_price


def short_trades(
    latest: pd.DataFrame, orders: Iterable[BrokerOrder], positions: Iterable[BrokerPosition]
) -> tuple[Trade, ...]:
    """Return the sell orders whose client order id is a predicted row key, each marked at its symbol's price.

    Args:
        latest: One prediction per row key.
        orders: The account's orders.
        positions: The account's open positions; a symbol without one has no mark.

    Returns:
        One trade per order.
    """
    marks = {position.symbol: position.current_price for position in positions}
    probabilities = dict(zip(latest[Col.ROW_KEY], latest[Col.PROBABILITY], strict=True))
    return tuple(
        Trade(
            order.client_order_id,
            order.symbol,
            float(probabilities[order.client_order_id]),
            order.status,
            order.qty,
            order.filled_avg_price,
            mark := marks.get(order.symbol),
            *short_pnl(order.filled_avg_price, mark, order.qty),
        )
        for order in orders
        if order.side == 'sell' and order.client_order_id in probabilities
    )


def short_winners(
    latest: pd.DataFrame, labels: Mapping[str, float], label_returns: Mapping[str, float | None]
) -> pd.Series[bool]:
    """Return whether each prediction's row was a short winner, missing while it has no label.

    Args:
        latest: One prediction per row key.
        labels: The RT4 return per row key, for the rows labelled so far.
        label_returns: The label return per model version; None for a model saved without one.

    Returns:
        A nullable boolean per prediction, indexed like ``latest``.
    """
    returns = latest[Col.ROW_KEY].map(labels).astype(float)
    thresholds = latest[Col.MODEL_VERSION].map(label_returns).astype(float)
    return (returns < thresholds).astype('boolean').mask(returns.isna() | thresholds.isna())


def calibration(probabilities: pd.Series[float], winners: pd.Series[bool], buckets: int = 10) -> tuple[Bucket, ...]:
    """Return the share of short winners per probability bucket, over the labelled predictions.

    Args:
        probabilities: The predicted probability of a short winner.
        winners: Whether each was one, missing while unlabelled; indexed like ``probabilities``.
        buckets: How many equal-width buckets split ``[0, 1]``.

    Returns:
        The buckets holding at least one labelled prediction, lowest first.
    """
    known = winners.notna()
    labelled = pd.DataFrame({'p': probabilities[known], 'w': winners[known].astype(bool)})
    grouped = labelled.groupby(np.minimum((labelled['p'] * buckets).astype(int), buckets - 1))
    return tuple(
        Bucket(index / buckets, (index + 1) / buckets, int(rows), float(mean_p), float(share))
        for index, rows, mean_p, share in zip(
            grouped.size().index, grouped.size(), grouped['p'].mean(), grouped['w'].mean(), strict=True
        )
    )


def measure(
    predictions: pd.DataFrame,
    orders: Iterable[BrokerOrder],
    positions: Iterable[BrokerPosition],
    labels: Mapping[str, float],
    label_returns: Mapping[str, float | None],
) -> LivePerformance:
    """Return the live path's results from its predictions, the paper account and the labels known so far.

    Args:
        predictions: Live records of the ``predictions`` feature group.
        orders: The account's orders.
        positions: The account's open positions.
        labels: The RT4 return per row key, for the rows labelled so far.
        label_returns: The label return per model version.

    Returns:
        The results.
    """
    sells = tuple(order for order in orders if order.side == 'sell')
    latest = latest_predictions(predictions)
    trades = short_trades(latest, sells, positions)
    winners = short_winners(latest, labels, label_returns)
    signals = latest[Col.SHORTED].astype(bool)
    known = winners.notna()
    return LivePerformance(
        scored=len(latest),
        signals=int(signals.sum()),
        orders=len(trades),
        filled=sum(trade.fill_price is not None for trade in trades),
        unrecorded_orders=len(sells) - len(trades),
        total_pnl=sum(trade.pnl for trade in trades if trade.pnl is not None),
        trades=trades,
        labelled=int(known.sum()),
        labelled_signals=int((known & signals).sum()),
        hit_rate=float(winners[known & signals].astype(bool).mean()) if (known & signals).any() else None,
        base_rate=float(winners[known].astype(bool).mean()) if known.any() else None,
        calibration=calibration(latest[Col.PROBABILITY], winners),
    )

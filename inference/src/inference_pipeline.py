"""Inference pipeline: score the latest transactions and short the likely short winners."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

import pandas as pd
import sklearn
from constants import Col, OrderOutcome
from secform4strategy_clients.model_store import version_of
from secform4strategy_clients.ports import OrderType, PermanentError, RetryableError, Side, TimeInForce

if TYPE_CHECKING:
    from collections.abc import Sequence

    from secform4strategy_clients.ports import Broker, ModelStore
    from secform4strategy_clients.records import MarketSession
    from sklearn.pipeline import Pipeline

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OrderSpec:
    """How each short order is placed."""

    qty: float
    side: Side
    order_type: OrderType
    time_in_force: TimeInForce


class LoadedModel(NamedTuple):
    """The trained pipeline, its input feature order, the probability cutoff it shorts at, and its version."""

    pipeline: Pipeline
    features: tuple[str, ...]
    probability_cutoff: float
    version: str


def signal_cutoff(now: datetime, sessions: Sequence[MarketSession], max_age: timedelta) -> datetime:
    """Return the acceptance time a row must be later than to trade at ``now``.

    In a session a row may be ``max_age`` old. Outside one, and for ``max_age`` after the open, the age
    counts from the last close, so the filings disclosed since then trade at the open even when the
    morning queue runs late; no close among ``sessions`` counts from ``now``.

    Args:
        now: The current time, tz-aware.
        sessions: The market sessions around ``now``.
        max_age: The oldest a row may be when it trades.

    Returns:
        The cutoff, tz-aware.
    """
    if any(session.open + max_age <= now < session.close for session in sessions):
        return now - max_age
    return max((session.close for session in sessions if session.close <= now), default=now) - max_age


def score(model: Pipeline, features: Sequence[str], rows: pd.DataFrame) -> pd.DataFrame:
    """Return the distinct, identifiable rows with the model's probability of a short winner for each.

    Rows with missing features are scored, as training keeps them. Every missing feature reaches the pipeline as
    None, the value the feature store gave a missing category at training time; the scaler reads it as NaN.

    Args:
        model: The trained pipeline.
        features: Its input columns, in training order.
        rows: Disclosure rows from BIR4.

    Returns:
        The rows with a key, a ticker and an acceptance time, each with a ``probability`` column; empty when
        there are none.
    """
    if (scorable := rows.drop_duplicates().dropna(subset=[Col.KEY, Col.TICKER, Col.ACCEPTED_AT])).empty:
        return scorable.assign(**{Col.PROBABILITY: pd.Series(dtype=float)})
    X = scorable[list(features)].to_numpy(object)
    X[pd.isna(X)] = None
    return scorable.assign(**{Col.PROBABILITY: model.predict_proba(X)[:, 1]})


class InferencePipeline:
    """Score trades with the trained pipeline and place orders for the likely short winners.

    Attributes:
        broker: Where orders are placed.
        model_store: Where the trained pipeline is loaded from.
    """

    def __init__(self, broker: Broker, model_store: ModelStore):
        """Bind the pipeline to its ports.

        Args:
            broker: Where orders are placed.
            model_store: Where the trained pipeline is loaded from.
        """
        self.broker = broker
        self.model_store = model_store

    def load_model(self, cutoff_override: float | None = None) -> LoadedModel:
        """Load the trained pipeline, its feature order, the probability cutoff to short at, and its version.

        Args:
            cutoff_override: A cutoff that replaces the one saved with the model.

        Returns:
            The pipeline, its ordered feature names, the cutoff and the model version.

        Raises:
            RuntimeError: If the model was saved with a different scikit-learn version, or without a probability
                cutoff (a model trained before the classifier).
        """
        model, metadata = self.model_store.load()
        if metadata.sklearn_version != sklearn.__version__:
            raise RuntimeError(
                f'Model trained with scikit-learn {metadata.sklearn_version}, running {sklearn.__version__}'
            )
        if metadata.probability_cutoff is None:
            raise RuntimeError(
                f'Model trained at {metadata.trained_at} has no probability_cutoff: it predates the classifier'
            )
        cutoff = metadata.probability_cutoff if cutoff_override is None else cutoff_override
        logger.info(
            f'Loaded model trained at {metadata.trained_at} (git {metadata.git_sha}); '
            f'shorting at probability {cutoff:.2f} ({"override" if cutoff_override is not None else "model"})'
        )
        return LoadedModel(model, metadata.features, cutoff, version_of(metadata))

    def execute_trades(self, predictions_df: pd.DataFrame, cutoff: float, order: OrderSpec) -> dict[str, OrderOutcome]:
        """Place a short order for every row whose probability is at least ``cutoff``, once per row key.

        Each order's client order id is its row's key, so a rerun skips orders already placed.
        An order rejected for good is logged and dropped.

        Args:
            predictions_df: Scored rows with ``key``, ``ticker`` and ``probability``.
            cutoff: Probability at or above which a short is placed.
            order: How each order is placed.

        Returns:
            Each shorted row key's outcome; ``failed`` ones are left for a rerun.
        """
        return {
            key: self._place(ticker, key, order)
            for key, ticker in predictions_df.loc[
                predictions_df[Col.PROBABILITY] >= cutoff, [Col.KEY, Col.TICKER]
            ].itertuples(index=False)
        }

    def _place(self, ticker: str, key: str, order: OrderSpec) -> OrderOutcome:
        """Place one short under its row key and return what happened."""
        try:
            placed = self.broker.place_order(ticker, order.qty, order.side, order.order_type, order.time_in_force, key)
        except PermanentError as error:
            logger.error(f'Order {key} dropped: {error}')
            return OrderOutcome.REJECTED
        except RetryableError as error:
            logger.error(f'Order {key} left for a rerun: {error}')
            return OrderOutcome.FAILED
        return OrderOutcome.PLACED_BEFORE if placed is None else OrderOutcome.SUBMITTED

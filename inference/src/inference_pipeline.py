"""Inference pipeline: score the latest transactions and short the likely short winners."""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

import pandas as pd
import sklearn
from constants import Col
from secform4strategy_clients.ports import OrderType, PermanentError, RetryableError, Side, TimeInForce

if TYPE_CHECKING:
    from collections.abc import Sequence

    from secform4strategy_clients.ports import Broker, ModelStore
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
    """The trained pipeline, the feature order its input arrays follow, and the probability cutoff it shorts at."""

    pipeline: Pipeline
    features: tuple[str, ...]
    probability_cutoff: float


def score(model: Pipeline, features: Sequence[str], rows: pd.DataFrame) -> pd.DataFrame:
    """Return the complete, distinct rows with the model's probability of a short winner for each.

    The pipeline applies the preprocessing fitted at training time, on arrays in the trained feature order.

    Args:
        model: The trained pipeline.
        features: Its input columns, in training order.
        rows: Disclosure rows from BIR4.

    Returns:
        The rows with no missing value, each with a ``probability`` column; empty when no row is complete.
    """
    if (complete := rows.drop_duplicates().dropna()).empty:
        return complete.assign(**{Col.PROBABILITY: pd.Series(dtype=float)})
    return complete.assign(**{Col.PROBABILITY: model.predict_proba(complete[list(features)].to_numpy())[:, 1]})


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
        """Load the trained pipeline, its feature order, and the probability cutoff to short at.

        Args:
            cutoff_override: A cutoff that replaces the one saved with the model.

        Returns:
            The pipeline, its ordered feature names and the cutoff.

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
        return LoadedModel(model, metadata.features, cutoff)

    def execute_trades(self, predictions_df: pd.DataFrame, cutoff: float, order: OrderSpec) -> list[str]:
        """Place a short order for every row whose probability is at least ``cutoff``, once per row key.

        Each order's client order id is its row's key, so a rerun skips orders already placed.
        An order rejected for good is logged and dropped.

        Args:
            predictions_df: Scored rows with ``key``, ``ticker`` and ``probability``.
            cutoff: Probability at or above which a short is placed.
            order: How each order is placed.

        Returns:
            Keys of the orders that failed transiently and are left for a rerun.
        """
        retry_later = []
        for key, ticker in predictions_df.loc[
            predictions_df[Col.PROBABILITY] >= cutoff, [Col.KEY, Col.TICKER]
        ].itertuples(index=False):
            try:
                self.broker.place_order(ticker, order.qty, order.side, order.order_type, order.time_in_force, key)
            except PermanentError as error:
                logger.error(f'Order {key} dropped: {error}')
            except RetryableError as error:
                logger.error(f'Order {key} left for a rerun: {error}')
                retry_later.append(key)
        return retry_later

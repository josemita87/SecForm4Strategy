"""Inference pipeline: score the latest transactions and short the predicted losers."""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import sklearn
from constants import Col
from secform4strategy_clients.ports import OrderType, Side, TimeInForce

if TYPE_CHECKING:
    import pandas as pd
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


class InferencePipeline:
    """Score trades with the trained pipeline and place orders for the predicted losers.

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

    def load_model(self) -> tuple[Pipeline, tuple[str, ...]]:
        """Load the trained pipeline and the feature order its input arrays follow.

        Returns:
            The scikit-learn pipeline and its ordered feature names.

        Raises:
            RuntimeError: If the model was saved with a different scikit-learn version.
        """
        model, metadata = self.model_store.load()
        if metadata.sklearn_version != sklearn.__version__:
            raise RuntimeError(
                f'Model trained with scikit-learn {metadata.sklearn_version}, running {sklearn.__version__}'
            )
        logger.info(f'Loaded model trained at {metadata.trained_at} (git {metadata.git_sha})')
        return model, metadata.features

    def execute_trades(self, predictions_df: pd.DataFrame, threshold: float, order: OrderSpec) -> None:
        """Place a short order for every prediction below ``threshold``.

        Args:
            predictions_df: DataFrame of predictions with ticker symbols.
            threshold: Prediction value below which a short is placed.
            order: How each order is placed.
        """
        for ticker in predictions_df.loc[predictions_df[Col.PREDICTIONS] < threshold, Col.TICKER]:
            self.broker.place_order(ticker, order.qty, order.side, order.order_type, order.time_in_force)

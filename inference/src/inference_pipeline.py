"""Inference pipeline: score the latest transactions and short the predicted losers."""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import sklearn
from constants import FeatureGroup

if TYPE_CHECKING:
    import pandas as pd
    from secform4strategy_clients.ports import Broker, FeatureStore, ModelStore
    from sklearn.pipeline import Pipeline

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OrderSpec:
    """How each short order is placed."""

    qty: str
    side: str
    order_type: str
    time_in_force: str


class InferencePipeline:
    """Score trades with the trained pipeline and place orders for the predicted losers.

    Attributes:
        feature_store: Source of the inference transactions (BIR4).
        broker: Where orders are placed.
        model_store: Where the trained pipeline is loaded from.
    """

    def __init__(self, feature_store: FeatureStore, broker: Broker, model_store: ModelStore):
        """Bind the pipeline to its ports.

        Args:
            feature_store: Source of the inference transactions (BIR4).
            broker: Where orders are placed.
            model_store: Where the trained pipeline is loaded from.
        """
        self.feature_store = feature_store
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
        if (trained_with := metadata.get('sklearn_version')) != sklearn.__version__:
            raise RuntimeError(f'Model trained with scikit-learn {trained_with}, running {sklearn.__version__}')
        logger.info(f'Loaded model trained at {metadata.get("trained_at")} (git {metadata.get("git_sha")})')
        return model, tuple(metadata['features'])

    def fetch_new_trades(self) -> pd.DataFrame:
        """Read the inference transactions (BIR4), dropping duplicate and NaN rows.

        Returns:
            The cleaned transactions.
        """
        return self.feature_store.read(FeatureGroup.BIR4).drop_duplicates().dropna()

    @staticmethod
    def generate_predictions(model: Pipeline, features: tuple[str, ...], data: pd.DataFrame) -> pd.DataFrame:
        """Score the data and attach the predictions as a ``predictions`` column.

        The pipeline applies the preprocessing fitted at training time, so the
        inference batch is never used to refit the scaler or encoder.

        Args:
            model: The fitted pipeline used to score the data.
            features: Feature order the pipeline's input arrays follow.
            data: Inference data containing at least the training features.

        Returns:
            A copy of ``data`` with a ``predictions`` column.
        """
        return data.assign(predictions=model.predict(data[list(features)].to_numpy()))

    def execute_trades(self, predictions_df: pd.DataFrame, threshold: float, order: OrderSpec) -> None:
        """Place a short order for every prediction below ``threshold``.

        Args:
            predictions_df: DataFrame of predictions with ticker symbols.
            threshold: Prediction value below which a short is placed.
            order: How each order is placed.
        """
        for ticker in predictions_df.loc[predictions_df['predictions'] < threshold, 'ticker']:
            if placed := self.broker.place_order(
                str(ticker).upper().strip(), order.qty, order.side, order.order_type, order.time_in_force
            ):
                logger.info(f'Order details: {placed}')

"""Lambda entry point for the live path: scores the tickers a new filing names and places the orders.

The live-score state machine invokes it with ``{"tickers": [...]}``, after target has built their
BIR4 rows. The image is the same as the batch task's; Lambda runs ``python -m awslambdaric
handler.handle`` from ``src/``. The model is loaded on every invocation, so a weekly retrain
takes effect at once.
"""

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pandas as pd
from config import S3Models, config
from constants import Col, FeatureGroup
from inference_pipeline import InferencePipeline, OrderSpec, score
from paths import SPEC_PATH
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.broker import AlpacaClient
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.ports import RetryableError
from secform4strategy_clients.s3_model_store import S3ModelStore

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)
# The Lambda runtime installs its own log handler, so the level is set here
logging.getLogger().setLevel(logging.INFO)

if not isinstance(config.model_store, S3Models):
    raise TypeError('The Lambda handler loads the model from S3: set MODEL_STORE__BACKEND=s3')
pipeline = InferencePipeline(
    broker=AlpacaClient(config.alpaca_api_key, config.alpaca_api_secret, config.alpaca_base_url),
    model_store=S3ModelStore(config.model_store.region, config.model_store.bucket, config.model_store.prefix),
)
store = IcebergFeatureStore(
    load_catalog('default'),
    config.feature_store_namespace,
    load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
)


def handle(event: Mapping[str, Any], _context: object) -> dict[str, object]:
    """Score the tickers' rows whose next print is still ahead, and short the predicted losers.

    Rows whose print has passed are left alone: their trade can no longer be placed at the price
    the row was built for. Orders carry their row key as client order id, so a retry never doubles one.

    Args:
        event: ``{"tickers": [...]}``.
        _context: The Lambda context, unused; Lambda passes it to every handler.

    Returns:
        The tickers, how many rows were scored, and the keys of the rows below the threshold.

    Raises:
        RetryableError: Orders failed transiently; the state machine retries, and placed orders are not repeated.
    """
    if not (tickers := list(event['tickers'])):
        logger.info('No tickers with rows to score')
        return {'tickers': tickers, 'scored': 0, 'shorts': []}
    rows = store.read(FeatureGroup.BIR4, where={Col.TICKER: tickers})
    upcoming = rows[pd.to_datetime(rows[Col.NEXT_PRINT], utc=True) > datetime.now(UTC)]
    if upcoming.empty:
        logger.info(f'No upcoming rows for {tickers}')
        return {'tickers': tickers, 'scored': 0, 'shorts': []}
    model, features = pipeline.load_model()
    predictions = score(model, features, upcoming)
    shorts = predictions.loc[predictions[Col.PREDICTIONS] < config.prediction_threshold, Col.KEY].tolist()
    logger.info(f'{len(predictions)} of {len(upcoming)} rows complete and scored for {tickers}; shorting {shorts}')
    if retry_later := pipeline.execute_trades(
        predictions,
        config.prediction_threshold,
        OrderSpec(config.qty, config.side, config.order_type, config.time_in_force),
    ):
        raise RetryableError(f'Orders left for a retry: {retry_later}')
    return {'tickers': tickers, 'scored': len(predictions), 'shorts': shorts}

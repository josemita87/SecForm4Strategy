"""Lambda entry point for the live path: scores the tickers a new filing names and places the orders.

The live-score state machine invokes it with ``{"tickers": [...]}``, after target has built their
BIR4 rows. The image is the same as the batch task's; Lambda runs ``python -m awslambdaric
handler.handle`` from ``src/``. The model is loaded on every invocation, so a weekly retrain
takes effect at once.
"""

import logging
from datetime import UTC, datetime, timedelta
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
    """Score the tickers' fresh rows, and short the predicted losers at once.

    A row is fresh when its latest filing was accepted within ``MAX_SIGNAL_AGE_MINUTES``: the event
    carries tickers, and their older rows must not trade late. Orders carry their row key as client
    order id, so a retry never doubles one.

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
    since = datetime.now(UTC) - timedelta(minutes=config.max_signal_age_minutes)
    fresh = rows[pd.to_datetime(rows[Col.ACCEPTED_AT], utc=True) > since]
    if fresh.empty:
        logger.info(f'No rows of {tickers} accepted since {since:%Y-%m-%d %H:%M} UTC')
        return {'tickers': tickers, 'scored': 0, 'shorts': []}
    model, features = pipeline.load_model()
    predictions = score(model, features, fresh)
    shorts = predictions.loc[predictions[Col.PREDICTIONS] < config.prediction_threshold, Col.KEY].tolist()
    logger.info(f'{len(predictions)} of {len(fresh)} rows complete and scored for {tickers}; shorting {shorts}')
    if retry_later := pipeline.execute_trades(
        predictions,
        config.prediction_threshold,
        OrderSpec(config.qty, config.side, config.order_type, config.time_in_force),
    ):
        raise RetryableError(f'Orders left for a retry: {retry_later}')
    return {'tickers': tickers, 'scored': len(predictions), 'shorts': shorts}

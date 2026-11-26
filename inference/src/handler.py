"""Lambda entry point for the live path: scores the tickers a new filing names and places the orders.

The live-score state machine invokes it with ``{"tickers": [...]}``, after target has built their
BIR4 rows. The image is the same as the batch task's; Lambda runs ``python -m awslambdaric
handler.handle`` from ``src/``. The model is loaded on every invocation, so a weekly retrain
takes effect at once. Every scored row is upserted into the ``predictions`` feature group after the orders.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

import pandas as pd
from config import S3Models, config
from constants import Col, FeatureGroup, OrderOutcome, Source
from inference_pipeline import InferencePipeline, OrderSpec, score, signal_cutoff
from paths import SPEC_PATH
from predictions import ScoringRun, prediction_records, write_predictions
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
broker = AlpacaClient(config.alpaca_api_key, config.alpaca_api_secret, config.alpaca_base_url)
pipeline = InferencePipeline(
    broker=broker,
    model_store=S3ModelStore(config.model_store.region, config.model_store.bucket, config.model_store.prefix),
)
store = IcebergFeatureStore(
    load_catalog('default'),
    config.feature_store_namespace,
    load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
)


class LambdaContext(Protocol):
    """The slice of the Lambda context the handler reads."""

    aws_request_id: str


def handle(event: Mapping[str, Any], context: LambdaContext) -> dict[str, object]:
    """Score the tickers' fresh rows, and short the likely short winners at once.

    A row is fresh when its latest filing was accepted within ``MAX_SIGNAL_AGE_MINUTES`` of now, or of
    the last close while the market is closed (Alpaca's calendar), so filings disclosed overnight trade
    at the open. The event carries tickers, and their older rows must not trade late. Orders carry their
    row key as client order id, so a retry never doubles one. The predictions are upserted after the orders,
    also when some orders are left for a retry, so the retry replaces their records.

    Args:
        event: ``{"tickers": [...]}``.
        context: The Lambda context; its request id is the run id of the predictions.

    Returns:
        The tickers, how many rows were scored, the keys of the rows at or above the probability cutoff, the
        cutoff (None when nothing was scored), and each scored row's key, ticker, acceptance time and probability
        of a short winner (the site's live feed reads them).

    Raises:
        RetryableError: Orders, the calendar or the predictions write failed transiently; the state machine
            retries, and placed orders are not repeated.
    """
    if not (tickers := list(event['tickers'])):
        logger.info('No tickers with rows to score')
        return {'tickers': tickers, 'scored': 0, 'shorts': [], 'probability_cutoff': None, 'predictions': []}
    rows = store.read(FeatureGroup.BIR4, where={Col.TICKER: tickers})
    now = datetime.now(UTC)
    # Ten days back reaches the last close across any weekend and holiday
    since = signal_cutoff(
        now,
        broker.sessions(now.date() - timedelta(days=10), now.date()),
        timedelta(minutes=config.max_signal_age_minutes),
    )
    fresh = rows[pd.to_datetime(rows[Col.ACCEPTED_AT], utc=True) > since]
    if fresh.empty:
        logger.info(f'No rows of {tickers} accepted since {since:%Y-%m-%d %H:%M %Z}')
        return {'tickers': tickers, 'scored': 0, 'shorts': [], 'probability_cutoff': None, 'predictions': []}
    loaded = pipeline.load_model(config.probability_cutoff)
    predictions = score(loaded.pipeline, loaded.features, fresh)
    shorts = predictions.loc[predictions[Col.PROBABILITY] >= loaded.probability_cutoff, Col.KEY].tolist()
    logger.info(f'{len(predictions)} of {len(fresh)} rows scored for {tickers}; shorting {shorts}')
    outcomes = pipeline.execute_trades(
        predictions,
        loaded.probability_cutoff,
        OrderSpec(config.qty, config.side, config.order_type, config.time_in_force),
    )
    write_predictions(
        store, prediction_records(predictions, loaded, outcomes, ScoringRun(Source.LIVE, now, context.aws_request_id))
    )
    if retry_later := [key for key, outcome in outcomes.items() if outcome == OrderOutcome.FAILED]:
        raise RetryableError(f'Orders left for a retry: {retry_later}')
    scored = predictions[[Col.KEY, Col.TICKER, Col.ACCEPTED_AT, Col.PROBABILITY]].assign(
        **{Col.ACCEPTED_AT: pd.to_datetime(predictions[Col.ACCEPTED_AT], utc=True).dt.strftime('%Y-%m-%dT%H:%M:%SZ')}
    )
    return {
        'tickers': tickers,
        'scored': len(predictions),
        'shorts': shorts,
        'probability_cutoff': loaded.probability_cutoff,
        'predictions': scored.to_dict('records'),
    }

"""Entry point: wires the ports from config and runs the inference pipeline end to end."""

import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

import pandas as pd
from config import LocalModels, S3Models, config
from constants import FeatureGroup, OrderOutcome, Source
from inference_pipeline import InferencePipeline, OrderSpec, score
from paths import SPEC_PATH
from predictions import ScoringRun, prediction_records, write_predictions
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.broker import AlpacaClient
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.model_store import LocalModelStore
from secform4strategy_clients.s3_model_store import S3ModelStore

if TYPE_CHECKING:
    from secform4strategy_clients.ports import ModelStore

# Configure logging (the file handler needs the log directory to exist)
config.log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(config.log_path, maxBytes=10 * 1024 * 1024, backupCount=5),
    ],
)

if __name__ == '__main__':
    model_store: ModelStore
    match config.model_store:
        case LocalModels() as local:
            model_store = LocalModelStore(local.path)
        case S3Models() as s3:
            model_store = S3ModelStore(s3.region, s3.bucket, s3.prefix)
    pipeline = InferencePipeline(
        broker=AlpacaClient(config.alpaca_api_key, config.alpaca_api_secret, config.alpaca_base_url),
        model_store=model_store,
    )
    loaded = pipeline.load_model(config.probability_cutoff)

    # The CSV source stands in for the feature store when it is disabled; predictions are then not recorded
    store = (
        IcebergFeatureStore(
            load_catalog('default'),
            config.feature_store_namespace,
            load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
        )
        if config.use_feature_store
        else None
    )
    predictions = score(
        loaded.pipeline,
        loaded.features,
        pd.read_csv(config.csv_path) if store is None else store.read(FeatureGroup.BIR4),
    )
    outcomes = pipeline.execute_trades(
        predictions,
        loaded.probability_cutoff,
        OrderSpec(config.qty, config.side, config.order_type, config.time_in_force),
    )
    if store is not None:
        write_predictions(
            store, prediction_records(predictions, loaded, outcomes, ScoringRun(Source.BATCH, datetime.now(UTC), None))
        )
    # A non-zero exit has the scheduler rerun inference; client order ids keep placed orders from repeating
    if retry_later := [key for key, outcome in outcomes.items() if outcome == OrderOutcome.FAILED]:
        sys.exit(f'Orders left for a rerun: {retry_later}')

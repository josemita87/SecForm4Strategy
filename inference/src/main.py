"""Entry point: wires the ports from config and runs the inference pipeline end to end."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

import pandas as pd
from config import LocalModels, S3Models, config
from constants import FeatureGroup
from inference_pipeline import InferencePipeline, OrderSpec, score
from paths import SPEC_PATH
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

    # The CSV source stands in for the feature store when it is disabled
    rows = (
        IcebergFeatureStore(
            load_catalog('default'),
            config.feature_store_namespace,
            load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
        ).read(FeatureGroup.BIR4)
        if config.use_feature_store
        else pd.read_csv(config.csv_path)
    )
    predictions = score(loaded.pipeline, loaded.features, rows)
    # A non-zero exit has the scheduler rerun inference; client order ids keep placed orders from repeating
    if retry_later := pipeline.execute_trades(
        predictions,
        loaded.probability_cutoff,
        OrderSpec(config.qty, config.side, config.order_type, config.time_in_force),
    ):
        sys.exit(f'Orders left for a rerun: {retry_later}')

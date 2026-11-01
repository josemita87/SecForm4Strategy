"""Entry point: wires the ports from config and runs the inference pipeline end to end."""

import logging
from logging.handlers import RotatingFileHandler

import pandas as pd
from config import config
from constants import Col, FeatureGroup
from inference_pipeline import InferencePipeline, OrderSpec
from paths import SPEC_PATH
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.broker import AlpacaClient
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.model_store import LocalModelStore

# Configure logging (the file handler needs the log directory to exist)
config.log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(config.log_path, maxBytes=10 * 1024 * 1024, backupCount=5),
    ],
)

if __name__ == '__main__':
    pipeline = InferencePipeline(
        broker=AlpacaClient(config.alpaca_api_key, config.alpaca_api_secret, config.alpaca_base_url),
        model_store=LocalModelStore(config.model_path),
    )
    model, features = pipeline.load_model()

    # The CSV source stands in for the feature store when it is disabled
    data = (
        (
            IcebergFeatureStore(
                load_catalog('default'),
                config.feature_store_namespace,
                load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
            ).read(FeatureGroup.BIR4)
            if config.use_feature_store
            else pd.read_csv(config.csv_path)
        )
        .drop_duplicates()
        .dropna()
    )

    # The pipeline applies the preprocessing fitted at training time, on arrays in the trained feature order
    predictions = data.assign(**{Col.PREDICTIONS: model.predict(data[list(features)].to_numpy())})
    pipeline.execute_trades(
        predictions,
        config.prediction_threshold,
        OrderSpec(config.qty, config.side, config.order_type, config.time_in_force),
    )

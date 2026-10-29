"""Entry point: wires the ports from config and runs the inference pipeline end to end."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pandas as pd
from config import config
from inference_pipeline import InferencePipeline, OrderSpec
from secform4strategy_clients.broker import AlpacaClient
from secform4strategy_clients.feature_store import HopsworksClient, load_feature_group_catalog
from secform4strategy_clients.model_store import LocalModelStore

# Configure logging (the file handler needs the log directory to exist)
Path(config.log_path).parent.mkdir(parents=True, exist_ok=True)
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
        feature_store=HopsworksClient(
            config.project_name,
            config.hopsworks_api_key,
            load_feature_group_catalog(Path(__file__).parent / 'feature_groups.yaml', config.feature_group_version),
        ),
        broker=AlpacaClient(config.alpaca_api_key, config.alpaca_api_secret, config.alpaca_base_url),
        model_store=LocalModelStore(config.model_path),
    )
    model, features = pipeline.load_model()

    # The CSV source stands in for the feature store when it is disabled
    data = pipeline.fetch_new_trades() if config.hopsworks_connect else pd.read_csv(config.csv_path)

    predictions = pipeline.generate_predictions(model, features, data.drop(columns=config.columns_to_drop))
    pipeline.execute_trades(
        predictions,
        config.prediction_threshold,
        OrderSpec(config.qty, config.side, config.order_type, config.time_in_force),
    )

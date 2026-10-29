"""Entry point: wires the ports from config and refreshes the prices (P) feature group."""

import logging
import time
from pathlib import Path

from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore, load_feature_group_catalog
from secform4strategy_clients.market_data import MarketDataClient

from src.config import config
from src.constants import FeatureGroup
from src.ingestion import PriceIngestor

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)


if __name__ == '__main__':
    # Prices follow the tickers of the active system's transactions; training keeps one direction only
    if config.system_inference:
        source, where = FeatureGroup.BI4, None
    elif config.system_training:
        source, where = FeatureGroup.BT4, {config.filter_key: config.acquired_disposed}
    else:
        raise RuntimeError('No run mode selected: set SYSTEM_INFERENCE or SYSTEM_TRAINING')

    time.sleep(config.delay)
    PriceIngestor(
        feature_store=IcebergFeatureStore(
            load_catalog('default'),
            config.feature_store_namespace,
            load_feature_group_catalog(Path(__file__).parent / 'feature_groups.yaml', config.feature_group_version),
        ),
        market_data=MarketDataClient(),
    ).process_tickers(source, where, config.buffer_size)

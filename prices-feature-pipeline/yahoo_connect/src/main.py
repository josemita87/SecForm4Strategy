"""Entry point: reads the tickers to price, refreshes their daily bars, and stores them in the prices (P) group."""

import logging
import time
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NoSuchTableError
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.market_data import MarketDataClient
from tqdm import tqdm

from src import prices
from src.config import config
from src.constants import Col, FeatureGroup
from src.paths import SPEC_PATH

if TYPE_CHECKING:
    from secform4strategy_clients.ports import FeatureStore

logging.basicConfig(
    level=logging.INFO,
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
    store: FeatureStore = IcebergFeatureStore(
        load_catalog('default'),
        config.feature_store_namespace,
        load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
    )
    tickers = np.unique(store.read(source, where=where)[Col.TICKER]).tolist()
    try:
        stored = store.read(FeatureGroup.PRICES, where={Col.TICKER: tickers})
    except NoSuchTableError:
        # The first run creates the price table, so every ticker fetches its full history
        stored = pd.DataFrame({Col.TICKER: [], Col.DATE: pd.to_datetime([]), Col.CLOSE: [], Col.CLOSE_AS_TRADED: []})

    batches = prices.new_prices(MarketDataClient(), tickers, prices.resume_points(stored), config.buffer_size)
    for batch in tqdm(batches, total=-(-len(tickers) // config.buffer_size), desc='Pricing tickers', unit='batch'):
        # One key column: pyiceberg's upsert crashes on composite keys
        store.push(
            FeatureGroup.PRICES,
            batch.assign(**{Col.KEY: batch[Col.TICKER].astype(str) + '@' + batch[Col.DATE].dt.strftime('%Y-%m-%d')}),
        )

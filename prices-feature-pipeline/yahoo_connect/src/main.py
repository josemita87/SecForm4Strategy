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
    from secform4strategy_clients.ports import FeatureStore, MarketData

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)


def refresh_prices(
    store: FeatureStore, market: MarketData, tickers: list[str], buffer_size: int, history_years: int
) -> None:
    """Fetch the tickers' bars after their last stored day and upsert them into the prices group.

    Args:
        store: The feature store holding the prices group.
        market: Where the bars come from.
        tickers: The tickers to price.
        buffer_size: Tickers fetched per batch.
        history_years: Years of bars a ticker without stored prices fetches.
    """
    try:
        stored = store.read(
            FeatureGroup.PRICES,
            where={Col.TICKER: tickers},
            columns=(Col.TICKER, Col.DATE, Col.CLOSE, Col.CLOSE_AS_TRADED),
        )
    except NoSuchTableError:
        # The first run creates the price table, so every ticker fetches from the history start
        stored = pd.DataFrame({Col.TICKER: [], Col.DATE: pd.to_datetime([]), Col.CLOSE: [], Col.CLOSE_AS_TRADED: []})

    batches = prices.new_prices(
        market, tickers, prices.resume_points(stored), buffer_size, prices.history_start(history_years)
    )
    for batch in tqdm(batches, total=-(-len(tickers) // buffer_size), desc='Pricing tickers', unit='batch'):
        # One key column: pyiceberg's upsert crashes on composite keys
        store.push(
            FeatureGroup.PRICES,
            batch.assign(**{Col.KEY: batch[Col.TICKER].astype(str) + '@' + batch[Col.DATE].dt.strftime('%Y-%m-%d')}),
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
    refresh_prices(
        store,
        MarketDataClient(),
        np.unique(store.read(source, where=where, columns=(Col.TICKER,))[Col.TICKER]).tolist(),
        config.buffer_size,
        config.price_history_years,
    )

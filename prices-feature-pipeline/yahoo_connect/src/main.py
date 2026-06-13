"""Entry point for fetching Yahoo Finance prices into the feature store."""

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
from inside_out_clients.feature_store import HopsworksClient, load_feature_group_catalog
from inside_out_clients.market_data import MarketDataClient
from tqdm import tqdm

from src.clean import reduce_mem_storage
from src.config import config
from src.constants import FeatureGroup

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)

# Catalog of feature groups this service touches, loaded from the YAML spec template.
FEATURE_GROUPS = load_feature_group_catalog(Path(__file__).parent / 'feature_groups.yaml', config.feature_group_version)


class YahooDataFetcher:
    """Fetch and process price data from Yahoo Finance.

    Attributes:
        feature_store: Connection to the feature store backend.
    """

    def __init__(self):
        """Initialize the fetcher with feature-store and market-data clients."""
        self.feature_store = HopsworksClient(config.project_name, config.hopsworks_api_key, FEATURE_GROUPS)
        self.market_data = MarketDataClient()

    def fetch_data_from_yahoo(self, prices: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
        """Fetches the latest data from Yahoo Finance for the given tickers.

        Args:
            prices (pd.DataFrame): Current prices data for the buffer of tickers.
            tickers (list[str]): List of tickers to fetch data for.

        Returns:
            pd.DataFrame: Updated prices data with the new data only.
        """
        all_prices = pd.DataFrame()

        for ticker in tickers:
            try:
                offset = prices.loc[prices['ticker'] == ticker]['date'].max() if not prices.empty else None
                start = pd.to_datetime(offset) + pd.Timedelta(days=1) if offset else None

                new_data = self.market_data.close_history(ticker, start=start)
                new_data['ticker'] = ticker
                new_data = reduce_mem_storage(new_data)

                all_prices = pd.concat([all_prices, new_data], axis=0) if not all_prices.empty else new_data

            except Exception as e:
                logger.error(f'Failed to fetch data for {ticker}: {e}')
                continue

        all_prices.sort_values(by=['ticker', 'date'], inplace=True)
        return reduce_mem_storage(all_prices)

    def process_tickers(self) -> None:
        """Process the tickers in batches and fetch data from Yahoo Finance."""
        # Route to the correct feature group
        if config.system_inference:
            data = self.feature_store.read(FeatureGroup.BI4, read_options={'use_hive': True})
        elif config.system_training:
            data = self.feature_store.read(
                FeatureGroup.BT4,
                where=lambda fg: fg.filter(fg[config.filter_key] == config.acquired_disposed),
                read_options={'use_hive': True},
            )

        # Get the unique tickers from the 'ticker' column
        tickers = np.unique(data['ticker'].tolist())
        logger.debug(len(tickers))

        # Process the tickers in batches
        for i in tqdm(range(0, len(tickers), config.buffer_size), desc='Processing tickers', unit='batch'):
            processing_tickers = tickers[i : i + config.buffer_size]
            try:
                current_data = self.feature_store.read(
                    FeatureGroup.PRICES,
                    where=lambda fg: fg.filter(fg['ticker'].isin(processing_tickers)),
                    read_options={'use_hive': True},
                )
            except Exception:
                current_data = pd.DataFrame()
            new_data = self.fetch_data_from_yahoo(current_data, processing_tickers)
            self.feature_store.push(FeatureGroup.PRICES, new_data)

        logger.debug('Finished processing all tickers')


if __name__ == '__main__':
    # Fetch the latest Yahoo Finance prices and update the prices (P) feature group.
    time.sleep(config.delay)
    fetcher = YahooDataFetcher()
    fetcher.process_tickers()

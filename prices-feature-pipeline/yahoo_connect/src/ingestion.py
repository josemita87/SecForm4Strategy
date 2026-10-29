"""Price-ingestion domain logic: refresh the prices (P) feature group.

Reads the tickers referenced by a transaction feature group, pulls their latest
close history through the market-data port, and upserts it into the price
feature group.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from tqdm import tqdm

from src.constants import FeatureGroup

if TYPE_CHECKING:
    from secform4strategy_clients.ports import FeatureStore, Filters, MarketData

logger = logging.getLogger(__name__)


class PriceIngestor:
    """Pull close-price history and upsert it into the price feature group.

    Attributes:
        feature_store: Where transactions are read and prices upserted.
        market_data: Source of close-price history.
    """

    def __init__(self, feature_store: FeatureStore, market_data: MarketData):
        """Bind the ingestor to its ports.

        Args:
            feature_store: Where transactions are read and prices upserted.
            market_data: Source of close-price history.
        """
        self.feature_store = feature_store
        self.market_data = market_data

    def fetch_new_prices(self, prices: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
        """Fetch close history for ``tickers``, resuming each from its latest stored date.

        Args:
            prices: Current prices data for the batch of tickers.
            tickers: Tickers to fetch data for.

        Returns:
            New close-price rows, tagged by ticker.
        """
        # A ticker with no valid latest date gets no start, so it fetches full history
        latest = pd.Series() if prices.empty else prices.groupby('ticker')['date'].max().dropna()
        starts = {str(ticker): pd.to_datetime(date) + pd.Timedelta(days=1) for ticker, date in latest.items()}
        return self.market_data.close_histories(tickers, starts=starts)

    def process_tickers(self, source: FeatureGroup, where: Filters | None, batch_size: int) -> None:
        """Refresh the price feature group for every ticker referenced by ``source``, in batches.

        Args:
            source: Transaction feature group whose tickers need prices.
            where: Filter applied when reading ``source``.
            batch_size: Tickers fetched and pushed per batch.
        """
        tickers = np.unique(self.feature_store.read(source, where=where)['ticker'].tolist())
        logger.debug(f'{len(tickers)} unique tickers')

        for i in tqdm(range(0, len(tickers), batch_size), desc='Processing tickers', unit='batch'):
            batch = tickers[i : i + batch_size].tolist()
            try:
                current = self.feature_store.read(FeatureGroup.PRICES, where={'ticker': batch})
            except Exception as e:
                # Recorded degradation: without stored prices every ticker fetches full history
                logger.warning(f'No stored prices for batch {i // batch_size} ({e}); fetching full history')
                current = pd.DataFrame()
            self.feature_store.push(FeatureGroup.PRICES, self.fetch_new_prices(current, batch))

        logger.debug('Finished processing all tickers')

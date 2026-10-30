"""Market-data client backed by yfinance.

Hides the yfinance download call and its column layout behind tidy daily bars. The
batch :meth:`MarketDataClient.price_histories` skips a symbol that fails to download
(delisted, renamed), the only way to keep a multi-symbol pull alive.
"""

import logging
from collections.abc import Iterable, Mapping
from datetime import date

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class MarketDataClient:
    """Look up market-data fields for a ticker via yfinance."""

    def daily_bars(self, symbol: str, start: date | None = None) -> pd.DataFrame:
        """Return the daily open and close history for ``symbol``.

        Hides the yfinance specifics (``download`` and its column layout) behind a
        tidy three-column frame.

        Args:
            symbol: Ticker symbol to download.
            start: Optional inclusive start date; when None, the full available
                history is returned (``period='max'``).

        Prices are split- and dividend-adjusted (``auto_adjust``).

        Returns:
            A DataFrame with ``date``, ``open`` and ``close`` columns.
        """
        # Without a start yfinance returns only its default one-month period, so ask for all of it
        window = {'start': start} if start is not None else {'period': 'max'}
        bars: pd.DataFrame = yf.download(symbol, **window, auto_adjust=True, multi_level_index=False)[
            ['Open', 'Close']
        ].reset_index()
        bars.columns = ['date', 'open', 'close']
        return bars

    def price_histories(self, symbols: Iterable[str], starts: Mapping[str, date] | None = None) -> pd.DataFrame:
        """Return concatenated daily open/close history for many symbols, tagged by ticker.

        Best-effort: a symbol whose download fails (delisted, renamed) is logged
        and skipped rather than aborting the whole batch. The result uses
        storage-friendly dtypes (``float32`` prices, categorical ticker), sorted by
        ticker then date.

        Args:
            symbols: Iterable of ticker symbols to download.
            starts: Optional mapping of symbol to inclusive start date; symbols
                absent from the mapping fetch their full available history.

        Returns:
            A DataFrame with ``date``, ``open``, ``close`` and ``ticker`` columns.
        """
        starts = starts or {}
        frames = []
        for symbol in symbols:
            try:
                frames.append(self.daily_bars(symbol, start=starts.get(symbol)).assign(ticker=symbol))
            except Exception as exc:
                logger.warning('Skipping %s: %s', symbol, exc)

        if not frames:
            logger.warning('No price history downloaded for any symbol')
            return pd.DataFrame(columns=['date', 'open', 'close', 'ticker'])

        return (
            pd.concat(frames, ignore_index=True)
            .astype({'open': 'float32', 'close': 'float32', 'ticker': 'category'})
            .assign(date=lambda df: pd.to_datetime(df['date']))
            .sort_values(['ticker', 'date'], ignore_index=True)
        )

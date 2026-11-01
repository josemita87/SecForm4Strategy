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
        """Return the daily history for ``symbol``: adjusted open and close, the close as traded, and corporate actions.

        ``open`` and ``close`` are adjusted for later splits and dividends (as yfinance's
        ``auto_adjust`` does), so returns between any two days are comparable.
        ``close_as_traded`` is the price on the day itself, in that day's shares: yfinance's
        split-adjusted ``Close`` times every split ratio after that day. ``split`` (ratio) and
        ``dividend`` (per share) are the day's corporate actions, 0 when none.

        Args:
            symbol: Ticker symbol to download.
            start: Optional inclusive start date; when None, the full available
                history is returned (``period='max'``).

        Returns:
            A DataFrame with ``date``, ``open``, ``close``, ``close_as_traded``, ``split`` and ``dividend`` columns.
        """
        # Without a start yfinance returns only its default one-month period, so ask for all of it
        window = {'start': start} if start else {'period': 'max'}
        bars: pd.DataFrame = yf.download(symbol, **window, auto_adjust=False, actions=True, multi_level_index=False)
        # A split's ratio sits on its first post-split day; each day is scaled by the splits strictly after it
        later_splits = bars['Stock Splits'].replace(0, 1)[::-1].cumprod()[::-1].shift(-1, fill_value=1)
        return pd.DataFrame(
            {
                'date': bars.index,
                'open': bars['Open'] * bars['Adj Close'] / bars['Close'],
                'close': bars['Adj Close'],
                'close_as_traded': bars['Close'] * later_splits,
                'split': bars['Stock Splits'],
                'dividend': bars['Dividends'],
            }
        ).reset_index(drop=True)

    def price_histories(self, symbols: Iterable[str], starts: Mapping[str, date] | None = None) -> pd.DataFrame:
        """Return concatenated daily history (``daily_bars``) for many symbols, tagged by ticker.

        Best-effort: a symbol whose download fails (delisted, renamed) is logged
        and skipped rather than aborting the whole batch. The result uses
        storage-friendly dtypes (``float32`` prices, categorical ticker), sorted by
        ticker then date.

        Args:
            symbols: Iterable of ticker symbols to download.
            starts: Optional mapping of symbol to inclusive start date; symbols
                absent from the mapping fetch their full available history.

        Returns:
            A DataFrame with ``date``, ``open``, ``close``, ``close_as_traded``, ``split``, ``dividend`` and
            ``ticker`` columns.
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
        # Typed even when empty, so callers can rely on the column dtypes
        return (
            (
                pd.concat(frames, ignore_index=True)
                if frames
                else pd.DataFrame(columns=['date', 'open', 'close', 'close_as_traded', 'split', 'dividend', 'ticker'])
            )
            .astype(
                {
                    'open': 'float32',
                    'close': 'float32',
                    'close_as_traded': 'float32',
                    'split': 'float32',
                    'dividend': 'float32',
                    'ticker': 'category',
                }
            )
            .assign(date=lambda df: pd.to_datetime(df['date']))
            .sort_values(['ticker', 'date'], ignore_index=True)
        )

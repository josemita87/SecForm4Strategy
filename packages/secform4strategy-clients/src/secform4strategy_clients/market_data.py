"""Market-data client backed by yfinance.

Encapsulates the yfinance-specific knowledge callers would otherwise reach into
directly: which ``Ticker.info`` keys hold a market cap or an exchange. Callers ask
for a domain value (``market_cap``/``exchange``) rather than driving the SDK. It
does not catch errors — how to handle a missing value is the caller's domain.

The one exception is the batch :meth:`MarketDataClient.close_histories`, where
skipping a symbol that fails to download (delisted, renamed) is itself a
market-data concern and the only sensible way to keep a multi-symbol pull alive.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    import pandas as pd

logger = logging.getLogger(__name__)


class MarketDataClient:
    """Look up market-data fields for a ticker via yfinance."""

    def __init__(self) -> None:
        """Bind the yfinance module."""
        # Lazy import so the SDK is only required when this client is built.
        import yfinance as yf

        self._yf = yf

    def market_cap(self, symbol: str) -> int | None:
        """Return the market capitalization for ``symbol`` (or None if absent)."""
        cap: int | None = self._yf.Ticker(symbol).info.get('marketCap')
        return cap

    def exchange(self, symbol: str) -> str | None:
        """Return the listing exchange for ``symbol`` (or None if absent)."""
        exchange: str | None = self._yf.Ticker(symbol).info.get('exchange')
        return exchange

    def close_history(self, symbol: str, start: object = None) -> pd.DataFrame:
        """Return the daily close-price history for ``symbol``.

        Hides the yfinance specifics (``download`` and the ``Close`` column)
        behind a tidy two-column frame.

        Args:
            symbol: Ticker symbol to download.
            start: Optional inclusive start date; when None, the full available
                history is returned.

        Returns:
            A DataFrame with ``date`` and ``close`` columns.
        """
        close: pd.DataFrame = self._yf.download(symbol, start=start)['Close'].reset_index()
        close.columns = ['date', 'close']
        return close

    def close_histories(self, symbols: Iterable[str], starts: Mapping[str, object] | None = None) -> pd.DataFrame:
        """Return concatenated daily close history for many symbols, tagged by ticker.

        Best-effort: a symbol whose download fails (delisted, renamed) is logged
        and skipped rather than aborting the whole batch. The result uses
        storage-friendly dtypes (``float32`` close, categorical ticker), sorted by
        ticker then date.

        Args:
            symbols: Iterable of ticker symbols to download.
            starts: Optional mapping of symbol to inclusive start date; symbols
                absent from the mapping fetch their full available history.

        Returns:
            A DataFrame with ``date``, ``close`` and ``ticker`` columns.
        """
        import pandas as pd

        starts = starts or {}
        frames = []
        for symbol in symbols:
            try:
                frames.append(self.close_history(symbol, start=starts.get(symbol)).assign(ticker=symbol))
            except Exception as exc:
                logger.warning('Skipping %s: %s', symbol, exc)

        if not frames:
            logger.warning('No close history downloaded for any symbol')
            return pd.DataFrame(columns=['date', 'close', 'ticker'])

        return (
            pd.concat(frames, ignore_index=True)
            .astype({'close': 'float32', 'ticker': 'category'})
            .assign(date=lambda df: pd.to_datetime(df['date']))
            .sort_values(['ticker', 'date'], ignore_index=True)
        )

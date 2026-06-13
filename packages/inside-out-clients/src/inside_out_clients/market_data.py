"""Market-data client backed by yfinance.

Encapsulates the yfinance-specific knowledge callers would otherwise reach into
directly: which ``Ticker.info`` keys hold a market cap or an exchange. Callers ask
for a domain value (``market_cap``/``exchange``) rather than driving the SDK. It
does not catch errors — how to handle a missing value is the caller's domain.
"""


class MarketDataClient:
    """Look up market-data fields for a ticker via yfinance."""

    def __init__(self) -> None:
        """Bind the yfinance module."""
        # Lazy import so the SDK is only required when this client is built.
        import yfinance as yf

        self._yf = yf

    def market_cap(self, symbol: str):
        """Return the market capitalization for ``symbol`` (or None if absent)."""
        return self._yf.Ticker(symbol).info.get('marketCap')

    def exchange(self, symbol: str):
        """Return the listing exchange for ``symbol`` (or None if absent)."""
        return self._yf.Ticker(symbol).info.get('exchange')

    def close_history(self, symbol: str, start=None):
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
        raw = self._yf.download(symbol, start=start) if start is not None else self._yf.download(symbol)
        close = raw['Close'].reset_index()
        close.columns = ['date', 'close']
        return close

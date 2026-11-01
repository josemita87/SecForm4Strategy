"""Fake of the ``MarketData`` port (needs the ``market-data`` extra for pandas)."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from secform4strategy_clients.ports import MarketData

BAR_COLUMNS = ('date', 'open', 'close', 'close_as_traded', 'split', 'dividend', 'ticker')


@dataclass
class FakeMarketData:
    """Yahoo as it is now: each symbol's full daily history; a symbol absent from ``histories`` fails to download.

    Attributes:
        histories: Symbol -> its full history, in ``MarketData.price_histories``' columns (``ticker`` optional).
        calls: Every ``price_histories`` call, as (symbols, starts).
    """

    histories: Mapping[str, pd.DataFrame]
    calls: list[tuple[list[str], dict[str, date]]] = field(default_factory=list)

    def price_histories(self, symbols: Iterable[str], starts: Mapping[str, date] | None = None) -> pd.DataFrame:
        """Return each known symbol's history from its start, typed as the real client returns it."""
        symbols, starts = list(symbols), dict(starts or {})
        self.calls.append((symbols, starts))
        frames = [
            (bars[bars['date'] >= pd.Timestamp(starts[symbol])] if symbol in starts else bars).assign(ticker=symbol)
            for symbol in symbols
            if (bars := self.histories.get(symbol)) is not None
        ]
        return (
            (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=list(BAR_COLUMNS)))
            .astype(dict.fromkeys(BAR_COLUMNS[1:-1], 'float32') | {'ticker': 'category'})
            .assign(date=lambda d: pd.to_datetime(d['date']))
            .sort_values(['ticker', 'date'], ignore_index=True)
        )


_conforms: MarketData = FakeMarketData({})

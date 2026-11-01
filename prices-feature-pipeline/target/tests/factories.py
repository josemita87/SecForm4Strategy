"""Builders for target's inputs: transactions as read from BT4/BI4, and daily price bars.

Every field is defaulted, so a test names only what it is about. Times are written in New
York time, the way the market rules are defined, and converted to UTC as stored.
"""

from collections.abc import Mapping, Sequence

import pandas as pd
from src.helpers import as_of_keys, normalize_prices

TRANSACTION = {
    'accession': 'A-1',
    'position': 0,
    'ticker': 'AAA',
    'company_cik': '0000000001',
    'insider_cik': 'I1',
    'owner_code': '0100',
    'coding': 'S',
    'derivative': False,
    'rule105b1': False,
    'equity_swap': False,
    'ownership': 'D',
    'shares': 100.0,
    'price': 10.0,
    'remaining_shares': 900.0,
    'direct_holding': 0.0,
    'indirect_holding': 0.0,
    'date': '2024-03-05',
    'accepted_at': '2024-03-05 18:00',
}


def ny(moment: str) -> pd.Timestamp:
    """A New York wall-clock moment, in UTC."""
    return pd.Timestamp(moment, tz='America/New_York').tz_convert('UTC')


def transactions(*overrides: Mapping[str, object]) -> pd.DataFrame:
    """Transactions prepared as target prepares them (``as_of_keys``), one per override mapping."""
    rows = [TRANSACTION | dict(override) for override in overrides]
    frame = pd.DataFrame(rows).assign(key=[f'k{i}' for i in range(len(rows))])
    return as_of_keys(
        frame.assign(accepted_at=[ny(str(m)) for m in frame['accepted_at']], date=pd.to_datetime(frame['date']))
    )


def daily_bars(
    days: Sequence[str] | pd.DatetimeIndex,
    ticker: str = 'AAA',
    opens: Sequence[float] | None = None,
    closes: Sequence[float] | None = None,
    as_traded: Sequence[float] | None = None,
) -> pd.DataFrame:
    """Daily bars prepared as target prepares them (``normalize_prices``); prices default to 100."""
    days = pd.DatetimeIndex(days)
    return normalize_prices(
        pd.DataFrame(
            {
                'ticker': ticker,
                'date': days,
                'open': opens if opens is not None else 100.0,
                'close': closes if closes is not None else 100.0,
                'close_as_traded': as_traded if as_traded is not None else 100.0,
                'split': 0.0,
                'dividend': 0.0,
            }
        )
    )

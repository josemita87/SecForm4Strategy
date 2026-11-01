"""Frame preparation shared by the target steps: normalized bars, as-of keys, acceptance days and next prints."""

import numpy as np
import pandas as pd

from src.constants import MARKET_CLOSE, MARKET_OPEN, MARKET_TZ, Col


def normalize_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Return daily bars with naive-UTC ns ``date`` (naive read as UTC) and string tickers, sorted by date."""
    columns: dict[str, pd.Series] = {
        Col.DATE: pd.to_datetime(prices[Col.DATE], utc=True).dt.tz_localize(None).dt.as_unit('ns'),
        Col.TICKER: prices[Col.TICKER].astype(str),
    }
    prices = prices.assign(**columns).sort_values(Col.DATE)
    assert prices[Col.DATE].notna().all(), 'Price dates contain NaT'
    return prices


def as_of_keys(transactions: pd.DataFrame) -> pd.DataFrame:
    """Return the transactions with string tickers and UTC ns acceptance times, sorted for an as-of join on time."""
    columns: dict[str, pd.Series] = {
        Col.TICKER: transactions[Col.TICKER].astype(str),
        Col.ACCEPTED_AT: pd.to_datetime(transactions[Col.ACCEPTED_AT], utc=True).dt.as_unit('ns'),
    }
    return transactions.assign(**columns).sort_values(Col.ACCEPTED_AT)


def acceptance_day(accepted_at: pd.Series) -> pd.Series:
    """Return each acceptance's calendar day in New York as naive ns midnight, the key of daily bars and snapshots."""
    day: pd.Series = (
        pd.to_datetime(accepted_at, utc=True)
        .dt.tz_convert(MARKET_TZ)
        .dt.tz_localize(None)
        .dt.normalize()
        .dt.as_unit('ns')
    )
    return day


def next_print(accepted_at: pd.Series) -> pd.Series:
    """Return the first scheduled market price strictly after each acceptance, in UTC.

    That is the 09:30 open when accepted before it on a weekday, the 16:00 close when accepted in session,
    otherwise the next weekday's open (US/Eastern; exchange holidays are not modelled). It needs no
    price data, so it also places live filings whose entry price has not printed yet.

    Args:
        accepted_at: Acceptance times.

    Returns:
        The next print after each acceptance.
    """
    local = pd.to_datetime(accepted_at, utc=True).dt.tz_convert(MARKET_TZ).dt.tz_localize(None)
    day, weekday = local.dt.normalize(), ~local.dt.day_name().isin(('Saturday', 'Sunday'))
    since_midnight = local - day
    prints = np.where(
        weekday & (since_midnight < MARKET_OPEN),
        day + MARKET_OPEN,
        np.where(weekday & (since_midnight < MARKET_CLOSE), day + MARKET_CLOSE, day + pd.offsets.BDay(1) + MARKET_OPEN),
    )
    return pd.Series(
        pd.DatetimeIndex(prints).tz_localize(MARKET_TZ).tz_convert('UTC').as_unit('ns'), index=accepted_at.index
    )

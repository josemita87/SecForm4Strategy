"""Frame preparation shared by the target steps: bars, as-of keys, market clock, acceptance days, next prints."""

import calendar

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


def wall_clock(instants: pd.Series) -> pd.Series:
    """Return tz-aware instants as naive New York wall-clock times, the calendar of daily bars."""
    local: pd.Series = instants.dt.tz_convert(MARKET_TZ).dt.tz_localize(None)
    return local


def instant(wall_clock: pd.Series) -> pd.Series:
    """Return naive New York wall-clock times as UTC instants."""
    utc: pd.Series = wall_clock.dt.tz_localize(MARKET_TZ).dt.tz_convert('UTC')
    return utc


def acceptance_day(accepted_at: pd.Series) -> pd.Series:
    """Return each acceptance's New York calendar day at naive midnight, the as-of key of daily bars and snapshots."""
    day: pd.Series = wall_clock(accepted_at).dt.normalize()
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
    local = wall_clock(accepted_at)
    day, weekday = local.dt.normalize(), local.dt.dayofweek < calendar.SATURDAY
    since_midnight = local - day
    return instant(
        pd.Series(
            np.select(
                condlist=(weekday & (since_midnight < MARKET_OPEN), weekday & (since_midnight < MARKET_CLOSE)),
                choicelist=(day + MARKET_OPEN, day + MARKET_CLOSE),
                default=day + pd.offsets.BDay(1) + MARKET_OPEN,
            ),
            index=accepted_at.index,
        )
    )

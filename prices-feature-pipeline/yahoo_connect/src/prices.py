"""Price service: where each ticker's refresh resumes, and fetching final bars in batches on one price basis."""

import logging
from collections.abc import Iterator, Mapping, Sequence
from datetime import date
from itertools import batched
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import pandas as pd

from src.constants import MARKET_CLOSE, MARKET_TZ, REBASE_LOOKBACK_DAYS, Col

if TYPE_CHECKING:
    from secform4strategy_clients.ports import MarketData

logger = logging.getLogger(__name__)


class Resume(NamedTuple):
    """Where a ticker's refresh starts, and the adjustment factor of each stored day it fetches again."""

    start: date
    factors: Mapping[date, float]


def history_start(years: int) -> date:
    """Return the first day a full refetch downloads: ``years`` years before today.

    Args:
        years: Years of history to keep.

    Returns:
        Today minus ``years`` years.
    """
    return (pd.Timestamp.today() - pd.DateOffset(years=years)).date()


def resume_points(stored: pd.DataFrame) -> dict[str, Resume]:
    """Return where each ticker's refresh resumes; tickers without bars are absent and fetch from the history start.

    The refresh starts ``REBASE_LOOKBACK_DAYS`` stored days back. Those days' adjustment
    factors (adjusted over as-traded close) are kept, so ``rebased`` can tell whether a split or
    dividend has been applied to them since, even one Yahoo records late.

    Args:
        stored: Stored daily bars with ``ticker``, ``date``, ``close`` and ``close_as_traded``.

    Returns:
        Ticker -> where its refresh resumes.
    """
    recent = stored.sort_values(Col.DATE).groupby(Col.TICKER, observed=True).tail(REBASE_LOOKBACK_DAYS)
    points = {}
    for ticker, days in recent.groupby(Col.TICKER, observed=True):
        factors = dict(zip(days[Col.DATE].dt.date, days[Col.CLOSE] / days[Col.CLOSE_AS_TRADED], strict=True))
        points[str(ticker)] = Resume(min(factors), factors)
    return points


def rebased(bars: pd.DataFrame, resume: Mapping[str, Resume]) -> set[str]:
    """Return the resumed tickers whose stored adjusted prices are on an outdated basis.

    A split or dividend rebases every earlier adjusted price. It shows as a corporate action
    after the last stored day, or as a changed adjustment factor on a stored day fetched again;
    a ticker whose stored days did not come back at all is checked in full too. Factors move by
    an action's share of the price, so the 0.001% tolerance, above float32 and download noise,
    misses only dividends below 0.001% of the price.

    Args:
        bars: Newly fetched bars of tickers resuming from ``resume``.
        resume: Ticker -> where its refresh resumed.

    Returns:
        Tickers to fetch in full again.
    """
    tickers, days = bars[Col.TICKER].astype(str), bars[Col.DATE].dt.date
    actions = (bars[Col.SPLIT] != 0) | (bars[Col.DIVIDEND] != 0)
    after_stored = days > tickers.map(lambda t: max(resume[t].factors))
    stored_factor = pd.Series(
        [resume[t].factors.get(d, np.nan) for t, d in zip(tickers, days, strict=True)],
        index=bars.index,
        dtype='float64',
    )
    refetched = stored_factor.notna()
    moved = refetched & ~np.isclose(bars[Col.CLOSE] / bars[Col.CLOSE_AS_TRADED], stored_factor, rtol=1e-5)
    return set(tickers[(actions & after_stored) | moved]) | (set(resume) - set(tickers[refetched]))


def new_prices(
    market_data: MarketData, tickers: Sequence[str], resume: Mapping[str, Resume], batch_size: int, start: date
) -> Iterator[pd.DataFrame]:
    """Yield each batch's final daily bars as soon as it is downloaded, so the caller can store them as they arrive.

    A bar whose close has not printed yet (fetched mid-session) is left out, so no provisional
    close is ever stored. A ticker whose stored prices were rebased (``rebased``) gets its history
    from ``start`` again, which the caller's upsert writes over its stored bars from that day.

    Args:
        market_data: Source of daily price history.
        tickers: Tickers to refresh.
        resume: Ticker -> where its refresh resumes (``resume_points``); absent tickers fetch from ``start``.
        batch_size: Tickers downloaded per batch.
        start: First day of a full refetch (``history_start``).

    Yields:
        One batch's daily bars (``date``, ``open``, ``close``, ``close_as_traded``, ``split``, ``dividend``,
        ``ticker``).
    """
    for batch in batched(tickers, batch_size, strict=False):
        resumed = {ticker: point for ticker in batch if (point := resume.get(ticker))}
        bars = market_data.price_histories(
            batch, starts={ticker: resumed[ticker].start if ticker in resumed else start for ticker in batch}
        )
        if stale := rebased(bars[bars[Col.TICKER].astype(str).isin(resumed)], resumed):
            logger.info(f'Prices rebased since last stored, fetching again from {start}: {sorted(stale)}')
            full = market_data.price_histories(sorted(stale), starts=dict.fromkeys(stale, start))
            bars = pd.concat([bars[~bars[Col.TICKER].astype(str).isin(stale)], full], ignore_index=True)
        yield bars[bars[Col.DATE].dt.tz_localize(MARKET_TZ) + MARKET_CLOSE < pd.Timestamp.now(MARKET_TZ)]

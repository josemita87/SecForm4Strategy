"""Price service: the batch refresh of the prices group on one price basis, and the live path's recent closes."""

import logging
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, timedelta
from itertools import batched
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import pandas as pd
from pyiceberg.exceptions import NoSuchTableError
from tqdm import tqdm

from src.constants import LIVE_LOOKBACK_DAYS, MARKET_CLOSE, MARKET_TZ, REBASE_LOOKBACK_DAYS, Col, FeatureGroup

if TYPE_CHECKING:
    from secform4strategy_clients.ports import FeatureStore, MarketData

logger = logging.getLogger(__name__)


class Resume(NamedTuple):
    """Where a refresh starts, the first stored day, and the adjustment factors of the stored days fetched again."""

    start: date
    first: date
    factors: Mapping[date, float]


class PriceBatch(NamedTuple):
    """One batch's final bars: bars to upsert, and full histories that replace their tickers' stored bars."""

    appended: pd.DataFrame
    reloaded: pd.DataFrame


def history_start(years: int) -> date:
    """Return the first day a new ticker downloads: ``years`` years before today.

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
    by_ticker = stored.sort_values(Col.DATE).groupby(Col.TICKER, observed=True)
    firsts = by_ticker[Col.DATE].min()
    points = {}
    for ticker, days in by_ticker.tail(REBASE_LOOKBACK_DAYS).groupby(Col.TICKER, observed=True):
        factors = dict(zip(days[Col.DATE].dt.date, days[Col.CLOSE] / days[Col.CLOSE_AS_TRADED], strict=True))
        points[str(ticker)] = Resume(min(factors), firsts[ticker].date(), factors)
    return points


def rebased(bars: pd.DataFrame, resume: Mapping[str, Resume]) -> set[str]:
    """Return the resumed tickers whose stored adjusted prices are on an outdated basis, or hold a day without a price.

    A split or dividend rebases every earlier adjusted price. It shows as a corporate action
    after the last stored day, or as a changed adjustment factor on a stored day fetched again;
    a ticker whose stored days did not come back with a price is checked in full too. Factors move by
    an action's share of the price, so the 0.001% tolerance, above float32 and download noise,
    misses only dividends below 0.001% of the price.

    Args:
        bars: Newly fetched bars of tickers resuming from ``resume``.
        resume: Ticker -> where its refresh resumed.

    Returns:
        Tickers to reload in full.
    """
    tickers, days = bars[Col.TICKER].astype(str), bars[Col.DATE].dt.date
    actions = (bars[Col.SPLIT] != 0) | (bars[Col.DIVIDEND] != 0)
    after_stored = days > tickers.map(lambda t: max(resume[t].factors))
    stored_factor = pd.Series(
        [resume[t].factors.get(d, np.nan) for t, d in zip(tickers, days, strict=True)],
        index=bars.index,
        dtype='float64',
    )
    fetched_factor = bars[Col.CLOSE] / bars[Col.CLOSE_AS_TRADED]
    refetched = stored_factor.notna() & fetched_factor.notna()
    moved = refetched & ~np.isclose(fetched_factor, stored_factor, rtol=1e-5)
    unpriced = {ticker for ticker, point in resume.items() if np.isnan(list(point.factors.values())).any()}
    return set(tickers[(actions & after_stored) | moved]) | (set(resume) - set(tickers[refetched])) | unpriced


def final(bars: pd.DataFrame) -> pd.Series:
    """Return the mask of bars with a printed close: dated before the last regular-session close, and priced."""
    closed: pd.Series = bars[Col.DATE].dt.tz_localize(MARKET_TZ) + MARKET_CLOSE < pd.Timestamp.now(MARKET_TZ)
    return closed & bars[Col.CLOSE].notna() & bars[Col.CLOSE_AS_TRADED].notna()


def new_prices(
    market_data: MarketData, tickers: Sequence[str], resume: Mapping[str, Resume], batch_size: int, start: date
) -> Iterator[PriceBatch]:
    """Yield each batch's final daily bars as soon as it is downloaded, so the caller can store them as they arrive.

    A ticker whose stored prices were rebased (``rebased``) is downloaded again from its first stored
    day, or from ``start`` if that is earlier, into ``reloaded``: the caller replaces all its stored bars
    with them, so its bars are on one basis. A rebased ticker whose reload returns no bars is left out,
    and its stored bars stay as they are until a later refresh.

    Args:
        market_data: Source of daily price history.
        tickers: Tickers to refresh.
        resume: Ticker -> where its refresh resumes (``resume_points``); absent tickers fetch from ``start``.
        batch_size: Tickers downloaded per batch.
        start: First day a new ticker downloads (``history_start``).

    Yields:
        One batch's daily bars (``date``, ``open``, ``close``, ``close_as_traded``, ``split``, ``dividend``,
        ``ticker``), split into bars to upsert and full histories to replace.
    """
    for batch in batched(tickers, batch_size, strict=False):
        resumed = {ticker: point for ticker in batch if (point := resume.get(ticker))}
        bars = market_data.price_histories(
            batch, starts={ticker: resumed[ticker].start if ticker in resumed else start for ticker in batch}
        )
        fetched, reloaded = bars[Col.TICKER].astype(str), bars.iloc[:0]
        if stale := rebased(bars[fetched.isin(resumed)], resumed):
            logger.info(f'Prices rebased since last stored, reloading in full: {sorted(stale)}')
            reloaded = market_data.price_histories(
                sorted(stale), starts={ticker: min(resumed[ticker].first, start) for ticker in stale}
            )
            if missing := stale - set(reloaded[Col.TICKER].astype(str)):
                logger.warning(f'Reload returned no bars, keeping the stored ones: {sorted(missing)}')
        yield PriceBatch(bars[final(bars) & ~fetched.isin(stale)], reloaded[final(reloaded)])


def keyed(bars: pd.DataFrame) -> pd.DataFrame:
    """Return the bars with their one-column key ``<ticker>@<date>``; pyiceberg's upsert crashes on composite keys."""
    return bars.assign(**{Col.KEY: bars[Col.TICKER].astype(str) + '@' + bars[Col.DATE].dt.strftime('%Y-%m-%d')})


def refresh_prices(
    store: FeatureStore, market: MarketData, tickers: list[str], buffer_size: int, history_years: int
) -> None:
    """Fetch the tickers' bars after their last stored day and store them in the prices group.

    New bars are upserted. A rebased ticker's stored bars are replaced in one commit filtered by
    ticker, which rewrites only the data files holding that ticker.

    Args:
        store: The feature store holding the prices group.
        market: Where the bars come from.
        tickers: The tickers to price.
        buffer_size: Tickers fetched per batch; at most 200, so the replace filter prunes data files.
        history_years: Years of bars a ticker without stored prices fetches.
    """
    try:
        stored = store.read(
            FeatureGroup.PRICES,
            where={Col.TICKER: tickers},
            columns=(Col.TICKER, Col.DATE, Col.CLOSE, Col.CLOSE_AS_TRADED),
        )
    except NoSuchTableError:
        # The first run creates the price table, so every ticker fetches from the history start
        stored = pd.DataFrame({Col.TICKER: [], Col.DATE: pd.to_datetime([]), Col.CLOSE: [], Col.CLOSE_AS_TRADED: []})

    batches = new_prices(market, tickers, resume_points(stored), buffer_size, history_start(history_years))
    for batch in tqdm(batches, total=-(-len(tickers) // buffer_size), desc='Pricing tickers', unit='batch'):
        store.push(FeatureGroup.PRICES, keyed(batch.appended))
        store.replace(
            FeatureGroup.PRICES,
            keyed(batch.reloaded),
            where={Col.TICKER: sorted(set(batch.reloaded[Col.TICKER].astype(str)))},
        )


def recent_closes(market: MarketData, tickers: Sequence[str]) -> list[dict[str, str | float]]:
    """Return the tickers' final as-traded closes of the last ``LIVE_LOOKBACK_DAYS`` days, as JSON records.

    Args:
        market: Where the bars come from.
        tickers: The tickers to price.

    Returns:
        One ``{ticker, date, close_as_traded}`` record per final bar, ``date`` as ``YYYY-MM-DD``; none for a ticker
        whose download failed.
    """
    bars = market.price_histories(
        tickers, starts=dict.fromkeys(tickers, pd.Timestamp.now(MARKET_TZ).date() - timedelta(days=LIVE_LOOKBACK_DAYS))
    )
    closes = bars[final(bars)]
    if missing := set(tickers) - set(closes[Col.TICKER].astype(str)):
        logger.warning(f'No final closes, so their rows are not valued: {sorted(missing)}')
    return [
        {Col.TICKER: str(ticker), Col.DATE: f'{day:%Y-%m-%d}', Col.CLOSE_AS_TRADED: float(close)}
        for ticker, day, close in zip(closes[Col.TICKER], closes[Col.DATE], closes[Col.CLOSE_AS_TRADED], strict=True)
    ]

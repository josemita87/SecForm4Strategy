"""Target computations: aggregation, forward returns, and expanding averages."""

# import dask.dataframe as dd
import logging
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from src.constants import MARKET_CLOSE, MARKET_OPEN, MARKET_TZ

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from src.constants import ReferenceData

logger = logging.getLogger(__name__)


def data_aggregation(transactions: pd.DataFrame, agg_dict: Mapping[str, str | Callable[..., object]]) -> pd.DataFrame:
    """Aggregate transaction data using the provided aggregation dictionary.

    Aggregations are grouped by ``ticker`` and ``date``; keys in ``agg_dict``
    not present in the DataFrame columns are ignored.

    Args:
        transactions: DataFrame containing transaction data.
        agg_dict: Mapping of column names to aggregation operations.

    Returns:
        The aggregated transaction data.
    """
    # Filter out columns that are not present in the transactions DataFrame
    valid_agg_dict = {k: v for k, v in agg_dict.items() if k in transactions.columns}
    return transactions.groupby(['ticker', 'date'], as_index=False).agg(valid_agg_dict)


def compute_target(transactions: pd.DataFrame, prices: pd.DataFrame, period: int) -> pd.DataFrame:
    """Forward return from the first price a trader could act on after each filing's acceptance.

    Every trading day offers two prices, its 09:30 open and its 16:00 close (US/Eastern).
    A filing accepted at ``timestamp`` (epoch ms) enters at the first of them strictly
    after acceptance: the same day's close when accepted in session, the next open
    otherwise. It exits at the first close ``period`` days after the entry day;
    ``label_known_at`` (epoch ms) is that exit close. Share counts become USD values.

    Args:
        transactions: Transactions with ``ticker``, ``date``, ``timestamp`` and share columns.
        prices: Daily bars with ``ticker``, ``date``, ``open`` and ``close``.
        period: Days between the entry day and the exit.

    Returns:
        Transactions with ``pct_change`` and ``label_known_at``, and USD value columns.
    """
    # Trade dates and price days as naive UTC midnights; tickers as plain strings for the as-of joins
    transactions['date'] = pd.to_datetime(transactions['date']).dt.tz_convert('UTC').dt.tz_localize(None)
    prices = prices.assign(
        date=pd.to_datetime(prices['date']).dt.tz_convert('UTC').dt.tz_localize(None),
        ticker=prices['ticker'].astype(str),
    ).sort_values('date')
    assert prices['date'].notna().all(), 'Price dates contain NaT'
    assert transactions['timestamp'].notna().all(), 'Acceptance timestamps contain NaN'

    # One row per tradable price: each day's open and close, stamped at the moment it prints
    session = prices['date'].dt.tz_localize(MARKET_TZ)
    events = pd.concat(
        [
            pd.DataFrame(
                {
                    'ticker': prices['ticker'],
                    'entry_day': prices['date'],
                    'start_price': prices[col],
                    'at': session + offset,
                }
            )
            for col, offset in (('open', MARKET_OPEN), ('close', MARKET_CLOSE))
        ]
    ).dropna(subset=['start_price'])
    events = events.assign(at=events['at'].astype('int64') // 10**6).sort_values('at')

    start = pd.merge_asof(
        transactions.assign(
            ticker=transactions['ticker'].astype(str), timestamp=transactions['timestamp'].astype('int64')
        ).sort_values('timestamp'),
        events,
        left_on='timestamp',
        right_on='at',
        by='ticker',
        direction='forward',
        allow_exact_matches=False,
    )
    start['exit_due'] = start['entry_day'] + pd.to_timedelta(period, unit='D')
    start = start[start['exit_due'] <= prices['date'].max()]

    # Exit: the first close on or after `period` days past the entry day
    es = pd.merge_asof(
        start.sort_values('exit_due'),
        prices[['ticker', 'date', 'close']].rename(columns={'date': 'exit_date', 'close': 'end_price'}),
        left_on='exit_due',
        right_on='exit_date',
        by='ticker',
        direction='forward',
    )
    es['pct_change'] = ((es['end_price'] - es['start_price']) / es['start_price']).round(5)
    exit_close = (es['exit_date'] + MARKET_CLOSE).dt.tz_localize(MARKET_TZ)
    es['label_known_at'] = exit_close.dropna().astype('int64') // 10**6

    # Collapse price columns into one: this way we increase likelihood of having a price to work with
    es['price'] = np.where(es['price'] == 0, es['start_price'], es['price'])

    # Convert the shares-related counts to USD value
    es[['tx_value', 'remaining_value', 'direct_holding', 'indirect_holding']] = es[
        ['shares', 'remaining_shares', 'direct_holding', 'indirect_holding']
    ].mul(es['price'], axis=0)

    es = es.drop(
        columns=['at', 'entry_day', 'exit_due', 'exit_date', 'start_price', 'end_price', 'remaining_shares', 'shares']
    )
    logger.debug(f'Target computation completed. Shape: {es.shape}')
    return es


def compute_RT4(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``avg_target_expanding``: each row's mean of its ticker's targets already known at its acceptance.

    A row accepted at ``timestamp`` averages the same ticker's ``pct_change`` over rows
    whose ``label_known_at`` (the exit close) is at or before it, so no return that was
    still unrealised at acceptance leaks into the feature. Every row needs a
    ``label_known_at``; unlabelled rows are dropped before this step.

    Args:
        df: Transactions with ``ticker``, ``timestamp``, ``pct_change`` and ``label_known_at``.

    Returns:
        The DataFrame sorted by ticker and time, with an added ``avg_target_expanding`` column.
    """
    df = df.sort_values(['ticker', 'timestamp'])
    if df.empty:
        return df.assign(avg_target_expanding=np.nan)

    tickers = df['ticker'].to_numpy()
    accepted = df['timestamp'].to_numpy(np.int64)
    known_at = df['label_known_at'].to_numpy(np.int64)
    pct = df['pct_change'].to_numpy(np.float64)

    # Each ticker is one contiguous block; `start` is the first row of each row's block
    new_block = np.r_[True, tickers[1:] != tickers[:-1]]
    block = np.cumsum(new_block) - 1
    start = np.flatnonzero(new_block)[block]

    # Exit times grow with acceptance inside a block; per-block key ranges never overlap, so one
    # search finds, per row, how many of its ticker's labels were known at its acceptance
    origin = min(accepted.min(), known_at.min())
    span = int(max(accepted.max(), known_at.max()) - origin) + 1
    known_keys = block * span + (known_at - origin)
    assert (np.diff(known_keys) >= 0).all(), 'label_known_at must not decrease with acceptance within a ticker'
    end = np.searchsorted(known_keys, block * span + (accepted - origin), side='right')

    # Mean of the known rows' non-NaN targets, from prefix sums differenced within the block
    sums, counts = (np.r_[0, np.cumsum(a)] for a in (np.nan_to_num(pct), ~np.isnan(pct)))
    known = counts[end] - counts[start]
    df['avg_target_expanding'] = np.divide(
        sums[end] - sums[start], known, out=np.full(len(df), np.nan), where=known > 0
    )
    return df


def add_company_context(transactions: pd.DataFrame, reference: ReferenceData) -> pd.DataFrame:
    """Attach each transaction's market cap, exchange, SIC code and SIC group.

    Market cap is the latest monthly snapshot strictly before the filing's acceptance
    day, so it is what a trader knew at disclosure; the rest is looked up by issuer CIK.

    Args:
        transactions: Transactions with ``ticker``, ``company_cik`` and acceptance ``timestamp``.
        reference: Market-cap snapshots and the company table.

    Returns:
        The transactions with ``market_cap``, ``exchange``, ``sic`` and ``sic_group`` columns.
    """
    accepted_day = (
        pd.to_datetime(transactions['timestamp'], unit='ms', utc=True)
        .dt.tz_convert(MARKET_TZ)
        .dt.tz_localize(None)
        .dt.normalize()
    )
    enriched = pd.merge_asof(
        transactions.assign(ticker=transactions['ticker'].astype(str), snapshot=accepted_day).sort_values('snapshot'),
        reference.market_caps,
        on='snapshot',
        by='ticker',
        direction='backward',
        allow_exact_matches=False,
    ).drop(columns='snapshot')
    cik = enriched['company_cik'].astype(str).str.zfill(10)
    return enriched.assign(**{col: cik.map(reference.companies[col]) for col in ('exchange', 'sic', 'sic_group')})


def map_RT4_to_BIR4(target_dataframe: pd.DataFrame, inference_data: pd.DataFrame) -> pd.DataFrame:
    """Map the latest expanding target values onto the inference DataFrame.

    Takes the most recent ``avg_target_expanding`` value per ticker from the
    target DataFrame and maps it onto the inference DataFrame by ticker.

    Args:
        target_dataframe: DataFrame containing at least ``ticker``, ``date``
            and ``avg_target_expanding``.
        inference_data: DataFrame to which the ``avg_target_expanding`` column
            is added based on matching ticker.

    Returns:
        The inference DataFrame with an added ``avg_target_expanding`` column.
    """
    # Group the dataframe by ticker and select the last row (in terms of date) for each ticker
    df = target_dataframe.sort_values(by='date', ascending=True).groupby('ticker').tail(1)

    # Map the values
    inference_data['avg_target_expanding'] = inference_data['ticker'].map(
        df.set_index('ticker')['avg_target_expanding']
    )

    return inference_data

"""Target computations: aggregation, forward returns, and expanding averages."""

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from src.constants import MARKET_CLOSE, MARKET_OPEN, MARKET_TZ, Column

if TYPE_CHECKING:
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
    return transactions.groupby([Column.TICKER, Column.DATE], as_index=False, observed=True).agg(valid_agg_dict)


def _normalize_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Return daily bars with naive-UTC ns ``date`` (naive read as UTC) and string tickers, sorted by date."""
    columns: dict[str, pd.Series] = {
        Column.DATE: pd.to_datetime(prices[Column.DATE], utc=True).dt.tz_localize(None).dt.as_unit('ns'),
        Column.TICKER: prices[Column.TICKER].astype(str),
    }
    prices = prices.assign(**columns).sort_values(Column.DATE)
    assert prices[Column.DATE].notna().all(), 'Price dates contain NaT'
    return prices


def _as_of_keys(transactions: pd.DataFrame) -> pd.DataFrame:
    """Return the transactions with string tickers and int64 acceptance times, sorted for an as-of join on time."""
    columns: dict[str, pd.Series] = {
        Column.TICKER: transactions[Column.TICKER].astype(str),
        Column.TIMESTAMP: transactions[Column.TIMESTAMP].astype('int64'),
    }
    return transactions.assign(**columns).sort_values(Column.TIMESTAMP)


def add_value_features(transactions: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Price each transaction and value its share counts in USD, using only prices known at acceptance.

    Training and inference both run this step, so the model sees the same features in
    both. A transaction without a reported ``price`` (0) takes its ticker's last close
    strictly before acceptance. ``tx_value``, ``remaining_value``, ``direct_holding``
    and ``indirect_holding`` become USD values; ``shares`` and ``remaining_shares`` go.

    Args:
        transactions: Transactions with ``ticker``, acceptance ``timestamp``, ``price`` and share columns.
        prices: Daily bars with ``ticker``, ``date`` and ``close``.

    Returns:
        The transactions with USD value columns.
    """
    prices = _normalize_prices(prices)
    closes = pd.DataFrame(
        {
            Column.TICKER: prices[Column.TICKER],
            Column.LAST_CLOSE: prices[Column.CLOSE],
            Column.CLOSED_AT: (prices[Column.DATE].dt.tz_localize(MARKET_TZ) + MARKET_CLOSE).astype('int64') // 10**6,
        }
    ).sort_values(Column.CLOSED_AT)
    valued = pd.merge_asof(
        _as_of_keys(transactions),
        closes,
        left_on=Column.TIMESTAMP,
        right_on=Column.CLOSED_AT,
        by=Column.TICKER,
        direction='backward',
        allow_exact_matches=False,
    )
    valued[Column.PRICE] = valued[Column.PRICE].where(valued[Column.PRICE] != 0, valued[Column.LAST_CLOSE])
    valued[[Column.TX_VALUE, Column.REMAINING_VALUE, Column.DIRECT_HOLDING, Column.INDIRECT_HOLDING]] = valued[
        [Column.SHARES, Column.REMAINING_SHARES, Column.DIRECT_HOLDING, Column.INDIRECT_HOLDING]
    ].mul(valued[Column.PRICE], axis=0)
    return valued.drop(columns=[Column.LAST_CLOSE, Column.CLOSED_AT, Column.SHARES, Column.REMAINING_SHARES])


def compute_target(transactions: pd.DataFrame, prices: pd.DataFrame, period: int) -> pd.DataFrame:
    """Label each transaction with the forward return from the first price a trader could act on.

    Every trading day offers two prices, its 09:30 open and its 16:00 close (US/Eastern).
    A filing accepted at ``timestamp`` (epoch ms) enters at the first of them strictly
    after acceptance: the same day's close when accepted in session, the next open
    otherwise. It exits at the first close ``period`` days after the entry day;
    ``label_known_at`` (epoch ms) is that exit close.

    Args:
        transactions: Transactions with ``ticker``, ``date`` and ``timestamp``.
        prices: Daily bars with ``ticker``, ``date``, ``open`` and ``close``.
        period: Days between the entry day and the exit.

    Returns:
        Transactions with ``pct_change`` and ``label_known_at``.
    """
    transactions = transactions.assign(
        **{Column.DATE: pd.to_datetime(transactions[Column.DATE], utc=True).dt.tz_localize(None).dt.as_unit('ns')}
    )
    prices = _normalize_prices(prices)
    assert transactions[Column.TIMESTAMP].notna().all(), 'Acceptance timestamps contain NaN'

    # One row per tradable price: each day's open and close, stamped at the moment it prints
    session = prices[Column.DATE].dt.tz_localize(MARKET_TZ)
    events = pd.concat(
        [
            pd.DataFrame(
                {
                    Column.TICKER: prices[Column.TICKER],
                    Column.ENTRY_DAY: prices[Column.DATE],
                    Column.ENTRY_PRICE: prices[col],
                    Column.PRINTS_AT: (session + offset).astype('int64') // 10**6,
                }
            )
            for col, offset in ((Column.OPEN, MARKET_OPEN), (Column.CLOSE, MARKET_CLOSE))
        ]
    ).dropna(subset=[Column.ENTRY_PRICE])

    start = pd.merge_asof(
        _as_of_keys(transactions),
        events.sort_values(Column.PRINTS_AT),
        left_on=Column.TIMESTAMP,
        right_on=Column.PRINTS_AT,
        by=Column.TICKER,
        direction='forward',
        allow_exact_matches=False,
    )
    start[Column.EXIT_DUE] = start[Column.ENTRY_DAY] + pd.to_timedelta(period, unit='D')
    start = start[start[Column.EXIT_DUE] <= prices[Column.DATE].max()]

    # Exit: the first close on or after `period` days past the entry day
    es = pd.merge_asof(
        start.sort_values(Column.EXIT_DUE),
        prices[[Column.TICKER, Column.DATE, Column.CLOSE]].rename(
            columns={Column.DATE: Column.EXIT_DATE, Column.CLOSE: Column.EXIT_PRICE}
        ),
        left_on=Column.EXIT_DUE,
        right_on=Column.EXIT_DATE,
        by=Column.TICKER,
        direction='forward',
    )
    es[Column.PCT_CHANGE] = ((es[Column.EXIT_PRICE] - es[Column.ENTRY_PRICE]) / es[Column.ENTRY_PRICE]).round(5)
    exit_close = (es[Column.EXIT_DATE] + MARKET_CLOSE).dt.tz_localize(MARKET_TZ)
    es[Column.LABEL_KNOWN_AT] = exit_close.dropna().astype('int64') // 10**6

    es = es.drop(
        columns=[
            Column.PRINTS_AT,
            Column.ENTRY_DAY,
            Column.EXIT_DUE,
            Column.EXIT_DATE,
            Column.ENTRY_PRICE,
            Column.EXIT_PRICE,
        ]
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
    df = df.sort_values([Column.TICKER, Column.TIMESTAMP])
    if df.empty:
        return df.assign(**{Column.AVG_TARGET_EXPANDING: np.nan})

    tickers = df[Column.TICKER].to_numpy()
    accepted = df[Column.TIMESTAMP].to_numpy(np.int64)
    known_at = df[Column.LABEL_KNOWN_AT].to_numpy(np.int64)
    pct = df[Column.PCT_CHANGE].to_numpy(np.float64)

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
    df[Column.AVG_TARGET_EXPANDING] = np.divide(
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
        pd.to_datetime(transactions[Column.TIMESTAMP], unit='ms', utc=True)
        .dt.tz_convert(MARKET_TZ)
        .dt.tz_localize(None)
        .dt.normalize()
    )
    enriched = pd.merge_asof(
        _as_of_keys(transactions).assign(**{Column.SNAPSHOT: accepted_day}).sort_values(Column.SNAPSHOT),
        reference.market_caps,
        on=Column.SNAPSHOT,
        by=Column.TICKER,
        direction='backward',
        allow_exact_matches=False,
    ).drop(columns=Column.SNAPSHOT)
    cik = enriched[Column.COMPANY_CIK].astype(str).str.zfill(10)
    return enriched.assign(
        **{col: cik.map(reference.companies[col]) for col in (Column.EXCHANGE, Column.SIC, Column.SIC_GROUP)}
    )


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
    latest = target_dataframe.sort_values(Column.DATE).groupby(Column.TICKER).tail(1).set_index(Column.TICKER)
    return inference_data.assign(
        **{Column.AVG_TARGET_EXPANDING: inference_data[Column.TICKER].map(latest[Column.AVG_TARGET_EXPANDING])}
    )

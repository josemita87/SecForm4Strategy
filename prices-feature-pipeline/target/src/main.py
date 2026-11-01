"""Entry point and core steps: label training transactions (BT4 -> RT4) or prepare inference ones (BI4 -> BIR4)."""

import logging
import time
from collections.abc import Hashable
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore

from src.config import config
from src.constants import MARKET_CLOSE, MARKET_OPEN, MARKET_TZ, MAX_PRICE_GAP, Col, FeatureGroup, ReferenceData
from src.helpers import acceptance_day, as_of_keys, next_print, normalize_prices
from src.paths import SPEC_PATH

if TYPE_CHECKING:
    from secform4strategy_clients.ports import FeatureStore

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)


def aggregate_disclosures(transactions: pd.DataFrame) -> pd.DataFrame:
    """Collapse transactions into one row per ticker and next market print: what a trader knows at that price.

    Every filing accepted between two consecutive prints is in the same row, so the row is
    complete at its latest acceptance and loses no trading opportunity. Stock and derivative
    quantities stay apart; remaining shares count each insider's position after their last
    stock transaction (ordered by acceptance, trade date and place in the filing's table, so
    buys and sales alike) in each ownership line (direct, indirect), and stock holdings each
    filing once. Transaction codes and insider roles
    become flags; ``filing_lag_days`` runs from the earliest trade to acceptance.

    Args:
        transactions: Transactions prepared by ``as_of_keys``, reported ``price`` 0 meaning none.

    Returns:
        One row per ``ticker`` and ``next_print``, keyed ``<ticker>@<next print>``.
    """
    stock = ~transactions[Col.DERIVATIVE].astype(bool)
    tx = transactions.assign(
        **{
            Col.NEXT_PRINT: next_print(transactions[Col.ACCEPTED_AT]),
            Col.SHARES: transactions[Col.SHARES].where(stock, 0.0),
            Col.DERIVATIVE_SHARES: transactions[Col.SHARES].where(~stock, 0.0),
            # A stock price only, and 0 means "not reported", so the mean covers reported stock prices
            Col.PRICE: transactions[Col.PRICE].where(stock & (transactions[Col.PRICE] > 0)),
            Col.DIRECTOR: transactions[Col.OWNER_CODE].str[0] == '1',
            Col.OFFICER: transactions[Col.OWNER_CODE].str[1] == '1',
            Col.SALE: transactions[Col.CODING] == 'S',
            Col.GIFT: transactions[Col.CODING] == 'G',
            Col.TAX_WITHHOLDING: transactions[Col.CODING] == 'F',
            Col.EXERCISE: transactions[Col.CODING].isin(['M', 'C', 'X']),
        }
    )
    keys = [Col.TICKER, Col.NEXT_PRINT]
    rows = (
        tx.groupby(keys, observed=True)
        .agg(
            **{
                Col.COMPANY_CIK: (Col.COMPANY_CIK, 'first'),
                Col.ACCEPTED_AT: (Col.ACCEPTED_AT, 'max'),
                Col.DATE: (Col.DATE, 'min'),
                Col.N_FILINGS: (Col.ACCESSION, 'nunique'),
                Col.N_INSIDERS: (Col.INSIDER_CIK, 'nunique'),
                Col.SHARES: (Col.SHARES, 'sum'),
                Col.DERIVATIVE_SHARES: (Col.DERIVATIVE_SHARES, 'sum'),
                Col.PRICE: (Col.PRICE, 'mean'),
                **{
                    flag: (flag, 'any')
                    for flag in (
                        Col.RULE_10B5_1,
                        Col.EQUITY_SWAP,
                        Col.DIRECTOR,
                        Col.OFFICER,
                        Col.SALE,
                        Col.GIFT,
                        Col.TAX_WITHHOLDING,
                        Col.EXERCISE,
                    )
                },
            }
        )
        .join(
            tx[stock]
            .sort_values([Col.ACCEPTED_AT, Col.DATE, Col.POSITION])
            .groupby([*keys, Col.INSIDER_CIK, Col.OWNERSHIP], observed=True, dropna=False)[Col.REMAINING_SHARES]
            .last()
            .groupby(level=keys)
            .sum()
        )
        .join(
            tx[stock]
            .groupby([*keys, Col.ACCESSION], observed=True)[[Col.DIRECT_HOLDING, Col.INDIRECT_HOLDING]]
            .first()
            .groupby(level=keys)
            .sum()
        )
        .fillna({Col.REMAINING_SHARES: 0.0, Col.DIRECT_HOLDING: 0.0, Col.INDIRECT_HOLDING: 0.0})
        .reset_index()
    )
    return rows.assign(
        **{
            Col.KEY: rows[Col.TICKER] + '@' + rows[Col.NEXT_PRINT].dt.strftime('%Y-%m-%dT%H:%MZ'),
            Col.FILING_LAG_DAYS: (acceptance_day(rows[Col.ACCEPTED_AT]) - rows[Col.DATE]).dt.days,
        }
    )


def add_cluster_features(rows: pd.DataFrame, transactions: pd.DataFrame, window: pd.Timedelta) -> pd.DataFrame:
    """Count the distinct insiders, officers and filings of each row's ticker disclosed in the trailing window.

    The window ends at the row's acceptance and includes it, so a row sees only filings
    already public: the selling that preceded it, plus its own.

    Args:
        rows: Disclosure rows from ``aggregate_disclosures``.
        transactions: The transactions they were built from, prepared by ``as_of_keys``.
        window: How far back the counts reach.

    Returns:
        The rows with ``insiders_recent``, ``officers_recent`` and ``filings_recent``.
    """
    rows = rows.sort_values([Col.TICKER, Col.ACCEPTED_AT])
    by_ticker = dict(tuple(transactions.sort_values(Col.ACCEPTED_AT).groupby(Col.TICKER, observed=True)))
    counts = []
    for ticker, group in rows.groupby(Col.TICKER, observed=True, sort=True):
        tx = by_ticker[ticker]
        times = tx[Col.ACCEPTED_AT].to_numpy('datetime64[ns]')
        until = group[Col.ACCEPTED_AT].to_numpy('datetime64[ns]')
        since = np.searchsorted(times, until - window.to_timedelta64(), side='right')
        upto = np.searchsorted(times, until, side='right')
        insiders, filings = tx[Col.INSIDER_CIK].to_numpy(), tx[Col.ACCESSION].to_numpy()
        officers = tx[Col.OWNER_CODE].str[1].eq('1').to_numpy()
        counts += [
            (len(set(insiders[a:b])), len(set(insiders[a:b][officers[a:b]])), len(set(filings[a:b])))
            for a, b in zip(since, upto, strict=True)
        ]
    recent = pd.DataFrame(
        counts, index=rows.index, columns=[Col.INSIDERS_RECENT, Col.OFFICERS_RECENT, Col.FILINGS_RECENT]
    )
    return rows.join(recent)


def add_value_features(transactions: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Value each row's share counts in USD at its ticker's as-traded close on the last day before acceptance.

    Training and inference both run this step, so the model sees the same features in
    both. Every row is valued at that one market price: known at disclosure whatever the
    acceptance time, and in that day's shares, like the filing's counts. Values are
    therefore comparable across transaction types and across later splits. ``tx_value``,
    ``derivative_value`` (notional: underlying shares at the stock's close),
    ``remaining_value``, ``direct_holding`` and ``indirect_holding`` become USD values; the
    share counts go. The reported ``price`` is kept as is and compared with the close as
    ``price_to_close`` (missing when no price was reported).

    Args:
        transactions: Disclosure rows prepared by ``as_of_keys``, with reported ``price`` (missing if none) and
            share columns.
        prices: Daily bars prepared by ``normalize_prices``, with ``close_as_traded``.

    Returns:
        The transactions with USD value columns and ``price_to_close``.
    """
    # Backward and not exact: the last trading day strictly before the acceptance day (weekends and holidays skipped)
    valued = pd.merge_asof(
        transactions.assign(**{Col.ACCEPTED_DAY: acceptance_day(transactions[Col.ACCEPTED_AT])}).sort_values(
            Col.ACCEPTED_DAY
        ),
        prices[[Col.TICKER, Col.DATE, Col.CLOSE_AS_TRADED]].rename(
            columns={Col.DATE: Col.CLOSE_DAY, Col.CLOSE_AS_TRADED: Col.LAST_CLOSE}
        ),
        left_on=Col.ACCEPTED_DAY,
        right_on=Col.CLOSE_DAY,
        by=Col.TICKER,
        direction='backward',
        allow_exact_matches=False,
    )
    # Each USD value column and the share count it values
    for value, count in (
        (Col.TX_VALUE, Col.SHARES),
        (Col.DERIVATIVE_VALUE, Col.DERIVATIVE_SHARES),
        (Col.REMAINING_VALUE, Col.REMAINING_SHARES),
        (Col.DIRECT_HOLDING, Col.DIRECT_HOLDING),
        (Col.INDIRECT_HOLDING, Col.INDIRECT_HOLDING),
    ):
        valued[value] = valued[count] * valued[Col.LAST_CLOSE]
    valued[Col.PRICE_TO_CLOSE] = valued[Col.PRICE] / valued[Col.LAST_CLOSE]
    return valued.drop(
        columns=[
            Col.LAST_CLOSE,
            Col.CLOSE_DAY,
            Col.ACCEPTED_DAY,
            Col.SHARES,
            Col.DERIVATIVE_SHARES,
            Col.REMAINING_SHARES,
        ]
    )


def compute_target(transactions: pd.DataFrame, prices: pd.DataFrame, period: int) -> pd.DataFrame:
    """Label each disclosure row with the forward return from the first price a trader could act on.

    Every trading day offers two prices, its 09:30 open and its 16:00 close (US/Eastern).
    A filing accepted at ``accepted_at`` enters at the first of them strictly
    after acceptance: the same day's close when accepted in session, the next open
    otherwise. It exits at the first close ``period`` days after the entry day;
    ``label_known_at`` (UTC) is that exit close. A transaction stays unlabelled when its
    exit falls after its ticker's last bar, or when the entry or exit price is more than
    ``MAX_PRICE_GAP`` away (a gap in the ticker's bars).

    Args:
        transactions: Disclosure rows prepared by ``as_of_keys``.
        prices: Daily bars prepared by ``normalize_prices``, with adjusted ``open`` and ``close``.
        period: Days between the entry day and the exit.

    Returns:
        The rows with ``pct_change`` and ``label_known_at``.
    """
    assert transactions[Col.ACCEPTED_AT].notna().all(), 'Acceptance times contain NaT'

    # One row per tradable price: each day's open and close, stamped at the moment it prints
    events = pd.concat(
        [
            pd.DataFrame(
                {
                    Col.TICKER: prices[Col.TICKER],
                    Col.ENTRY_DAY: prices[Col.DATE],
                    Col.ENTRY_PRICE: prices[col],
                    Col.PRINTS_AT: (prices[Col.DATE].dt.tz_localize(MARKET_TZ) + offset).dt.tz_convert('UTC'),
                }
            )
            for col, offset in ((Col.OPEN, MARKET_OPEN), (Col.CLOSE, MARKET_CLOSE))
        ]
    ).dropna(subset=[Col.ENTRY_PRICE])

    start = pd.merge_asof(
        transactions.sort_values(Col.ACCEPTED_AT),
        events.sort_values(Col.PRINTS_AT),
        left_on=Col.ACCEPTED_AT,
        right_on=Col.PRINTS_AT,
        by=Col.TICKER,
        direction='forward',
        allow_exact_matches=False,
        tolerance=MAX_PRICE_GAP,
    )
    start[Col.EXIT_DUE] = start[Col.ENTRY_DAY] + pd.to_timedelta(period, unit='D')
    # Only exits the ticker's own bars already reach can be labelled
    start = start[start[Col.EXIT_DUE] <= start[Col.TICKER].map(prices.groupby(Col.TICKER)[Col.DATE].max())]

    # Exit: the first close on or after `period` days past the entry day
    es = pd.merge_asof(
        start.sort_values(Col.EXIT_DUE),
        prices[[Col.TICKER, Col.DATE, Col.CLOSE]].rename(columns={Col.DATE: Col.EXIT_DATE, Col.CLOSE: Col.EXIT_PRICE}),
        left_on=Col.EXIT_DUE,
        right_on=Col.EXIT_DATE,
        by=Col.TICKER,
        direction='forward',
        allow_exact_matches=True,
        tolerance=MAX_PRICE_GAP,
    )
    es[Col.PCT_CHANGE] = ((es[Col.EXIT_PRICE] - es[Col.ENTRY_PRICE]) / es[Col.ENTRY_PRICE]).round(5)
    es[Col.LABEL_KNOWN_AT] = (es[Col.EXIT_DATE].dt.tz_localize(MARKET_TZ) + MARKET_CLOSE).dt.tz_convert('UTC')

    es = es.drop(
        columns=[
            Col.PRINTS_AT,
            Col.ENTRY_DAY,
            Col.EXIT_DUE,
            Col.EXIT_DATE,
            Col.ENTRY_PRICE,
            Col.EXIT_PRICE,
        ]
    )
    logger.debug(f'Target computation completed. Shape: {es.shape}')
    return es


def known_label_mean(labels: pd.DataFrame, rows: pd.DataFrame) -> np.ndarray:
    """Return, for each row, the mean ``pct_change`` of its ticker's labels already known at its acceptance.

    A label counts once its ``label_known_at`` (the exit close) is at or before the row's
    ``accepted_at``, so no return still unrealised at acceptance leaks in. Training (each RT4
    row against RT4) and inference (each BIR4 row against RT4) use this one rule.

    Args:
        labels: Labelled rows with ``ticker``, ``label_known_at`` and ``pct_change``.
        rows: Rows with ``ticker`` and ``accepted_at``, in the order of the result.

    Returns:
        The mean per row; NaN where no label of its ticker was known yet.
    """
    means = np.full(len(rows), np.nan)
    by_ticker: dict[Hashable, pd.DataFrame] = dict(
        tuple(labels.sort_values(Col.LABEL_KNOWN_AT).groupby(Col.TICKER, observed=True))
    )
    accepted = rows[Col.ACCEPTED_AT].to_numpy('datetime64[ns]')
    for ticker, positions in rows.groupby(Col.TICKER, observed=True).indices.items():
        if (known := by_ticker.get(ticker)) is None:
            continue
        pct = known[Col.PCT_CHANGE].to_numpy(np.float64)
        # Prefix sums over the labels in the order they became known; a search counts each row's known ones
        sums, counts = (np.r_[0, np.cumsum(a)] for a in (np.nan_to_num(pct), ~np.isnan(pct)))
        n = np.searchsorted(known[Col.LABEL_KNOWN_AT].to_numpy('datetime64[ns]'), accepted[positions], side='right')
        means[positions] = np.divide(sums[n], counts[n], out=np.full(len(n), np.nan), where=counts[n] > 0)
    return means


def add_company_context(transactions: pd.DataFrame, reference: ReferenceData) -> pd.DataFrame:
    """Attach each transaction's market cap, exchange, SIC code and SIC group.

    Market cap is the latest monthly snapshot strictly before the filing's acceptance
    day, so it is what a trader knew at disclosure; the rest is looked up by issuer CIK.

    Args:
        transactions: Transactions prepared by ``as_of_keys``, with ``company_cik``.
        reference: Market-cap snapshots and the company table.

    Returns:
        The transactions with ``market_cap``, ``exchange``, ``sic`` and ``sic_group`` columns.
    """
    enriched = pd.merge_asof(
        transactions.assign(**{Col.SNAPSHOT: acceptance_day(transactions[Col.ACCEPTED_AT])}).sort_values(Col.SNAPSHOT),
        reference.market_caps,
        on=Col.SNAPSHOT,
        by=Col.TICKER,
        direction='backward',
        allow_exact_matches=False,
    ).drop(columns=Col.SNAPSHOT)
    cik = enriched[Col.COMPANY_CIK].astype(str).str.zfill(10)
    return enriched.assign(**{col: cik.map(reference.companies[col]) for col in (Col.EXCHANGE, Col.SIC, Col.SIC_GROUP)})


if __name__ == '__main__':
    if config.system_training:
        source = FeatureGroup.BT4
    elif config.system_inference:
        source = FeatureGroup.BI4
    else:
        raise RuntimeError('No run mode selected: set SYSTEM_TRAINING or SYSTEM_INFERENCE')

    time.sleep(config.delay)
    store: FeatureStore = IcebergFeatureStore(
        load_catalog('default'),
        config.feature_store_namespace,
        load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
    )
    transactions = store.read(source, where={config.filter_key: config.acquired_disposed})
    logger.info(f'{len(tickers := transactions[Col.TICKER].unique())} tickers in {source.name}')
    # Without prices no feature or target can be computed, so a failed read fails the run
    P = normalize_prices(store.read(FeatureGroup.PRICES, where={Col.TICKER: tickers}))

    # One row per ticker and next print, with its insider cluster, company context and USD values known at acceptance
    keyed = as_of_keys(transactions)
    daily = add_value_features(
        add_company_context(
            add_cluster_features(aggregate_disclosures(keyed), keyed, pd.Timedelta(days=config.cluster_window_days)),
            ReferenceData.load(config.market_caps_path, config.companies_path),
        ),
        P,
    )
    if config.system_training:
        # Every labelled row is kept, its average missing until a label of its ticker is known
        RT4 = compute_target(daily, P, config.delta_period).dropna(subset=[Col.LABEL_KNOWN_AT, Col.PCT_CHANGE])
        store.push(FeatureGroup.RT4, RT4.assign(**{Col.AVG_TARGET_EXPANDING: known_label_mean(RT4, RT4)}))
    else:
        # BIR4 has no realized return yet; its average covers the RT4 labels known at each row's acceptance
        BIR4 = daily.assign(**{Col.AVG_TARGET_EXPANDING: known_label_mean(store.read(FeatureGroup.RT4), daily)})
        store.push(FeatureGroup.BIR4, BIR4)

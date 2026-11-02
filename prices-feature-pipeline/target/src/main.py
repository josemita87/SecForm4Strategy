"""Entry point and core steps: label training transactions (BT4 -> RT4) or prepare inference ones (BI4 -> BIR4)."""

import logging
import time
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore

from src.config import config
from src.constants import MARKET_CLOSE, MARKET_OPEN, MAX_PRICE_WAIT, Col, FeatureGroup, ReferenceData
from src.helpers import acceptance_day, as_of_keys, instant, next_print, normalize_prices
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
    buys and sales alike) in each ownership line (direct, indirect), and stock holdings come
    from each insider's latest filing, since every filing restates them. Transaction codes and insider roles
    become flags; ``filing_lag_days`` runs from the earliest trade to acceptance.

    Args:
        transactions: Transactions prepared by ``as_of_keys``, reported ``price`` 0 meaning none.

    Returns:
        One row per ``ticker`` and ``next_print``, keyed ``<ticker>@<next print>``.
    """
    stock = ~transactions[Col.DERIVATIVE].astype(bool)

    # Per transaction: next print, stock vs derivative shares, reported stock price, code and role flags
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

    # Group by 'ticker' and 'next_print'. Txs between 2 prints taken as a single opportunity trade.
    keys = [Col.TICKER, Col.NEXT_PRINT]
    latest = tx[stock].sort_values([Col.ACCEPTED_AT, Col.DATE, Col.POSITION])
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
                Col.REPORTED_PRICE: (Col.PRICE, 'mean'),
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
        # Traded accounts: each insider's last balance per ownership line, summed into one `remaining_shares`
        .join(
            latest.groupby([*keys, Col.INSIDER_CIK, Col.OWNERSHIP], observed=True, dropna=False)[Col.REMAINING_SHARES]
            .last()
            .groupby(level=keys)
            .sum()
        )
        # Untraded accounts: each insider's latest filed holdings, summed with direct and indirect kept apart
        .join(
            latest.groupby([*keys, Col.INSIDER_CIK], observed=True, dropna=False)[
                [Col.DIRECT_HOLDING, Col.INDIRECT_HOLDING]
            ]
            .last()
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
    # tuple() is needed: GroupBy has a `keys` attribute, so dict() would read it as a mapping
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


def add_value_features(rows: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Value each row's share counts in USD at its ticker's as-traded close on the last day before acceptance.

    Training and inference both run this step, so the model sees the same features in
    both. Every row is valued at that one market price: known at disclosure whatever the
    acceptance time, and in that day's shares, like the filing's counts. Values are
    therefore comparable across transaction types and across later splits. ``tx_value``,
    ``derivative_value`` (notional: underlying shares at the stock's close),
    ``remaining_value``, ``direct_holding`` and ``indirect_holding`` become USD values, and
    ``total_held`` is their sum (traded accounts after the trade plus untraded ones); the
    share counts go. ``reported_price`` is kept as is and compared with the close as
    ``price_to_close`` (missing when no price was reported).

    Args:
        rows: Disclosure rows from ``aggregate_disclosures``, with ``reported_price`` (missing if none) and
            share columns.
        prices: Daily bars prepared by ``normalize_prices``, with ``close_as_traded``.

    Returns:
        The rows with USD value columns and ``price_to_close``.
    """
    # Backward and not exact: the last trading day strictly before the acceptance day (weekends and holidays skipped)
    valued = pd.merge_asof(
        rows.assign(**{Col.ACCEPTED_DAY: acceptance_day(rows[Col.ACCEPTED_AT])}).sort_values(Col.ACCEPTED_DAY),
        prices[[Col.TICKER, Col.DATE, Col.CLOSE_AS_TRADED]].rename(
            columns={Col.DATE: Col.CLOSE_DAY, Col.CLOSE_AS_TRADED: Col.LAST_CLOSE}
        ),
        left_on=Col.ACCEPTED_DAY,
        right_on=Col.CLOSE_DAY,
        by=Col.TICKER,
        direction='backward',
        allow_exact_matches=False,
    )
    # Each USD value column and the share count it values, all multiplied by the row's close at once
    counts = {
        Col.TX_VALUE: Col.SHARES,
        Col.DERIVATIVE_VALUE: Col.DERIVATIVE_SHARES,
        Col.REMAINING_VALUE: Col.REMAINING_SHARES,
        Col.DIRECT_HOLDING: Col.DIRECT_HOLDING,
        Col.INDIRECT_HOLDING: Col.INDIRECT_HOLDING,
    }
    valued[list(counts)] = valued[list(counts.values())].mul(valued[Col.LAST_CLOSE], axis=0).to_numpy()
    valued[Col.TOTAL_HELD] = valued[Col.REMAINING_VALUE] + valued[Col.DIRECT_HOLDING] + valued[Col.INDIRECT_HOLDING]
    valued[Col.PRICE_TO_CLOSE] = valued[Col.REPORTED_PRICE] / valued[Col.LAST_CLOSE]
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


def compute_target(rows: pd.DataFrame, prices: pd.DataFrame, period: int) -> pd.DataFrame:
    """Label each disclosure row with the forward return from the first price a trader could act on.

    Every trading day offers two prices, its 09:30 open and its 16:00 close (US/Eastern).
    A row enters at its ``next_print``, or at the first actual print after it when that
    print did not happen (an exchange holiday, a gap in the ticker's bars). It exits at
    the first close ``period`` days after the entry day;
    ``label_known_at`` (UTC) is that exit close. A row stays unlabelled when its
    exit falls after its ticker's last bar, or when the entry or exit price is more than
    ``MAX_PRICE_WAIT`` away (a gap in the ticker's bars).

    Args:
        rows: Disclosure rows from ``aggregate_disclosures``, with ``next_print``.
        prices: Daily bars prepared by ``normalize_prices``, with adjusted ``open`` and ``close``.
        period: Days between the entry day and the exit.

    Returns:
        The rows with ``pct_change`` and ``label_known_at``.
    """
    assert rows[Col.NEXT_PRINT].notna().all(), 'Next prints contain NaT'

    # One row per tradable price: each day's open and close, stamped at the moment it prints
    events = pd.concat(
        [
            pd.DataFrame(
                {
                    Col.TICKER: prices[Col.TICKER],
                    Col.ENTRY_DAY: prices[Col.DATE],
                    Col.ENTRY_PRICE: prices[col],
                    Col.PRINTS_AT: instant(prices[Col.DATE] + offset),
                }
            )
            for col, offset in ((Col.OPEN, MARKET_OPEN), (Col.CLOSE, MARKET_CLOSE))
        ]
    ).dropna(subset=[Col.ENTRY_PRICE])

    start = pd.merge_asof(
        rows.sort_values(Col.NEXT_PRINT),
        events.sort_values(Col.PRINTS_AT),
        left_on=Col.NEXT_PRINT,
        right_on=Col.PRINTS_AT,
        by=Col.TICKER,
        direction='forward',
        allow_exact_matches=True,
        tolerance=MAX_PRICE_WAIT,
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
        tolerance=MAX_PRICE_WAIT,
    )
    es[Col.PCT_CHANGE] = ((es[Col.EXIT_PRICE] - es[Col.ENTRY_PRICE]) / es[Col.ENTRY_PRICE]).round(5)
    es[Col.LABEL_KNOWN_AT] = instant(es[Col.EXIT_DATE] + MARKET_CLOSE)

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


def known_label_mean(labels: pd.DataFrame, rows: pd.DataFrame) -> pd.Series:
    """Return, for each row, the mean ``pct_change`` of its ticker's labels already known at its acceptance.

    A label counts once its ``label_known_at`` (the exit close) is strictly before the row's
    ``accepted_at``, so no return still unrealised at acceptance leaks in. Training (each RT4
    row against RT4) and inference (each BIR4 row against RT4) use this one rule.

    Args:
        labels: Labelled rows with ``ticker``, ``label_known_at`` and ``pct_change``.
        rows: Rows with ``ticker`` and ``accepted_at``, uniquely indexed.

    Returns:
        The mean per row, indexed like ``rows``; NaN where no label of its ticker was known yet.
    """
    # Each ticker's running mean return, in the order its labels became known
    known = labels.dropna(subset=[Col.PCT_CHANGE]).sort_values(Col.LABEL_KNOWN_AT)
    returns = known.groupby(Col.TICKER, observed=True)[Col.PCT_CHANGE]
    known = known.assign(
        **{
            # Group-wise transforms: one value per label, indexed like `known`, so each lands on its own row
            Col.AVG_TARGET_EXPANDING: returns.cumsum() / (returns.cumcount() + 1),
            # The as-of keys must match the rows' dtypes: labels read from the store may differ
            Col.TICKER: known[Col.TICKER].astype(str),
            Col.LABEL_KNOWN_AT: known[Col.LABEL_KNOWN_AT].dt.as_unit('ns'),
        }
    )
    # Each row takes the latest running mean known strictly before its acceptance (a close publishes just after 16:00)
    ordered = rows.sort_values(Col.ACCEPTED_AT)
    means: pd.Series = (
        pd.merge_asof(
            ordered[[Col.TICKER, Col.ACCEPTED_AT]],
            known[[Col.TICKER, Col.LABEL_KNOWN_AT, Col.AVG_TARGET_EXPANDING]],
            left_on=Col.ACCEPTED_AT,
            right_on=Col.LABEL_KNOWN_AT,
            by=Col.TICKER,
            direction='backward',
            allow_exact_matches=False,
        )[Col.AVG_TARGET_EXPANDING]
        .set_axis(ordered.index)
        .reindex(rows.index)
    )
    return means


def add_company_context(rows: pd.DataFrame, reference: ReferenceData) -> pd.DataFrame:
    """Attach each row's market cap, exchange, SIC code and SIC group.

    Market cap is the latest monthly snapshot strictly before the row's acceptance day
    (its latest filing's), so it is what a trader knew at disclosure; the rest is looked up by issuer CIK.

    Args:
        rows: Disclosure rows from ``aggregate_disclosures``, with ``company_cik``.
        reference: Market-cap snapshots and the company table.

    Returns:
        The rows with ``market_cap``, ``exchange``, ``sic`` and ``sic_group`` columns.
    """
    # Keyed on the acceptance day (midnight), so excluding exact matches skips that day's own snapshot
    enriched = pd.merge_asof(
        rows.assign(**{Col.SNAPSHOT: acceptance_day(rows[Col.ACCEPTED_AT])}).sort_values(Col.SNAPSHOT),
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

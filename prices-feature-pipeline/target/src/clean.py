"""Cleaning and normalization helpers for transaction and price data."""

import logging

import numpy as np
import pandas as pd

from src.constants import Direction

logger = logging.getLogger(__name__)


def get_unique_tickers(df: pd.DataFrame) -> list[str]:
    """Return a list of unique tickers from the given DataFrame.

    Args:
        df: DataFrame containing the transactions with a ``ticker`` column.

    Returns:
        A list of unique tickers.
    """
    return df['ticker'].unique().tolist()


def data_cleaning(data: pd.DataFrame, required: list[str]) -> pd.DataFrame:
    """Normalize missing values, drop duplicates, and drop rows missing any ``required`` column.

    ``'None'`` and ``'---'`` count as missing.

    Args:
        data: DataFrame to be cleaned.
        required: Columns that must be present on every kept row.

    Returns:
        The cleaned DataFrame.
    """
    return data.replace(['None', '---'], np.nan).drop_duplicates().dropna(subset=required)


def normalize_data(
    txs: pd.DataFrame | None = None, prices: pd.DataFrame | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Normalize ticker casing and validate the transaction direction.

    Uppercases and strips ``ticker`` columns in both DataFrames and verifies
    that every ``acquired_disposed`` value is ``'D'``.

    Args:
        txs: Transactions DataFrame containing ``ticker`` and
            ``acquired_disposed``.
        prices: Prices DataFrame containing ``ticker``.

    Returns:
        The updated ``txs`` and ``prices`` DataFrames with consistent
        formatting (empty DataFrames for the ones not given).

    Raises:
        ValueError: If not all ``acquired_disposed`` values are ``'D'``.
    """
    txs = pd.DataFrame() if txs is None else txs
    prices = pd.DataFrame() if prices is None else prices

    if not txs.empty:
        txs['ticker'] = txs['ticker'].str.upper().str.strip()
        txs['acquired_disposed'] = txs['acquired_disposed'].str.upper().str.strip()
        if not (txs['acquired_disposed'] == Direction.DISPOSED).all():
            raise ValueError(f"Not all values in 'acquired_disposed' are '{Direction.DISPOSED.value}'")

    if not prices.empty:
        prices['ticker'] = prices['ticker'].str.upper().str.strip()

    logger.info('Data cleaning and formatting successful.')
    return txs, prices

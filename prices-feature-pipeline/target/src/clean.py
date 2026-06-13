"""Cleaning and normalization helpers for transaction and price data."""

import logging

import numpy as np
import pandas as pd

from src.constants import Direction

logger = logging.getLogger(__name__)


def get_unique_tickers(df: pd.DataFrame) -> list:
    """Return a list of unique tickers from the given DataFrame.

    Args:
        df: DataFrame containing the transactions with a ``ticker`` column.

    Returns:
        A list of unique tickers.
    """
    return df['ticker'].unique().tolist()


def data_cleaning(data: pd.DataFrame) -> pd.DataFrame:
    """Clean the data by normalizing missing values and dropping invalid rows.

    Replaces ``'None'`` and ``'---'`` with NaN, drops duplicate rows, and
    removes rows with NaN in ``pct_change`` and ``avg_target_expanding``.

    Args:
        data: DataFrame to be cleaned.

    Returns:
        The cleaned DataFrame.
    """
    data.replace(['None', '---'], np.nan, inplace=True)
    data.drop_duplicates(inplace=True)
    data.dropna(subset=['pct_change', 'avg_target_expanding'], inplace=True)
    return data


def normalize_data(
    txs: pd.DataFrame = pd.DataFrame(), prices: pd.DataFrame = pd.DataFrame()
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
        formatting.

    Raises:
        ValueError: If not all ``acquired_disposed`` values are ``'D'``.
    """
    try:
        if not txs.empty:
            # Convert 'ticker' columns to uppercase and strip whitespace
            txs['ticker'] = txs['ticker'].str.upper().str.strip()
            # Convert 'acquired_disposed' column to uppercase, strip whitespace, and fill missing values
            txs['acquired_disposed'] = txs['acquired_disposed'].str.upper().str.strip()

            # Verify that all values in 'acquired_disposed' are 'D'
            if not (txs['acquired_disposed'] == Direction.DISPOSED).all():
                raise ValueError(f"Not all values in 'acquired_disposed' are '{Direction.DISPOSED.value}'")

        if not prices.empty:
            prices['ticker'] = prices['ticker'].str.upper().str.strip()

        logger.info('Data cleaning and formatting successful.')
        return txs, prices

    except Exception as e:
        logger.error(f'Error in assert_and_format_data: {e}')
        raise

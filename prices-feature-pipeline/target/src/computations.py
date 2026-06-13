"""Target computations: aggregation, forward returns, and expanding averages."""

# import dask.dataframe as dd
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def data_aggregation(transactions: pd.DataFrame, agg_dict: dict) -> pd.DataFrame:
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
    """Compute forward-return targets and percentage changes for transactions.

    Aligns transaction dates with prices via backward and forward
    ``merge_asof`` joins, computes the percentage change over ``period`` days,
    and converts share counts into USD values.

    Args:
        transactions: DataFrame containing transaction data.
        prices: DataFrame containing price data.
        period: Look-ahead window in days used to compute the target.

    Returns:
        A DataFrame with computed ``pct_change`` and value columns.
    """
    # Homogenize to naive UTC datetimes so both frames share one datetime type.
    # tz_localize(None) already yields datetime64[ns], so no astype is needed.
    transactions['date'] = pd.to_datetime(transactions['date']).dt.tz_convert('UTC').dt.tz_localize(None)
    prices['date'] = pd.to_datetime(prices['date']).dt.tz_convert('UTC').dt.tz_localize(None)

    # Sort both dataframes by date (merge_asof requires the key sorted)
    transactions.sort_values(['date'], inplace=True)
    prices.sort_values(['date'], inplace=True)

    # NaT dates survive sorting and would silently misalign the as-of joins
    assert transactions['date'].notna().all(), 'Transaction dates contain NaT'
    assert prices['date'].notna().all(), 'Price dates contain NaT'

    # Backward merge_asof to get the start price
    start_state = pd.merge_asof(
        left=transactions,
        right=prices,
        on='date',
        by='ticker',
        direction='backward'
    ).rename(columns={'close': 'start_price'})

    # Create future_date column and filter out dates in the future
    latest_date = prices['date'].max()
    start_state['future_date'] = start_state['date'] + pd.to_timedelta(period, unit='D')
    start_state = start_state[start_state['future_date'] <= latest_date]

    # Perform forward merge_asof to get the end price
    end_state = pd.merge_asof(
        left=start_state,
        right=prices,
        left_on='future_date',
        right_on='date',
        by='ticker',
        direction='forward'
    ).rename(columns={'close': 'end_price', 'date_x': 'date'})

    # Drop the future_date column
    es = end_state.drop(columns=['future_date'])

    # Calculate % change of end_state (es)
    es['pct_change'] = (
        (es['end_price'] - es['start_price']) / es['start_price']
    ).round(5)

    # Collapse price columns into one: this way we increase likelihood of having a price to work with
    es['price'] = np.where(es['price'] == 0, es['start_price'], es['price'])

    # Convert the shares-related counts to USD value
    es[['tx_value', 'remaining_value', 'direct_holding', 'indirect_holding']] = end_state[
        ['shares', 'remaining_shares', 'direct_holding', 'indirect_holding']
    ].mul(es['price'], axis=0)

    # Drop unnecessary columns
    es = es.drop(
        columns=['start_price', 'end_price', 'remaining_shares', 'shares', 'date_y']
    )

    logger.debug(f'Target computation completed. Shape: {es.shape}')
    return es


def compute_RT4(df: pd.DataFrame, timedelta: int) -> pd.DataFrame:
    """Compute the expanding average target over a specified time period.

    Args:
        df: DataFrame containing transaction data with ``ticker``,
            ``timestamp`` and ``pct_change`` columns.
        timedelta: Look-back window in days for the expanding average.

    Returns:
        The DataFrame with an added ``avg_target_expanding`` column.
    """
    # Sort the dataframe by ticker and date
    df.sort_values(['ticker', 'date'], inplace=True)

    # Constant period (days) covered (in milliseconds)
    delta_ms = timedelta * 24 * 60 * 60 * 1000

    # Prepare an empty column to store the average pct_change
    df['avg_target_expanding'] = np.nan

    # Extract timestamps and pct_changes for easier access
    timestamps = df['timestamp'].astype(np.int64).values
    pct_changes = df['pct_change'].astype(np.float64).values
    tickers = df['ticker'].astype(str).values

    # Numba JIT-compiled function for computing expanding averages
    # @jit(nopython=True, parallel=True)
    def expanding_average(tickers, timestamps, pct_changes, delta_ms):
        """Compute the expanding average of percentage changes per ticker.

        The algorithm works the following way:
            1. For each ticker, get the timestamp of the current row.
            2. Find the offset, which is the row that is delta_ms milliseconds
               before the row timestamp.
            3. Compute the average of all percentage changes belonging to the
               same ticker (after the recorded offset and before the timestamp
               found in step 2).

        Step 3 ensures the average target resulting from this function is not
        biased by the percentage changes of the same ticker that happened
        after the current row.

        Args:
            tickers: Sorted array of ticker symbols.
            timestamps: Sorted array of timestamps in milliseconds.
            pct_changes: Sorted array of percentage changes.
            delta_ms: Look-back window for the expanding average in milliseconds.

        Returns:
            An array of computed expanding averages.
        """
        avg_pct_changes = np.empty_like(pct_changes)

        # Initialize offset tracker
        offset = 0
        last_ticker = tickers[0]

        for i in range(len(tickers)):
            current_ticker = tickers[i]
            current_timestamp = timestamps[i]
            cutoff_time = current_timestamp - delta_ms

            # Reset offset if we move to a new ticker
            if current_ticker != last_ticker:
                offset = i

            # Perform binary search to find the cutoff index for this ticker
            idx = np.searchsorted(timestamps[offset:], cutoff_time, side='right') + offset

            if idx > offset:
                # Get all pct_changes before the cutoff time for this ticker
                valid_pct_changes = pct_changes[offset:idx]
                avg_pct_changes[i] = np.nanmean(valid_pct_changes)
            else:
                avg_pct_changes[i] = np.nan

            # Update the last_ticker for the next iteration
            last_ticker = current_ticker

        return avg_pct_changes

    # Call the Numba-accelerated function
    df['avg_target_expanding'] = expanding_average(tickers, timestamps, pct_changes, delta_ms)

    return df


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

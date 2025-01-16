import dask.dataframe as dd
import pandas as pd
from loguru import logger
import numpy as np
from numba import jit



       
def data_aggregation(transactions: pd.DataFrame, agg_dict: dict) -> pd.DataFrame:
    # Filter out columns that are not present in the transactions DataFrame
    valid_agg_dict = {k: v for k, v in agg_dict.items() if k in transactions.columns}
    return transactions.groupby(['ticker', 'date'], as_index=False).agg(valid_agg_dict)


def compute_target(
    transactions:pd.DataFrame, 
    prices:pd.DataFrame, 
    period: int) -> pd.DataFrame:
       
    # 0. Homogenize the date format
    transactions['date'] = pd.to_datetime(
        transactions['date'])
    
    prices['date'] = pd.to_datetime(
        prices['date'] 
    )

    # 1. Sort both dataframes by date
    transactions.sort_values(['date'], inplace=True)
    prices.sort_values(['date'], inplace=True)

    # Check if the data is sorted correctly
    assert transactions['date'].is_monotonic_increasing, "Txs dates not sorted"
    assert prices['date'].is_monotonic_increasing, "Prices dates not sorted"
    
    # 2. Backward merge_asof to get the start price
    start_state = pd.merge_asof(
        transactions,
        prices,
        on='date',
        by='ticker',
        direction='backward'
    ).rename(columns={'close': 'start_price'})

    # 3. Create future_date column and filter out dates in the future
    latest_date = prices['date'].max()
    start_state['future_date'] = start_state['date'] + pd.to_timedelta(period, unit='D')
    start_state = start_state[start_state['future_date'] <= latest_date]

    #Homogenize the date format
    transactions['date'] = transactions['date'].astype('datetime64[ns, UTC]')
    prices['date'] = prices['date'].astype('datetime64[ns, UTC]')


    # 4. Perform forward merge_asof to get the end price
    end_state = pd.merge_asof(
        start_state,
        prices,
        left_on='future_date',
        right_on='date',
        by='ticker',
        direction='forward'
    ).rename(columns={
        'close': 'end_price', 
        'date_x': 'date'})

    # 5. Drop the future_date column
    end_state = end_state.drop(columns=['future_date'])

    # 6. Calculate % change
    end_state['pct_change'] = (
        ((end_state['end_price'] - end_state['start_price']) / 
                    end_state['start_price']).round(5)
    )

    # 7. Collapse price columns into one: this way we increase likelihood of having a price to work with
    end_state['price'] = np.where(
        end_state['price']==0, 
        end_state['start_price'], 
        end_state['price']
    )

    # 8. Convert the shares-related counts to USD value 
    end_state[['tx_value', 'remaining_value', 'direct_holding', 'indirect_holding']] = \
        end_state[
            ['shares', 'remaining_shares', 'direct_holding', 'indirect_holding']
            ].mul(end_state['price'], axis=0)


    # 9. Drop unnecessary columns 
    end_state = end_state.drop(
        columns=['start_price', 'end_price', 'remaining_shares', 'shares', 'date_y']
    )

    logger.debug(f"Target computation completed. Shape: {end_state.shape}")
    return end_state

def compute_avg_target_price(
        df: pd.DataFrame, timedelta:int) -> pd.DataFrame:
        
    # Sort the dataframe by ticker and date
    df.sort_values(['ticker', 'date'], inplace=True)
    # Constant period (days) covered (in milliseconds)
    delta_ms = timedelta * 24 * 60 * 60 * 1000 

    # Prepare an empty column to store the average pct_change
    df['avg_target_expanding'] = np.nan

    # Extract timestamps and pct_changes for easier access
    timestamps = df['timestamp'].values
    pct_changes = df['pct_change'].values
    tickers = df['ticker'].values
    
  
    # Numba JIT-compiled function for computing expanding averages
    #@jit(nopython=True, parallel=True)
    def expanding_average(tickers, timestamps, pct_changes, delta_ms):
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
            
            # Perform binary search to find the cutoff index for this ticker using np.searchsorted
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












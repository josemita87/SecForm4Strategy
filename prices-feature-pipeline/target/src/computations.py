import dask.dataframe as dd
import pandas as pd
from loguru import logger
import numpy as np
from numba import njit



       
def data_aggregation(transactions: pd.DataFrame, agg_dict:dict) -> pd.DataFrame:
    return transactions.groupby(
        ['ticker', 'date'], as_index=False).agg(agg_dict)


def compute_target(
    transactions:pd.DataFrame, 
    prices:pd.DataFrame, 
    period: int) -> pd.DataFrame:
       
    # 0. Homogenize the date format
    transactions['date'] = pd.to_datetime(
        transactions['date'], 
        unit = 'ms', utc = True, 
        errors='coerce').dt.normalize()
    
    prices['date'] = pd.to_datetime(
        prices['date'], 
        unit = 'ms', 
        utc = True, 
        errors='coerce').dt.normalize()

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

    return end_state

def compute_avg_target_price(
        df: pd.DataFrame, expanding: bool, window_days:int = False) -> pd.DataFrame:
        
    @njit
    def expanding_mean_numba(arr):
        n = len(arr)
        result = np.empty(n)
        cumulative_sum = 0.0
        
        for i in range(n):
            cumulative_sum += arr[i]
            result[i] = cumulative_sum / (i + 1)
        
        return result
    
    @njit
    def rolling_mean_numba(arr, window):
        result = np.empty(len(arr))
        result[:] = np.nan  
        
        for i in range(len(arr)):
            if i + 1 >= window:
                try:
                    result[i] = arr[i + 1 - window:i + 1].mean()
                except:
                    result[i] = np.nan
                    
            else:
                result[i] = np.nan  
        return result

    if expanding:
        # Apply expanding mean per ticker
        df['avg_target_expanding'] = df.groupby('ticker')['pct_change'].transform(
            lambda x: expanding_mean_numba(x.to_numpy())
        )
    
    # Apply rolling mean per ticker
    #df['avg_target_rolling'] = df.groupby('ticker')['pct_change'].transform(
    #    lambda x: rolling_mean_numba(x.to_numpy(), window=window_days)
    #)

    return df












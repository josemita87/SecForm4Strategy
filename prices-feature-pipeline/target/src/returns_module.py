import dask.dataframe as dd
import pandas as pd
from loguru import logger

class Mapper:
    def __init__(self, prices: pd.DataFrame):
        self.prices = prices
       
    def data_aggregation(
            self, 
            transactions: pd.DataFrame,
            agg_dict:dict
        ) -> pd.DataFrame:

        return transactions.groupby(
            ['ticker', 'date'], as_index=False).agg(agg_dict)


    def compute_returns(
        self, 
        transactions: pd.DataFrame, 
        period: int) -> pd.DataFrame:
       
        logger.debug(self.prices)
        
        # 0. Homogenize the date format
        transactions['date'] = pd.to_datetime(transactions['date'], unit = 'ms', utc = True, errors='coerce').dt.normalize()
        self.prices['date'] = pd.to_datetime(self.prices['date'], unit = 'ms', utc = True, errors='coerce').dt.normalize()

        # 1. Sort both dataframes by date
        transactions.sort_values(['date'], inplace=True)
        self.prices.sort_values(['date'], inplace=True)

        # Check if the data is sorted correctly
        assert transactions['date'].is_monotonic_increasing, "Transactions dates are not sorted"
        assert self.prices['date'].is_monotonic_increasing, "Prices dates are not sorted"
        
        # 2. Backward merge_asof to get the start price
        start_state = pd.merge_asof(
            transactions,
            self.prices,
            on='date',
            by='ticker',
            direction='backward'
        ).rename(columns={'close': 'start_price'})

        # 3. Create future_date column and filter out dates in the future
        latest_date = self.prices['date'].max()
        start_state['future_date'] = start_state['date'] + pd.to_timedelta(period, unit='D')
        start_state = start_state[start_state['future_date'] <= latest_date]

        #Homogenize the date format
        transactions['date'] = transactions['date'].astype('datetime64[ns, UTC]')
        self.prices['date'] = self.prices['date'].astype('datetime64[ns, UTC]')


        # 4. Perform forward merge_asof to get the end price
        end_state = pd.merge_asof(
            start_state,
            self.prices,
            left_on='future_date',
            right_on='date',
            by='ticker',
            direction='forward'
        ).rename(columns={'close': 'end_price'})

        # 5. Drop the future_date column
        end_state = end_state.drop(columns=['future_date'])

        # 6. Calculate % change
        end_state = end_state.assign(
            pct_change=((end_state['end_price'] - end_state['start_price']) / 
                        end_state['start_price']).round(5)
        )

        # 7. Convert the shares-related counts to value in USD 
        end_state[['value', 'remaining_value', 'direct_holding', 'indirect_holding']] = \
            end_state[['shares', 'remaining_shares', 'direct_holding', 'indirect_holding']].mul(end_state['start_price'], axis=0)

        # 8. Drop unnecessary columns 
        end_state = end_state.drop(
            columns=['end_price', 'remaining_value']
        )
        #rename date
        
        end_state.rename(columns={'date': 'transaction_date'}, inplace = True)
        return end_state






















        for tx in transactions.to_dict(orient='records'):
            processed_tx = self.compute_returns(tx, ticker, period)
            processed_transactions.append(processed_tx)

        # Convert the processed transactions to a DataFrame
        processed_transactions = pd.DataFrame(processed_transactions)
        
        # Remove the ticker's data (using Dask) to enhance performance
        self.prices = self.prices[self.prices['ticker'] != ticker]

        return processed_transactions

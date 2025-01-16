from src.feature_store import Connection, assert_and_format_data, data_cleaning
from src.config import config
import pandas as pd

from src import computations
from loguru import logger


if __name__ == "__main__":
   
    # Initialize connection to Hopsworks
    feature_store = Connection()

    # Get transactions in the feature store
    #txs: pd.DataFrame = feature_store.fetch_4f_transactions(
     #   key=config.filter_key, value=config.acquired_disposed
    #)
    #txs.to_csv('/app/src/txsv2.csv', index=False)   
    #txs['ticker'] = txs['ticker'].astype(str).str.upper()           
    txs = pd.read_csv('/app/src/txsv2.csv')
    
    # Aggregate data
    aggregated_df = computations.data_aggregation(txs, config.agg_dict)
    
    # Get the unique tickers to process
    tickers_to_process: list = txs['ticker'].unique().tolist()

    # Get the latest price data for the tickers to process  
    prices = feature_store.fetch_price_data(tickers_to_process)
    prices.to_csv('/app/src/pricesv2.csv', index = False) 
    prices = pd.read_csv('/app/src/pricesv2.csv')

    # Ensure the data is in the correct format
    #txs, prices = assert_and_format_data(txs, prices)

    # Get targets & price-related features
    target_df = computations.compute_target(
        aggregated_df, 
        prices, 
        config.delta_period
    )
    
    # Compute the average target price
    final_df = computations.compute_avg_target_price(
        df=target_df, 
        timedelta=config.delta_period
    )
    
    # Clean data & reduce memory space
    final_df = data_cleaning(final_df)
    
    final_df.to_csv('/app/src/final_df.csv', index = False)
    breakpoint()
    # Push data to fs
    #if not final_df.empty:
        #feature_store.push_returns_data(final_df)


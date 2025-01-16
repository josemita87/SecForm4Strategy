import yfinance as yf
import pandas as pd
from datetime import datetime
from loguru import logger
from src.config import config
from src.feature_store import Connection, reduce_mem_storage
import time

# Initialize connection to Hopsworks
feature_store = Connection()

# Fetch the latest data from Yahoo Finance
def fetch_data_from_yahoo(prices: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Fetches the latest data from Yahoo Finance for the given tickers
    
    Args: 
        prices (pd.DataFrame): Current prices data for the buffer of tickers
        tickers (list[str]): List of tickers to fetch data for
    Returns:
        pd.DataFrame: Updated prices data with the new data only
        """
    
    # Create a copy of the current prices data to append the new data
    all_prices = pd.DataFrame()

    for ticker in tickers:

        # Get the latest date for each ticker in the current prices data (if any)
        offset:datetime = prices.loc[
            prices[prices['ticker'] == ticker]['date'].idxmax()
        ] if not prices.empty else None

        #If not latest date, set offset to None
        offset = None if pd.isnull(offset) else offset

        if offset:
            new_data = yf.download(
                ticker, 
                start=offset + pd.Timedelta(days=1)
            )['Close']

        else:
            new_data = yf.download(ticker)['Close']

        try:
            # Convert the Series into a DataFrame with custom column names
            new_data.reset_index(inplace=True) 
            new_data['ticker'] = ticker
            new_data.columns = ['date', 'close', 'ticker'] 
            
            # Reduce memory usage of the dataframe before appending it
            new_data = reduce_mem_storage(new_data)

            if not all_prices.empty:
                all_prices = pd.concat([all_prices, new_data], axis=0)
                
            else:
                all_prices = new_data
        
        except Exception as e:
            logger.error(f"Failed to fetch data for {ticker}: {e}")
            continue

    all_prices.sort_values(by=['ticker', 'date'], inplace=True)

    # Set the ticker as category to reduce space usage
    all_prices['ticker'] = all_prices['ticker'].astype('category')

    return all_prices





if __name__ == '__main__':
    
    #Fetch the tickers from the feature store and convert them from an array to a list
    tickers = feature_store.fetch_ticker_data().tolist()

    #Process data in ticker batches
    for i in range(0, len(tickers), config.buffer_size):

        logger.debug(f"Processing tickers {tickers[i:i+config.buffer_size]}...")
        processing_tickers = tickers[i:i+config.buffer_size]

        #Extract the current data from the feature store for processing tickers
        current_data = feature_store.fetch_price_data(
            processing_tickers=processing_tickers
        )
        
        #Fetch the latest data from Yahoo Finance for the given processing tickers
        new_data = fetch_data_from_yahoo(current_data, processing_tickers)
        logger.debug(new_data)

        #Push the new data to the feature store (append mode)
        feature_store.push_data(new_data)

    logger.debug('Finished processing all tickers')
    feature_store.materialization_jobs()
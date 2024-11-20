import yfinance as yf
import pandas as pd
from typing import Tuple
from datetime import datetime
from loguru import logger
from src.config import config
from src.feature_store import Connection, reduce_mem_storage
import time

# Initialize connection to Hopsworks
feature_store = Connection(
    project_name=config.project_name,
    api_key=config.api_key,
)


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

   
        # Convert the Series into a DataFrame with custom column names
        new_data = new_data.reset_index() 
        new_data['ticker'] = ticker
        new_data.columns = ['date', 'close', 'ticker'] 

        logger.debug(f"Fetching data for {ticker} from {offset if offset else 'beggining'}...")
        
        # Reduce memory usage of the dataframe before appending it
        new_data = reduce_mem_storage(new_data)

        if not all_prices.empty:
            all_prices = pd.concat([all_prices, new_data], axis=0)
            
        else:
            all_prices = new_data

    all_prices.sort_values(by=['ticker', 'date'], inplace=True)

    # Set the ticker as category to reduce space usage
    all_prices['ticker'] = all_prices['ticker'].astype('category')

    return all_prices





if __name__ == '__main__':
    
    #Fetch the tickers from the feature store and convert them from an array to a list
    tickers = feature_store.fetch_ticker_data(
         feature_group_name=config.feature_group_form4,
         feature_group_version=config.feature_group_version,
    ).tolist()
    
    tickers = [
    'AAPL', 'GOOGL', 'AMZN', 'MSFT', 'TSLA', 'META', 'NVDA', 'NFLX', 'BABA', 'INTC',
    'AMD', 'BA', 'WMT', 'DIS', 'GS', 'IBM', 'V', 'MA', 'JPM', 'CVX', 'XOM', 
    'PYPL', 'AMD', 'INTU', 'NVDA', 'SNAP', 'SHOP', 'GOOG', 'AMZN', 'PEP', 'KO', 
    'MCD', 'UNH', 'PFE', 'BA', 'T', 'LMT', 'RTX', 'JNJ', 'MS', 'AMT', 'GS', 
    'CVX', 'XOM', 'NEE', 'MMM', 'CAT', 'DE', 'CSCO', 'INTC', 'GM', 'F', 'CHWY',
    'FTNT', 'BIDU', 'EA', 'SPY', 'QQQ', 'IWM', 'VTI', 'DIA', 'XLY', 'XLF', 'XLI',
    'XLC', 'XLE', 'XLB', 'XLV', 'XBI', 'SMH', 'ARKK', 'TSM', 'SHOP', 'SQ', 'ZM',
    'BIDU', 'PDD', 'SOFI', 'PINS', 'TWTR', 'ZM', 'RBLX', 'TSLA', 'LULU', 'ROKU',
    'ATVI', 'FSLY', 'PLTR', 'SPCE', 'DKNG', 'RIVN', 'UPST', 'PTON', 'LCID', 'SNOW',
    'PLUG', 'ENPH', 'AMD', 'NKE', 'TRIP', 'BYND', 'SQ', 'AMD', 'ULTA', 'VEEV', 'SHOP'
    ]

    #Process data in ticker batches
    for i in range(0, len(tickers), config.buffer_size):

        logger.debug(f"Processing tickers {tickers}...")
        processing_tickers = tickers[i:i+config.buffer_size]

        #Extract the current data from the feature store for processing tickers
        current_data = feature_store.fetch_price_data(

            processing_tickers=processing_tickers,
            feature_group_name=config.feature_group_prices,
            feature_group_version=config.feature_group_version,
            feature_view_name=config.feature_view_name,
            feature_view_version=config.feature_view_version,
            inference_blueprint=config.inference_blueprint
        )

        #Fetch the latest data from Yahoo Finance for the given processing tickers
        new_data = fetch_data_from_yahoo(current_data, processing_tickers)
        
        #Push the new data to the feature store (append mode)
        feature_store.push_data(
            new_data, 
            config.feature_group_prices, 
            config.feature_group_version
        )


import hopsworks
import pandas as pd
from loguru import logger
import time
from hsfs.feature import Feature
import numpy as np
from config import config

class Connection:  
    def __init__(self)-> None:

        #Initialize connection to Hopsworks
        self.project = hopsworks.login(
            project=config.project_name,
            api_key_value=config.api_key,
        )
        self.fs = self.project.get_feature_store()

        # Initialize feature group connections
        self.fg_form4 = self.fs.get_or_create_feature_group(
            name=config.feature_group_form4_basic,  
            version=config.feature_group_version,
            primary_key=['key'],
            event_time='date'
        )
        self.fg_prices = self.fs.get_or_create_feature_group(
            name=config.feature_group_prices,
            online_enabled=False,
            stream=False,
            version=config.feature_group_version,
            primary_key=['ticker', 'date'],
            event_time='date'
        )
        self.fg_returns = self.fs.get_or_create_feature_group(
            name=config.feature_group_target,
            online_enabled=False,
            stream=False,
            version=config.feature_group_version,
            primary_key=['key'],
            event_time='date'
        )
    
    def fetch_4f_transactions(self, key, value) -> pd.DataFrame:
        
        txs = self.fg_form4

        # Filter transactions based on key and value
        if key and value:
            txs = self.fg_form4.filter(
                self.fg_form4[key] == value
            )
            
        return txs.read(read_options={"use_hive": True})
        
    
    def fetch_price_data(self, tickers:list[str]) -> pd.DataFrame:
        
        # Fetch price data for the given tickers
        try:
            fg_prices = self.fg_prices.filter(
                self.fg_prices['ticker'].isin(tickers)
            )
        except Exception as e:

            logger.error(f"Failed to fetch price \
                         data:{e}. Tickers: {tickers}")
            return pd.DataFrame()
        
        # Read and return data
        return fg_prices.read(read_options={"use_hive": True})


    def push_returns_data(self,data: pd.DataFrame):
        
        if not data.empty: 
            self.fg_returns.insert(
                features = data, 
                write_options = {
                    'start_offline_materialization':True,
                    'mode':'append' 
                }
            )


#Auxiliary function
def data_cleaning(data: pd.DataFrame) -> pd.DataFrame:
    """
    Clean the data by replacing 'None' and '---' with NaN, 
    dropping duplicates and removing rows with NaN values.
    """

    # Replace 'None' and '---' with NaN
    data.replace(['None', '---'], np.nan, inplace=True)
    
    # Drop duplicate rows
    data.drop_duplicates(inplace=True)
    
    # Drop rows with NaN values (optional)
    data.dropna(subset=['pct_change', 'avg_target_expanding'], inplace=True)
    

    return data


def assert_and_format_data(txs: pd.DataFrame, prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    # Capitalize the 'ticker' column for both txs and prices DataFrames
    txs['ticker'] = txs['ticker'].str.upper()
    prices['ticker'] = prices['ticker'].str.upper()
    
    # Assert all tickers are in capital letters for both txs and prices DataFrames
    #assert txs['ticker'].str.isupper().all(), "Not all tickers in txs are in uppercase"
    #assert prices['ticker'].str.isupper().all(), "Not all tickers in prices are in uppercase"
    
    # Ensure 'acquired_disposed' column is 'D'
    txs['acquired_disposed'] = txs['acquired_disposed'].str.upper().str.strip()
    assert (txs['acquired_disposed'] == 'D').all(), "Not all values in 'acquired_disposed' are 'D'"
    
    return txs, prices
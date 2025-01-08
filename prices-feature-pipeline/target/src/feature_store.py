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
    data.dropna(inplace=True)

    return data


#Auxiliary function
def validate_and_reduce_mem_storage(data: pd.DataFrame) -> pd.DataFrame:
    """
    Reduce the memory usage of the DataFrame by downcasting the numeric columns.
    Invalid rows for each conversion will be dropped.
    """


    if 'link' in data.columns:
        data['link'] = data['link'].astype('str')

    for col in ['company_cik', 'ticker', 'insider_cik', 'insider_name', 
                'owner_code', 'exchange', 'acquired_disposed', 'coding', 'sic']:
        if col in data.columns:
            data[col] = data[col].astype('category')

    # Convert booleans to actual boolean dtype
    for col in ['rule105b1', 'derivative', 'equity_swap', 'ownership']:
        if col in data.columns:
            data[col] = data[col].astype('bool')

    # Convert numeric columns to more memory-efficient types
    numeric_columns = {
        'tx_value': 'int32',
        'remaining_value': 'int32',
        'direct_holding': 'int32',
        'indirect_holding': 'int32',
        'market_cap': 'int64',
        'timestamp': 'int64',
        'pct_change': 'float32',  
        'avg_target_expanding': 'float32',
        'avg_target_rolling': 'float32',
        'price': 'float32'
        
    }
    for col, dtype in numeric_columns.items():
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors='coerce').astype(dtype)
            data = data.dropna(subset=[col])  # Drop rows where conversion failed

    # Convert 'date' to datetime
    data['date'] = pd.to_datetime(data['date'], errors='coerce')
    data = data.dropna(subset=['date'])  # Drop rows where 'date' conversion failed

    return data
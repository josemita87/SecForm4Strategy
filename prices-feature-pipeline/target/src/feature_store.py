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

        # Materialization counters 
        self.materialization_counter_offset = 0
        self.materialization_counter_returns = 0

        # Initialize feature group connections
        self.fg_form4 = self.fs.get_or_create_feature_group(
            name=config.feature_group_form4_basic,
            online_enabled=False,
            stream=False,   
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
        self.fg_delta = self.fs.get_or_create_feature_group(
            name=config.feature_group_offset_target,
            online_enabled=False,
            stream=False,
            version=config.feature_group_version,
            primary_key=['ticker'],
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
    
        #Delta mappers
        try:
            self.delta_df = self.fg_delta.read()
            self.delta_dict = dict(
            zip(
                self.delta_df['ticker'], 
                self.delta_df['date']
            )
        )
        except:
            self.delta_df = pd.DataFrame()
            self.delta_dict = {}
        

    
    def fetch_4f_transactions(self, key, value) -> pd.DataFrame:
        
        txs = self.fg_form4

        # Filter transactions based on key and value
        if key and value:
            txs = self.fg_form4.filter(
                self.fg_form4[key] == value
            )
            
        data = txs.read(
            read_options={"use_hive": True}
            )
        
        return data
        
    
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
        data=fg_prices.read(
            read_options={"use_hive": True}
        )

        return data
        

    def push_returns_data(self,data: pd.DataFrame):
        
        if not data.empty: 
            self.returns_job = self.fg_returns.insert(
                features = data, 
                write_options = {
                    'start_offline_materialization':False,
                    'mode':'append' 
                }
            )

            self.returns_job.run()
            self.materialization_returns = 0

    def fetch_offset(self, ticker: str) -> int:
        
        try:
            #Fetch existing records 
            offset = self.delta_dict[ticker]
            logger.debug(f"Offset for {ticker}: {offset}")

        except:
            offset = 0

        #Convert to datetime in ms
        offset = pd.to_datetime(offset, unit='ms', utc=True) 
     
    
        return offset


    def update_delta_table_batch(self, batch:dict) -> None:

        #Fetch existing records as 
        try:
            delta_table = self.fg_delta.read(
                read_options={"use_hive": True}
            )
            #Convert to dictionary
            delta_table = dict(
                zip(
                    delta_table['ticker'], 
                    delta_table['date']
                )
            )

        #In case it is first time processing the ticker
        except:
            delta_table = {}

        #Update the delta table with latest offsets priority (inplace)
        delta_table.update(batch)
        df = pd.DataFrame.from_dict(delta_table, orient='index').reset_index()
        df.columns = ['ticker', 'date']
        
        self.offset_job = self.fg_delta.insert(
            features = df, 
            write_options = {
                'start_offline_materialization':False,
                'mode':'overwrite' 
            }
        )
        if self.offset_job and \
            self.materialization_counter_offset >= \
                config.materialization_batch_size:
            self.offset_job.run()
            self.materialization_counter_offset = 0

    def last_materialization(self):
        self.offset_job.run()
        self.returns_job.run()
        

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
        'value': 'int32',
        'remaining_value': 'int32',
        'direct_holding': 'int32',
        'indirect_holding': 'int32',
        'market_cap': 'int64',
        'timestamp': 'int64',
        'pct_change': 'float32',
        'start_price': 'float32',   
    }
    for col, dtype in numeric_columns.items():
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors='coerce').astype(dtype)
            data = data.dropna(subset=[col])  # Drop rows where conversion failed

    # Convert 'date' to datetime
    data['date'] = pd.to_datetime(data['date'], errors='coerce')
    data = data.dropna(subset=['date'])  # Drop rows where 'date' conversion failed

    return data
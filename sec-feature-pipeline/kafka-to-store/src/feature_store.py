
import hopsworks
import pandas as pd
from typing import Tuple, Any
from loguru import logger
import time
from datetime import datetime


class Connection:  

    def __init__(self, project_name, api_key):
        
        # Initialize Feature Store Object
        project = hopsworks.login(
            project=project_name,
            api_key_value=api_key,
        )
        self.fs = project.get_feature_store()
    
    
    def _validate_dataframe_types(self, data: pd.DataFrame, expected_schema: dict) -> pd.DataFrame:
        """
        Validate and coerce the data types of the DataFrame to match the expected schema.
        """
        # In next weeks , will develop type validation and coercion in this method
        return data


    def push_data(
            self,
            data: pd.DataFrame,
            feature_group_name: str,
            feature_group_version: int,
            schema: dict
        ) -> None:


        fg = self.fs.get_or_create_feature_group(
            name=feature_group_name,
            version=feature_group_version,
            primary_key=['key'],
            event_time='date'
        )

        
        # Validate and coerce the DataFrame
        data = self._validate_dataframe_types(data, schema)
        
        # Insert the data into the feature group
        try:
            fg.insert(data)
            logger.debug(f"Data pushed to feature store successfully.")

        except Exception as e:
            logger.error(f"Failed to push data to feature store: {e}")
            logger.debug(f"Data: {data}")
            
        



def data_cleaning(data: list[dict]) -> pd.DataFrame:
    """
    Clean the data by replacing 'None' and '---' with NaN, 
    dropping duplicates and removing rows with NaN values.
    """

    # Convert the list of dicts to a DataFrame
    data = pd.DataFrame(data)

    # Drop duplicate rows
    data = data.drop_duplicates()

    # Drop rows with NaN values in any column
    data = data.dropna()

    return data


#Auxiliary function
def validate_and_reduce_mem_storage(data: pd.DataFrame) -> pd.DataFrame:
    """
    Reduce the memory usage of the DataFrame by downcasting the numeric columns.
    Invalid rows for each conversion will be dropped.
    """
    data['link'] = data['link'].astype('str')

    # Convert columns to categorical where appropriate
    for col in ['company_cik', 'ticker', 'insider_cik', 'insider_name', 
                'owner_code', 'exchange', 'acquired_disposed', 'coding', 'sic']:
        data[col] = data[col].astype('category')

    # Convert booleans to actual boolean dtype
    for col in ['rule105b1', 'derivative', 'equity_swap', 'ownership']:
        data[col] = data[col].astype('bool')

    # Convert numeric columns to more memory-efficient types
    numeric_columns = {
        'shares': 'int32',
        'price': 'float32',
        'remaining_shares': 'int32',
        'direct_holding': 'int32',
        'indirect_holding': 'int32',
        'market_cap': 'int64',
        'timestamp': 'int64',
    }
    for col, dtype in numeric_columns.items():
        data[col] = pd.to_numeric(data[col], errors='coerce').astype(dtype)
        data = data.dropna(subset=[col])  # Drop rows where conversion failed

    # Convert 'date' to datetime
    data['date'] = pd.to_datetime(data['date'], errors='coerce')
    data = data.dropna(subset=['date'])  # Drop rows where 'date' conversion failed

    return data



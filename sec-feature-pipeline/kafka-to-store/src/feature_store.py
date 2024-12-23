
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
            raise
        



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
def reduce_mem_storage(data: pd.DataFrame) -> pd.DataFrame:
    """
    Reduce the memory usage of the DataFrame by downcasting the numeric columns.
    """
  
    # Convert columns to categorical where appropriate
    data['company_cik'] = data['company_cik'].astype('category')
    data['ticker'] = data['ticker'].astype('category')
    data['insider_cik'] = data['insider_cik'].astype('category')
    data['insider_name'] = data['insider_name'].astype('category')
    data['exchange'] = data['exchange'].astype('category')
    data['owner_code'] = data['owner_code'].astype('category')
    data['acquired_disposed'] = data['acquired_disposed'].astype('category')
    data['ownership'] = data['ownership'].astype('category')
    data['coding'] = data['coding'].astype('category')
    data['sic'] = data['sic'].astype('category')

    # Convert booleans to actual boolean dtype
    data['rule105b1'] = data['rule105b1'].astype('bool')
    data['derivative'] = data['derivative'].astype('bool')
    data['equity_swap'] = data['equity_swap'].astype('bool')

    # Convert numeric columns to more memory-efficient types
    data['shares'] = pd.to_numeric(data['shares'], errors='coerce').astype('int32')
    data['price'] = pd.to_numeric(data['price'], errors='coerce').astype('float32')
    data['remaining_shares'] = pd.to_numeric(data['remaining_shares'], errors='coerce').astype('int32')
    data['direct_holding'] = pd.to_numeric(data['direct_holding'], errors='coerce').astype('int32')
    data['indirect_holding'] = pd.to_numeric(data['indirect_holding'], errors='coerce').astype('int32')
    
    data['market_cap'] = pd.to_numeric(data['market_cap'], errors='coerce').astype('int64')
    data['timestamp'] = pd.to_numeric(data['timestamp'], errors='coerce').astype('int64')

    # Convert 'date' to datetime
    data['date'] = pd.to_datetime(data['date'])

    return data


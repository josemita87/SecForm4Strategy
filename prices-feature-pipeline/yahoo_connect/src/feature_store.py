import hopsworks
import pandas as pd
from loguru import logger
import time


class Connection:  
    def __init__(
            self, 
            project_name, 
            api_key
        )-> None:

        #Initialize connection to Hopsworks
        self.project_name = project_name
        self.api_key = api_key
        self.project = hopsworks.login(
            project=self.project_name,
            api_key_value=self.api_key,
        )
        self.fs = self.project.get_feature_store()
    
    

    def fetch_ticker_data(
        self,
        feature_group_name: hopsworks,
        feature_group_version: int,
        ) -> pd.DataFrame:
        
        fg = self.fs.get_or_create_feature_group(
            name=feature_group_name,
            version=feature_group_version,
            primary_key='key',
            event_time='date'
        )
       
        data: pd.DataFrame = fg.read()
        tickers = data['ticker'].unique()
        
        return tickers
    

    def fetch_price_data(
        self,
        processing_tickers: list,
        feature_group_name: hopsworks,
        feature_group_version: int,
        inference_blueprint: dict, 
        feature_view_name: str = None,
        feature_view_version: int = 1,
        ) -> pd.DataFrame:
        
        fg = self.fs.get_or_create_feature_group(
            name=feature_group_name,
            version=feature_group_version,
            primary_key=['ticker', 'date'],
            event_time='date'
        )

        #Insert dummy data infer schema
        blueprint = reduce_mem_storage(pd.DataFrame(inference_blueprint))
        fg.insert(blueprint)

        try:
            prices_fv = self.fs.get_or_create_feature_view(
                name=feature_view_name,
                version=feature_view_version,
                query=fg.filter(
                    fg.ticker.isin(processing_tickers)
                )
            )

            return prices_fv.get_batch_data()
        
        except:  
            logger.debug('Could not find data in feature store')
            return pd.DataFrame()
        

    def push_data(
            self,
            data: pd.DataFrame,
            feature_group_name: str,
            feature_group_version: int

        ) -> None:
        
        fg = self.fs.get_or_create_feature_group(
            name=feature_group_name,
            version=feature_group_version,
            primary_key=['ticker', 'date'],
            event_time='date'
        )
        
        if not data.empty: 
        
            fg.insert(
                data, write_options = {
                    'start_offline_materialization':False,
                    'mode':'append' 
                }
            )

            logger.debug(f"Data pushed to feature store")
            time.sleep(30)


# Auxiliary function
def reduce_mem_storage(df:pd.DataFrame) -> pd.DataFrame:
    """Reduces the memory usage of the given DataFrame"""
    # Drop index column if it exists
    if df.columns.str.contains('index').any():
        df.drop(columns=['index'], inplace=True)
    # Convert the date column to datetime 64ns
    df['date'] = pd.to_datetime(df['date'])
    # Convert the close column to float32
    df['close'] = df['close'].astype('float32')
    # Convert the ticker column to category
    df['ticker'] = df['ticker'].astype('category')

    return df
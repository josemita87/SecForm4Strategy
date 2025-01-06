from pydantic import Field
from pydantic_settings import BaseSettings


from dotenv import load_dotenv  

load_dotenv(".env")
load_dotenv(".credentials.env")

class Config(BaseSettings):

    project_name: str = Field(..., json_schema_extra={'env': 'PROJECT_NAME'})
    api_key: str = Field(..., json_schema_extra={'env': 'API_KEY'})

    # Feature group names
    feature_group_prices: str = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_PRICES'})
    feature_group_form4_basic: str = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_FORM4_BASIC'})
    feature_group_target: str = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_TARGET'})
    feature_group_offset_target: str = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_OFFSET_TARGET'})
    feature_group_version: int = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_VERSION'})
    materialization_batch_size: int = Field(..., json_schema_extra={'env': 'MATERIALIZATION_BATCH_SIZE'})

    #Other parameters
    event_time: str = Field(..., json_schema_extra={'env': 'EVENT_TIME'})
    npartitions: int = Field(1, json_schema_extra={'env': 'NPARTITIONS'})
    delta_period: int = Field(30, json_schema_extra={'env': 'DELTA_PERIOD'})
    offset_buffer_size: int = Field(10, json_schema_extra={'env': 'DELTA_BUFFER_SIZE'})
    acquired_disposed: str = Field('A', json_schema_extra={'env': 'ACQUIRED_DISPOSED'})

    #Aggregation
    agg_dict:dict = Field({
        # 'first' method aggregation
        'company_cik': 'first',
        'key': 'first',
        'timestamp': 'first',
        #'insider_cik': 'first',
        #'insider_name': 'first',
        #'acquired_disposed': 'first',
        'date': 'first',
        'market_cap': 'first',
        
        # 'sum' method aggregation
        'shares': 'sum',
        'remaining_shares': 'sum',
        'direct_holding': 'sum',
        'indirect_holding': 'sum',
        
        # 'any' method aggregation (for boolean columns)
        'equity_swap': 'any',
        'rule105b1': 'any',
        
        # 'mode' method aggregation
        'derivative': lambda x: x.mode()[0],
        'owner_code': lambda x: x.mode()[0],
        'ownership': lambda x: x.mode()[0],
        'coding': lambda x: x.mode()[0],
        
        # 'mean' method aggregation
        'price': 'mean'
    })
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

# Instantiate the configuration
config = Config()

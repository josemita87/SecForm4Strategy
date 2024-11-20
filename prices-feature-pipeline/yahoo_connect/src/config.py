from pydantic import Field
from pydantic_settings import BaseSettings
from dotenv import load_dotenv
import pandas as pd

# Load the main .env file and then the credentials.env file
load_dotenv(".env")
load_dotenv(".credentials.env")

class Config(BaseSettings):
    project_name: str = Field(..., json_schema_extra={'env': 'PROJECT_NAME'})
    feature_group_prices: str = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_NAME'})
    feature_group_form4: str = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_FORM4'})
    feature_group_version: int = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_VERSION'})
    feature_view_version: int = Field(..., json_schema_extra={'env': 'FEATURE_VIEW_VERSION'})
    feature_view_name: str = Field(..., json_schema_extra={'env': 'FEATURE_VIEW_NAME'})
    event_time: str = Field(..., json_schema_extra={'env': 'EVENT_TIME'})
    api_key: str = Field(..., json_schema_extra={'env': 'API_KEY'})
    inference_blueprint: dict = Field({
        "date": pd.to_datetime(["1999-01-01"]),
        "close": [-1.0],
        "ticker": ["INFERENCE_VALUE"],
        
        
    })
    
    buffer_size: int = Field(100, json_schema_extra={'env': 'BUFFER_SIZE'})

    class Config:
        # Specify only the main .env file here for Pydantic’s internal use
        env_file = ".env"
        env_file_encoding = "utf-8"

config = Config()

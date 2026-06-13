"""Application configuration loaded from environment variables and .env files."""

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

load_dotenv('.env')
load_dotenv('.credentials.env')


class Config(BaseSettings):
    """Settings for Alpaca, Hopsworks, feature selection, trading and logging."""

    # Alpaca API credentials
    alpaca_api_key: str = Field(..., json_schema_extra={'env': 'ALPACA_API_KEY'})
    alpaca_api_secret: str = Field(..., json_schema_extra={'env': 'ALPACA_API_SECRET'})
    alpaca_base_url: str = Field(..., json_schema_extra={'env': 'ALPACA_BASE_URL'})

    # Hopsworks / CSV Settings
    hopsworks_connect: bool = Field(..., json_schema_extra={'env': 'HOPSWORKS_CONNECT'})
    project_name: str = Field(..., json_schema_extra={'env': 'PROJECT_NAME'})
    feature_group_version: int = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_VERSION'})
    hopsworks_api_key: str = Field(..., json_schema_extra={'env': 'HOPSWORKS_API_KEY'})
    csv_path: str = Field(..., json_schema_extra={'env': 'CSV_PATH'})

    # Feature Selection
    columns_to_drop: list = Field(default=['key', 'timestamp', 'coding'])
    identification_features: list = Field(default=['ticker', 'date', 'company_cik', 'price'])
    target_feature: str = Field(..., json_schema_extra={'env': 'TARGET_FEATURE'})
    # Trading settings
    qty: str = Field(..., json_schema_extra={'env': 'QTY'})
    side: str = Field('sell', json_schema_extra={'env': 'SIDE'})
    order_type: str = Field('market', json_schema_extra={'env': 'ORDER_TYPE'})
    time_in_force: str = Field('gtc', json_schema_extra={'env': 'TIME_IN_FORCE'})
    prediction_threshold: float = Field(..., json_schema_extra={'env': 'PREDICTION_THRESHOLD'})
    last_n_days: int = Field(..., json_schema_extra={'env': 'LAST_N_DAYS'})

    # Logging
    log_path: str = Field(..., json_schema_extra={'env': 'LOG_PATH'})

    # Models
    model_path: str = Field(..., json_schema_extra={'env': 'MODEL_PATH'})


config = Config()

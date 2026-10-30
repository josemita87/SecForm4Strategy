"""Application configuration loaded from environment variables and .env files."""

from pathlib import Path

from dotenv import load_dotenv
from paths import CSV_PATH, LOG_PATH, MODEL_PATH
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings
from secform4strategy_clients.ports import OrderType, Side, TimeInForce

load_dotenv('.env')
load_dotenv('.credentials.env')


class Config(BaseSettings):
    """Settings for Alpaca, the feature store, trading and logging."""

    # Alpaca API credentials
    alpaca_api_key: SecretStr = Field(..., json_schema_extra={'env': 'ALPACA_API_KEY'})
    alpaca_api_secret: SecretStr = Field(..., json_schema_extra={'env': 'ALPACA_API_SECRET'})
    alpaca_base_url: str = Field(..., json_schema_extra={'env': 'ALPACA_BASE_URL'})

    # Iceberg feature store; the catalog itself is configured by PYICEBERG_CATALOG__DEFAULT__* (docs/configuration.md)
    feature_store_namespace: str = Field('secform4strategy', json_schema_extra={'env': 'FEATURE_STORE_NAMESPACE'})
    feature_group_version: int = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_VERSION'})
    # Read inference data from the feature store, or from csv_path when off
    use_feature_store: bool = Field(True, json_schema_extra={'env': 'USE_FEATURE_STORE'})
    csv_path: Path = Field(default=CSV_PATH, json_schema_extra={'env': 'CSV_PATH'})

    # Trading settings
    qty: float = Field(..., gt=0, json_schema_extra={'env': 'QTY'})
    side: Side = Field('sell', json_schema_extra={'env': 'SIDE'})
    order_type: OrderType = Field('market', json_schema_extra={'env': 'ORDER_TYPE'})
    time_in_force: TimeInForce = Field('gtc', json_schema_extra={'env': 'TIME_IN_FORCE'})
    prediction_threshold: float = Field(..., json_schema_extra={'env': 'PREDICTION_THRESHOLD'})

    # Logging
    log_path: Path = Field(default=LOG_PATH, json_schema_extra={'env': 'LOG_PATH'})

    # Models
    model_path: Path = Field(default=MODEL_PATH, json_schema_extra={'env': 'MODEL_PATH'})


config = Config()

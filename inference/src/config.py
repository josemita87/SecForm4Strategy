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
    alpaca_api_key: SecretStr
    alpaca_api_secret: SecretStr
    alpaca_base_url: str

    # Iceberg feature store; the catalog itself is configured by PYICEBERG_CATALOG__DEFAULT__* (docs/configuration.md)
    feature_store_namespace: str = 'secform4strategy'
    feature_group_version: int
    # Read inference data from the feature store, or from csv_path when off
    use_feature_store: bool = True
    csv_path: Path = CSV_PATH

    # Trading settings
    qty: float = Field(gt=0)
    side: Side = 'sell'
    order_type: OrderType = 'market'
    time_in_force: TimeInForce = 'gtc'
    prediction_threshold: float

    # Logging
    log_path: Path = LOG_PATH

    # Models
    model_path: Path = MODEL_PATH


config = Config()

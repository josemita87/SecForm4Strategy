"""Application configuration loaded from environment variables and .env files."""

from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from paths import CSV_PATH, LOG_PATH, MODEL_PATH
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import SettingsConfigDict
from secform4strategy_clients.ports import OrderType, Side, TimeInForce
from secform4strategy_clients.ssm_settings import SsmSettings

load_dotenv('.env')
load_dotenv('.credentials.env')


class LocalModels(BaseModel):
    """A model file on local disk, its metadata beside it."""

    backend: Literal['local']
    path: Path = MODEL_PATH


class S3Models(BaseModel):
    """Versioned models in an S3 bucket, on AWS."""

    backend: Literal['s3']
    region: str
    bucket: str
    prefix: str = 'models'


class Config(SsmSettings):
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
    # Live path: rows whose latest filing was accepted longer ago are not traded
    max_signal_age_minutes: int = Field(60, gt=0)

    # Logging
    log_path: Path = LOG_PATH

    # Model store: MODEL_STORE__BACKEND picks it, MODEL_STORE__<FIELD> sets its fields
    model_store: LocalModels | S3Models = Field(LocalModels(backend='local'), discriminator='backend')

    # `model_store` is a setting, not a pydantic internal
    model_config = SettingsConfigDict(env_nested_delimiter='__', protected_namespaces=('settings_',))


config = Config()

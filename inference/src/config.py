"""Application configuration loaded from environment variables and .env files."""

from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from paths import CSV_PATH, LOG_PATH, MODEL_PATH
from pydantic import Field
from pydantic_settings import BaseSettings

load_dotenv('.env')
load_dotenv('.credentials.env')


@lru_cache
def _specs() -> dict:
    """Load the static feature-selection spec (column lists) from specs.yaml."""
    return yaml.safe_load((Path(__file__).parent / 'specs.yaml').read_text())


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
    csv_path: str = Field(default=CSV_PATH.str, json_schema_extra={'env': 'CSV_PATH'})

    # Feature Selection (column lists loaded from specs.yaml)
    columns_to_drop: list = Field(default_factory=lambda: _specs()['columns_to_drop'])
    identification_features: list = Field(default_factory=lambda: _specs()['identification_features'])
    target_feature: str = Field(..., json_schema_extra={'env': 'TARGET_FEATURE'})
    # Trading settings
    qty: str = Field(..., json_schema_extra={'env': 'QTY'})
    side: str = Field('sell', json_schema_extra={'env': 'SIDE'})
    order_type: str = Field('market', json_schema_extra={'env': 'ORDER_TYPE'})
    time_in_force: str = Field('gtc', json_schema_extra={'env': 'TIME_IN_FORCE'})
    prediction_threshold: float = Field(..., json_schema_extra={'env': 'PREDICTION_THRESHOLD'})
    last_n_days: int = Field(..., json_schema_extra={'env': 'LAST_N_DAYS'})

    # Logging
    log_path: str = Field(default=LOG_PATH.str, json_schema_extra={'env': 'LOG_PATH'})

    # Models
    model_path: str = Field(default=MODEL_PATH.str, json_schema_extra={'env': 'MODEL_PATH'})


config = Config()

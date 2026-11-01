"""Pydantic settings for the target microservice."""

from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.constants import Direction
from src.paths import COMPANIES_PATH, MARKET_CAPS_PATH, SPEC_PATH


class Config(BaseSettings):
    """Configuration settings for the target microservice."""

    # Iceberg feature store; the catalog itself is configured by PYICEBERG_CATALOG__DEFAULT__* (docs/configuration.md)
    feature_store_namespace: str = 'secform4strategy'
    feature_group_version: int

    # Investment settings
    delta_period: int
    filter_key: str
    acquired_disposed: Direction

    # Reference data (docs/configuration.md)
    market_caps_path: Path = MARKET_CAPS_PATH
    companies_path: Path = COMPANIES_PATH

    # Days of preceding filings counted as a ticker's cluster of insiders (spec.yaml)
    cluster_window_days: int = Field(
        default_factory=lambda: yaml.safe_load(SPEC_PATH.read_text())['cluster_window_days']
    )

    # System settings
    system_training: bool = False
    system_inference: bool = False
    delay: int = 0

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8')


# Instantiate the configuration
config = Config()

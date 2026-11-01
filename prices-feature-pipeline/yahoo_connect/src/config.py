"""Pydantic settings for the Yahoo Connect microservice."""

from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """Configuration settings for the Yahoo Connect microservice."""

    # Iceberg feature store; the catalog itself is configured by PYICEBERG_CATALOG__DEFAULT__* (docs/configuration.md)
    feature_store_namespace: str = 'secform4strategy'
    feature_group_version: int

    # System Settings
    buffer_size: int
    delay: int = 0
    system_training: bool = False
    system_inference: bool = False
    filter_key: str
    acquired_disposed: str


# Instantiate the configuration
config = Config()

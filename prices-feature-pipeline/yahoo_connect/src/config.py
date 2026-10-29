"""Pydantic settings for the Yahoo Connect microservice."""

from pydantic import Field
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """Configuration settings for the Yahoo Connect microservice."""

    # Iceberg feature store; the catalog itself is configured by PYICEBERG_CATALOG__DEFAULT__* (docs/configuration.md)
    feature_store_namespace: str = Field('secform4strategy', json_schema_extra={'env': 'FEATURE_STORE_NAMESPACE'})
    feature_group_version: int = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_VERSION'})

    # System Settings
    buffer_size: int = Field(..., json_schema_extra={'env': 'BUFFER_SIZE'})
    delay: int = Field(0, json_schema_extra={'env': 'DELAY'})
    system_training: bool = Field(False, json_schema_extra={'env': 'SYSTEM_TRAINING'})
    system_inference: bool = Field(False, json_schema_extra={'env': 'SYSTEM_INFERENCE'})
    filter_key: str = Field(..., json_schema_extra={'env': 'FILTER_KEY'})
    acquired_disposed: str = Field(..., json_schema_extra={'env': 'ACQUIRED_DISPOSED'})


# Instantiate the configuration
config = Config()

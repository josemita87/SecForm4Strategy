"""Pydantic settings for the Yahoo Connect microservice."""

from pydantic import Field
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """Configuration settings for the Yahoo Connect microservice."""

    # Hopsworks Settings
    project_name: str = Field(..., json_schema_extra={'env': 'PROJECT_NAME'})
    feature_group_version: int = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_VERSION'})
    feature_view_version: int = Field(..., json_schema_extra={'env': 'FEATURE_VIEW_VERSION'})
    feature_view_name: str = Field(..., json_schema_extra={'env': 'FEATURE_VIEW_NAME'})
    event_time: str = Field(..., json_schema_extra={'env': 'EVENT_TIME'})
    hopsworks_api_key: str = Field(..., json_schema_extra={'env': 'HOPSWORKS_API_KEY'})

    # System Settings
    buffer_size: int = Field(..., json_schema_extra={'env': 'BUFFER_SIZE'})
    delay: int = Field(0, json_schema_extra={'env': 'DELAY'})
    system_training: bool = Field(False, json_schema_extra={'env': 'SYSTEM_TRAINING'})
    system_inference: bool = Field(False, json_schema_extra={'env': 'SYSTEM_INFERENCE'})
    filter_key: str = Field(..., json_schema_extra={'env': 'FILTER_KEY'})
    acquired_disposed: str = Field(..., json_schema_extra={'env': 'ACQUIRED_DISPOSED'})


# Instantiate the configuration
config = Config()

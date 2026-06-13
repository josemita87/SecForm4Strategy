"""Environment-driven configuration settings for the kafka-to-store service."""

from pydantic import Field
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """Application settings loaded from environment variables for kafka-to-store."""

    # Kafka settings
    kafka_broker_address: str = Field(..., env='KAFKA_BROKER_ADDRESS')
    kafka_input_topic: str = Field(..., env='KAFKA_INPUT_TOPIC')
    buffer_size: int = Field(1000, env='BUFFER_SIZE')
    poll_timeout: int = Field(10, env='POLL_TIMEOUT')
    consumer_group: str = Field(..., env='CONSUMER_GROUP')
    auto_offset_reset: str = Field(..., env='AUTO_OFFSET_RESET')

    # Hopsworks settings
    project_name: str = Field(..., env='PROJECT_NAME')
    hopsworks_api_key: str = Field(..., env='HOPSWORKS_API_KEY')
    feature_group_version: int = Field(1, env='FEATURE_GROUP_VERSION')

    # System Settings
    system_training: bool = Field(False, json_schema_extra={'env': 'SYSTEM_TRAINING'})
    system_inference: bool = Field(False, json_schema_extra={'env': 'SYSTEM_INFERENCE'})
    delay: int = Field(0, env='DELAY')


config = Config()

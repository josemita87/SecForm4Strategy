"""Environment-driven configuration settings for the kafka-to-store service."""

from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """Application settings loaded from environment variables for kafka-to-store."""

    # Kafka settings
    kafka_broker_address: str
    kafka_input_topic: str
    buffer_size: int = 1000
    poll_timeout: int = 10
    consumer_group: str
    auto_offset_reset: str

    # Iceberg feature store; the catalog itself is configured by PYICEBERG_CATALOG__DEFAULT__* (docs/configuration.md)
    feature_store_namespace: str = 'secform4strategy'
    feature_group_version: int

    # System Settings
    system_training: bool = False
    system_inference: bool = False
    delay: int = 0


config = Config()

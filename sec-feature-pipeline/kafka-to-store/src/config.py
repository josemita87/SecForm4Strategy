"""Environment-driven configuration settings for the kafka-to-store service."""

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Kafka(BaseModel):
    """Redpanda or Kafka, for local runs."""

    backend: Literal['kafka']
    broker_address: str
    consumer_group: str
    auto_offset_reset: str


class Sqs(BaseModel):
    """Amazon SQS, on AWS."""

    backend: Literal['sqs']
    region: str


class Config(BaseSettings):
    """Application settings loaded from environment variables for kafka-to-store."""

    # Message queue: QUEUE__BACKEND picks it, QUEUE__<FIELD> sets its fields
    queue: Kafka | Sqs = Field(discriminator='backend')
    input_queue: str
    buffer_size: int = 1000
    poll_timeout: int = 10
    # Live path (src/handler.py): where the tickers of each stored batch are sent, to start their scoring
    output_queue: str | None = None

    # Iceberg feature store; the catalog itself is configured by PYICEBERG_CATALOG__DEFAULT__* (docs/configuration.md)
    feature_store_namespace: str = 'secform4strategy'
    feature_group_version: int

    # System Settings
    system_training: bool = False
    system_inference: bool = False
    delay: int = 0

    model_config = SettingsConfigDict(env_nested_delimiter='__')


config = Config()

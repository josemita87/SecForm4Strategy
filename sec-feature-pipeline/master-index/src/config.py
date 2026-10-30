"""Environment-driven configuration for the master-index microservice."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """Settings for the master-index service loaded from environment variables."""

    # Kafka Settings
    kafka_broker_address: str = Field(..., json_schema_extra={'env': 'KAFKA_BROKER_ADDRESS'})
    kafka_output_topic: str = Field(..., json_schema_extra={'env': 'KAFKA_OUTPUT_TOPIC'})

    # SEC fair access requires a real name and contact email, e.g. 'Jane Doe jane@example.com'
    sec_user_agent: str = Field(..., pattern=r'^\S.*\s\S+@\S+\.\S+$', json_schema_extra={'env': 'SEC_USER_AGENT'})

    # System Settings
    buffer_size: int = Field(..., json_schema_extra={'env': 'BUFFER_SIZE'})
    mode: Literal['historical', 'live', 'last_quarter'] = Field(..., json_schema_extra={'env': 'MODE'})

    # Training Settings
    years: int = Field(5, json_schema_extra={'env': 'YEARS'})

    # Inference Settings
    num_pages: int = Field(1, json_schema_extra={'env': 'NUM_PAGES'})
    live_iterations: int = Field(1, json_schema_extra={'env': 'LIVE_ITERATIONS'})
    secs_between_iterations: int = Field(10, json_schema_extra={'env': 'SECS_BETWEEN_ITERATIONS'})


config = Config()

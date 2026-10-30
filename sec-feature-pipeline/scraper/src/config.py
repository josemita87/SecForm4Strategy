"""Environment-driven configuration for the scraper microservice."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """Settings for the scraper service loaded from environment variables."""

    # Kafka settings
    kafka_broker_address: str = Field(..., json_schema_extra={'env': 'KAFKA_BROKER_ADDRESS'})
    kafka_input_topic: str = Field(..., json_schema_extra={'env': 'KAFKA_INPUT_TOPIC'})
    kafka_output_topic: str = Field(..., json_schema_extra={'env': 'KAFKA_OUTPUT_TOPIC'})
    auto_offset_reset: str = Field(..., json_schema_extra={'env': 'AUTO_OFFSET_RESET'})
    consumer_group: str = Field(..., json_schema_extra={'env': 'CONSUMER_GROUP'})

    # SEC fair access requires a real name and contact email, e.g. 'Jane Doe jane@example.com'
    sec_user_agent: str = Field(..., pattern=r'^\S.*\s\S+@\S+\.\S+$', json_schema_extra={'env': 'SEC_USER_AGENT'})

    # System Settings
    buffer_size: int = Field(..., json_schema_extra={'env': 'BUFFER_SIZE'})
    sleep_time: float = Field(..., json_schema_extra={'env': 'SLEEP_TIME'})
    poll_timeout: int = Field(..., json_schema_extra={'env': 'POLL_TIMEOUT'})
    delay: int = Field(..., json_schema_extra={'env': 'DELAY'})
    mode: Literal['historical', 'live', 'last_quarter'] = Field(..., json_schema_extra={'env': 'MODE'})

    # Training Settings
    test_size: int = Field(400, json_schema_extra={'env': 'TEST_SIZE'})
    test_trial: bool = Field(False, json_schema_extra={'env': 'TEST_TRIAL'})

    # Inference Settings
    days_back: int = Field(1, json_schema_extra={'env': 'DAYS_BACK'})


config = Config()

"""Environment-driven configuration for the scraper microservice."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """Settings for the scraper service loaded from environment variables."""

    # Kafka settings
    kafka_broker_address: str
    kafka_input_topic: str
    kafka_output_topic: str
    auto_offset_reset: str
    consumer_group: str

    # SEC fair access requires a real name and contact email, e.g. 'Jane Doe jane@example.com'
    sec_user_agent: str = Field(pattern=r'^\S.*\s\S+@\S+\.\S+$')
    # Requests per second; SEC allows 10 per IP, shared by master-index and the scraper
    sec_max_rate: float = Field(8, gt=0, le=10)

    # System Settings
    buffer_size: int
    poll_timeout: int
    delay: int
    mode: Literal['historical', 'live', 'last_quarter', 'quarter']

    # Inference Settings
    days_back: int = 1


config = Config()

"""Environment-driven configuration for the master-index microservice."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """Settings for the master-index service loaded from environment variables."""

    # Kafka Settings
    kafka_broker_address: str
    kafka_output_topic: str

    # SEC fair access requires a real name and contact email, e.g. 'Jane Doe jane@example.com'
    sec_user_agent: str = Field(pattern=r'^\S.*\s\S+@\S+\.\S+$')
    # Requests per second; SEC allows 10 per IP, shared by master-index and the scraper
    sec_max_rate: float = Field(8, gt=0, le=10)

    # System Settings
    buffer_size: int
    mode: Literal['historical', 'live', 'last_quarter']

    # Training Settings
    years: int = 5

    # Inference Settings
    num_pages: int = 1
    live_iterations: int = 1
    secs_between_iterations: int = 10


config = Config()

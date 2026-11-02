"""Environment-driven configuration for the master-index microservice."""

from typing import Literal

from pydantic import Field, model_validator
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
    mode: Literal['historical', 'live', 'last_quarter', 'quarter']

    # Training Settings: the last `years` years (historical), or one quarter (quarter)
    years: int = 5
    year: int | None = None
    quarter: int | None = Field(None, ge=1, le=4)
    # Publish each index in random (seeded) order, so a scrape stopped early is a random sample
    shuffle: bool = False

    # Inference Settings
    num_pages: int = 1
    live_iterations: int = 1
    secs_between_iterations: int = 10

    @model_validator(mode='after')
    def _quarter_is_named(self) -> Config:
        """Require ``year`` and ``quarter`` in quarter mode."""
        if self.mode == 'quarter' and not (self.year and self.quarter):
            raise ValueError('MODE=quarter needs YEAR and QUARTER')
        return self


config = Config()

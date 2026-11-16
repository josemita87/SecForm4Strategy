"""Environment-driven configuration for the scraper microservice."""

from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field, computed_field
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
    """Settings for the scraper service loaded from environment variables."""

    # Message queue: QUEUE__BACKEND picks it, QUEUE__<FIELD> sets its fields
    queue: Kafka | Sqs = Field(discriminator='backend')
    input_queue: str
    output_queue: str

    # SEC fair access requires a real name and contact email, e.g. 'Jane Doe jane@example.com'
    sec_user_agent: str = Field(pattern=r'^\S.*\s\S+@\S+\.\S+$')
    # Requests per second; SEC allows 10 per IP, shared by master-index and the scraper
    sec_max_rate: float = Field(8, gt=0, le=10)

    # System Settings
    buffer_size: int
    poll_timeout: int
    delay: int
    # Seconds to wait after a batch stopped on a transient failure, so a struggling SEC is not hammered
    retry_pause: float = Field(60, ge=0)
    # Stop at the first receive that waits POLL_TIMEOUT for nothing, as a batch task must; otherwise consume forever
    exit_when_empty: bool = False
    mode: Literal['historical', 'live', 'last_quarter', 'quarter']

    # Inference Settings
    days_back: int = Field(1, gt=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_age(self) -> timedelta | None:
        """Return the oldest filing age parsed: ``days_back`` days in live mode, unbounded otherwise."""
        return timedelta(days=self.days_back) if self.mode == 'live' else None

    model_config = SettingsConfigDict(env_nested_delimiter='__')


config = Config()

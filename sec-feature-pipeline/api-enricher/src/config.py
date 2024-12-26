from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import List, Literal, Optional


class Config(BaseSettings):
    
    # Kafka settings
    kafka_broker_address: str = Field(..., env='KAFKA_BROKER_ADDRESS')
    kafka_input_topic: str = Field(..., env='KAFKA_INPUT_TOPIC') 
    kafka_output_topic: str = Field(..., env='KAFKA_OUTPUT_TOPIC')
    buffer_size: int = Field(..., env='BUFFER_SIZE')
    consumer_group: str = Field(..., env='CONSUMER_GROUP')
    auto_offset_reset: str = Field(..., env='AUTO_OFFSET_RESET')
    poll_timeout: int = Field(..., env='POLL_TIMEOUT')
    processing_guarantee: str = Field(..., env='PROCESSING_GUARANTEE')
    # Api  Paths
    mcaps_path: str = Field(..., env='MCAPS_PATH')
    mapper_path: str = Field(..., env='MAPPER_PATH')

    #Failed paths
    failed_mcaps_path: str = Field(..., env='FAILED_MCAPS_PATH')
    failed_date_path: str = Field(..., env='FAILED_DATE_PATH')
    failed_sic_path: str = Field(..., env='FAILED_SIC_PATH')
    failed_exchange_path: str = Field(..., env='FAILED_EXCHANGE_PATH')
    
    #Docker Compose settings
    delay: int = Field(..., env='DELAY')
config = Config()

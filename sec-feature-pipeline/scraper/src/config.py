from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import List, Literal, Optional


class Config(BaseSettings):
    


    kafka_broker_address: str = Field(..., env='KAFKA_BROKER_ADDRESS')
    kafka_input_topic: str = Field(..., env='KAFKA_INPUT_TOPIC') 
    kafka_output_topic: str = Field(..., env='KAFKA_OUTPUT_TOPIC')
    auto_offset_reset: str = Field(..., env='AUTO_OFFSET_RESET')
    consumer_group: str = Field(..., env='CONSUMER_GROUP')


    form_type: str = Field(..., env='FORM_TYPE')
    timedelta: int = Field(..., env='TIMEDELTA')
    test_size: int = Field(..., env='TEST_SIZE')
    buffer_size: int = Field(..., env='BUFFER_SIZE')
    sleep_time: float = Field(..., env='SLEEP_TIME')
    poll_timeout:int = Field(..., env='POLL_TIMEOUT')
    # Dev settings
    
    """kafka_broker_address: str = 'localhost:19092'
    kafka_input_topic: str = 'master-form-4'
    kafka_output_topic: str = 'form_4_dictionary'
    timedelta: int = 5
    years: Optional[int] = 5 
    form_type: Optional[str] = '4'
    """
    
    
config = Config()






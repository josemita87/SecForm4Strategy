from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import List, Literal, Optional


class Config(BaseSettings):
    
    kafka_broker_address: str = Field(..., env='KAFKA_BROKER_ADDRESS') 
    kafka_output_topic: str = Field(..., env='KAFKA_OUTPUT_TOPIC')
    years: int = Field(None, env='LAST_N_DAYS') 
    form_type: str = Field(None, env='FORM_TYPE') 
    batch_size: int = Field(1000, env='BATCH_SIZE')
    mode: Literal['historical', 'latest'] = Field('historical', env='MODE')
    @field_validator('form_type')
    def validate_form_type(cls, v):
        if v not in ['4', '13F-HR']:
            raise ValueError('form_type must be either 4 or 13F-HR')
        return v
    
config = Config()


# Dev settings
kafka_broker_address: str = 'localhost:19092'
kafka_output_topic: str = 'form_4_dictionary'
years: Optional[int] = 5 
form_type: Optional[str] = '4'
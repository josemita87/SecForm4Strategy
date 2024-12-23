from config import config
import time
import pandas as pd
from src import feature_store, kafka_topic
from loguru import logger 

# Connect to the Kafka topic
topic = kafka_topic.Connection(
    broker_address=config.kafka_broker_address,
    input_topic_name=config.kafka_input_topic,
)

# Connect to the feature store
fs = feature_store.Connection(
    project_name=config.project_name,
    api_key=config.api_key,
)


if __name__ == "__main__":
    is_finished = False

    while not is_finished:
        is_finished, data = topic.consume_data(buffer_size=config.buffer_size)

        if data:
            
            data:pd.DataFrame = feature_store.data_cleaning(data)
            data:pd.DataFrame = feature_store.reduce_mem_storage(data)
            logger.debug(data)       
            fs.push_data(
                data, 
                feature_group_name=config.feature_group_name,
                feature_group_version=config.feature_group_version, 
                schema = config.expected_schema
            )

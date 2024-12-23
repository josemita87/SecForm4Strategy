from quixstreams import Application
from config import config
from typing import List, Dict, Generator
from api_handler import ApiHandler
from loguru import logger
import json
from datetime import datetime
import xxhash

app = Application(
    broker_address=config.kafka_broker_address,
    consumer_group='form-4-enriched',
    auto_offset_reset='earliest',
)

input_topic = app.topic(
    name=config.kafka_input_topic,
    value_deserializer='json',
    key_deserializer='string'
)

output_topic = app.topic(
    name=config.kafka_output_topic,
    value_serializer='json',
    key_serializer='string'
)

def consume_data() -> Generator[Dict, None, None]:
    buffer = []

    with app.get_consumer() as consumer:
        consumer.subscribe(topics=[input_topic.name])

        while True:
            message = consumer.poll(4)

            if message is None:
                if buffer:
                    yield from buffer
                    buffer.clear()  
                
                break

            buffer.append(json.loads(message.value().decode('utf-8')))
            
            if len(buffer) >= config.buffer_size:
                yield from buffer
                buffer.clear()  

   

def produce_data(enriched_transactions: Generator[Dict, None, None]) -> None:
    
    with app.get_producer() as producer:    
        for tx in enriched_transactions:
            
            key = xxhash.xxh64(
                tx['link'] + tx['remaining_shares'] +
                ('1' if tx['derivative'] else '0')
            ).hexdigest()

            message = output_topic.serialize(
                key=key,
                value=tx
            )

            producer.produce(
                topic=output_topic.name,
                key=message.key,
                value=message.value,
                timestamp=tx['timestamp']
            )
    

if __name__ == '__main__':
    # Create an enriched generator from the consumed data
    enriched_generator = ApiHandler(
        consume_data(),
        config.mcaps_path,
        config.mapper_path,
        config.failed_mcaps_path,
        config.failed_date_path,
        config.failed_sic_path,
        config.failed_exchange_path,
        config.buffer_size
    ).get_enriched_data()

    # Pass the enriched generator directly to produce_data
    produce_data(enriched_generator)

    
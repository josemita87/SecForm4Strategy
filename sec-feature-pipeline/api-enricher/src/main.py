from quixstreams import Application
from config import config
from typing import List, Dict, Generator
from api_handler import ApiHandler
from loguru import logger
import json
from datetime import datetime
import xxhash
import time

app = Application(
    broker_address=config.kafka_broker_address,
    consumer_group=config.consumer_group,
    auto_offset_reset=config.auto_offset_reset,
    processing_guarantee=config.processing_guarantee
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
            message = consumer.poll(config.poll_timeout)

            if message is None:
                if buffer:
                    #consumer.store_offsets(message)
                    #consumer.commit_checkpoint()
                    yield from buffer
                    buffer.clear()  
                
                break

            buffer.append(json.loads(message.value().decode('utf-8')))
            
            #If buffer is full, yield the data
            if len(buffer) >= config.buffer_size:
                #consumer.store_offsets(message)
                #consumer.commit_checkpoint()
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
    
    time.sleep(config.delay * 2)
    logger.info(f'Enricher Microservice Started')
    logger.info(f'Connected to redpanda broker at {config.kafka_broker_address}')
    logger.info(f'Input topic: {config.kafka_input_topic}')
    logger.info(f'Output topic: {config.kafka_output_topic}')
    logger.info(f'ENV VARIABLES:\n ->Buffer Size: {config.buffer_size} \n -> Consumer Group: {config.consumer_group} \n -> Auto Offset Reset: {config.auto_offset_reset} \n')
    
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

    logger.info('Enricher Microservice Enriched Data')
    # Pass the enriched generator directly to produce_data
    produce_data(enriched_generator)

    #logger.info(f'Enricher Microservice Produced Buffered Data. Exiting...')

from quixstreams import Application
import json
from typing import Dict, Generator, Tuple
from loguru import logger
import pandas as pd
from datetime import datetime


class Connection:
    def __init__(self, broker_address, input_topic_name):
        
        self.app = Application(
            broker_address=broker_address,
            consumer_group='kafka-to-store',
            auto_offset_reset='latest',
            #commit_every=1
            )

        self.input_topic = self.app.topic(
            name=input_topic_name,
            value_deserializer='json',
            key_deserializer='string'
        )

         
    def consume_data(self, buffer_size) -> Tuple[bool, list[Dict]]:
        """ In this method, we consume data from the Kafka topic and return a buffer of transactions.
        We do not use generators because the aim is to push data in batches to the feature store."""

        buffer = []
        with self.app.get_consumer() as consumer:
            consumer.subscribe(topics=[self.input_topic.name])

            while True:
                
                message = consumer.poll()
                #if not message:
                   # logger.debug("Not any messages left to consume")
                    
                    #return True, buffer
            
                if message:
                    # Decode the key and value & add to buffer
                    key = {'key': message.key().decode('utf-8')}
                    updated_values = json.loads(message.value().decode('utf-8'))
                    
                    # Convert the date to datetime object
                    if 'date' in updated_values:
                        updated_values['date'] = datetime.strptime(updated_values['date'], '%Y-%m-%d')
                    
                    merged = {**key, **updated_values}
                    buffer.append(merged)
                    
                
                if len(buffer) >= buffer_size:
                    #Update offset for the consumer
                 
                    return False, buffer
                    

        
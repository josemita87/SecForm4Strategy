from config import config
from typing import List, Dict, Generator
from loguru import logger
from quixstreams import Application
from datetime import datetime
from parser import Form4Parser
from io import BytesIO
import xxhash
import json
from itertools import islice
from datetime import timedelta


app = Application(
    broker_address=config.kafka_broker_address,
    consumer_group='master-form-4-',
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



def last_n_days(n: int) -> int:
    return (datetime.now() - timedelta(days=n)).timestamp() * 1000

def consume_data() -> List[str]:
    
    urls = []
    with app.get_consumer() as consumer:

        consumer.subscribe(topics = [input_topic.name])
        #counter = 0

        while True:
            message = consumer.poll(1)
           
            try:  
                if int(message.timestamp()[1]) < last_n_days(config.timedelta):
                    logger.debug('Finished scraping!')
                    break
            except:
                logger.debug('Coudln\'t get timestamp')
                pass
           
            try:
                url = json.loads(message.value().decode('utf-8'))['file_path']
                urls.append(url)
                
            except:
                break    
                
    return urls


def parse_and_produce_4Fs(urls: List[str], buffer_size: int) -> None:

    buffer = []
    
    for url in islice(urls, config.test_size):
        
        filing_transactions = Form4Parser(url, config.sleep_time).create_txs()
        if filing_transactions:
            buffer.extend(filing_transactions)
        
        if len(buffer) >= buffer_size:
            produce_data(buffer)
            buffer = []
    
    if buffer:
        produce_data(buffer)


def produce_data(data: List[dict]) -> None:
    
    with app.get_producer() as producer:
        for record in data:

            timestamp = int(datetime.strptime(
                record['date'], '%Y-%m-%d').timestamp()
            ) * 1000
            
            #Add timestamp to the record for future reference
            record['timestamp'] = timestamp

            try:
                key = xxhash.xxh64(
                    record['link'] + record['remaining_shares'] +
                    ('1' if record['derivative'] else '0')
                ).hexdigest()

            except:
                #Create a key for the record without using link 
                key = xxhash.xxh64(
                    record['remaining_shares'] +
                    ('1' if record['derivative'] else '0')
                ).hexdigest()

            
            message = output_topic.serialize(
                key=key,
                value=record,
            )

            producer.produce(
                topic=output_topic.name,
                value=message.value,
                key=message.key,
                timestamp=timestamp
            )
                
    logger.debug(f"Produced {len(data)} messages")


#Auxiliary function to batch records when producing data
def batch_records(records: list, batch_size: int) -> Generator:
    """Yield successive batches of size `batch_size` from `records`."""
    for i in range(0, len(records), batch_size):
        yield records[i:i + batch_size]


if __name__ == '__main__':

    urls = consume_data()
    transactions = parse_and_produce_4Fs(urls, config.buffer_size)
    




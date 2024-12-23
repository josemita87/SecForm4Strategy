from config import config
from typing import List, Dict, Generator
from loguru import logger
from quixstreams import Application
import pandas as pd
import requests
from datetime import datetime
import xxhash
from io import BytesIO

headers = {
    'User-Agent': 'CompanyE InvestmentServices admin@companye.com',
    'Accept-Encoding': 'gzip, deflate',
    'Host': 'www.sec.gov'
}


# Create a connection to the redpanda broker
app = Application(config.kafka_broker_address, loglevel='CRITICAL')
output_topic = app.topic(
    name=config.kafka_output_topic,
    value_serializer='json',
    key_serializer='string'
)

logger.debug(f'Connected to redpanda broker at {config.kafka_broker_address}')
logger.debug(f'Output topic: {config.kafka_output_topic}')


def fetch_parse_data(year, quarter):

    url = f'https://www.sec.gov/Archives/edgar/full-index/{year}/{quarter}/master.idx'

    response = requests.get(url, headers=headers, timeout=10)

    if response.status_code == 200:
        content_str = response.content.decode('utf-8')
        lines = content_str.splitlines()

        start_index = 0
        # Crop the header
        for i, line in enumerate(lines):
            if '|' in line:
                start_index = i + 2
                break

        data_str = '\n'.join(lines[start_index:])

        data = BytesIO(data_str.encode('utf-8'))

        # Now read the data into a DataFrame
        df = pd.read_csv(
            data,
            sep='|',
            header=None,
            names=["cik", "company", "form_type", "date", "file_path"],
            dtype=str,
            encoding='utf-8'
        )

        import time
        time.sleep(1)
        logger.debug(f'Fetched data for {year} {quarter}')
        return df


def produce_data(data: pd.DataFrame) -> None:

    data = data.copy()

    # Convert the date to a timestamp in milliseconds
    data['timestamp'] = pd.to_datetime(data['date']).astype(int) // 10**6

    # Convert the file path to a unique key
    data['key'] = data['file_path'].apply(lambda x: xxhash.xxh64(x).hexdigest())
    
    # Convert the data to a list of dictionaries
    records: List[Dict] = data.to_dict(orient='records')

    with app.get_producer() as producer:
        
        # Split the records into batches
        batch:Generator = batch_records(records, config.batch_size)
        
        for records in batch:
            for record in records:
     
                key = record.pop('key')
                message = output_topic.serialize(
                    key=key,
                    value=record,
                )

                producer.produce(
                    topic=output_topic.name,
                    value=message.value,
                    key=message.key,
                    timestamp=record['timestamp']
                )

            # Flush the producer
            producer.flush()
            

def get_historic_data(form_type: str, years: int) -> None:

    for year in range(datetime.now().year - years, datetime.now().year + 1):

        for quarter in ['QTR1', 'QTR2', 'QTR3', 'QTR4']:
            df = fetch_parse_data(year, quarter)
            filings = df[df['form_type'] == form_type]

            produce_data(filings)


def get_latest_data(form_type: str) -> None:

    quarter = f'QTR{(datetime.now().month - 1) // 3 + 1}'
    df = fetch_parse_data(datetime.now().year, quarter)
    filings = df[df['Form Type'] == form_type]

    produce_data(filings)



#Auxiliary function to batch records when producing data
def batch_records(records: list, batch_size: int) -> Generator:
    """Yield successive batches of size `batch_size` from `records`."""
    for i in range(0, len(records), batch_size):
        yield records[i:i + batch_size]

if __name__ == '__main__':
    get_historic_data(config.form_type, config.years)



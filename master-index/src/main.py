from config import config
from typing import List, Dict
from loguru import logger
from quixstreams import Application
import sqlite3 as sql
import os
import pandas as pd
import requests
import json
from datetime import datetime
import time
import random
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


def produce_data(data: pd.DataFrame) -> None:

    data: List[Dict] = data.to_dict(orient='records')

    for record in data:

        timestamp = int(datetime.strptime(record['date'],'%Y-%m-%d').timestamp()) * 1000
        with app.get_producer() as producer:
            
            message = output_topic.serialize(
                key=record['file_path'],
                value=record, 
            )

            producer.produce(
                topic=output_topic.name,
                value=message.value,
                key=message.key,
                timestamp=timestamp
            )

            
def fetch_parse_data(year, quarter):

    url = f'https://www.sec.gov/Archives/edgar/full-index/{year}/{quarter}/master.idx'

    response = requests.get(url, headers=headers, timeout=10)

    if response.status_code == 200:
        content_str = response.content.decode('utf-8')
        lines = content_str.splitlines()

        start_index = 0
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
        
        logger.debug(f'Fetched data for {year} {quarter}')
        return df


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


if __name__ == '__main__':
    get_historic_data(config.form_type, config.years)

# TODO
# 1. Stablish connection with redpanda
# 2. Produce topics Finsish microservice
#2.2 Check why is the key of the topic failing
#2.3 Check why timestamp is failing

#2.4 Effectively manage cases of duplication ( avoid duplicates)
# 3. Dockerize microservice
# 4. Check whether the code can be reused to parse 13f forms

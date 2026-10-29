"""Entry point: consumes Kafka transactions, cleans them, and pushes them to the feature store.

Columns are cast by policy when present: ``CATEGORICAL`` to category, ``BOOLEAN``
to bool and ``NUMERIC`` to the listed dtypes. ``ownership`` holds 'D'/'I'
(direct/indirect) and is categorical, since a bool cast would make both True.
"""

import logging
from pathlib import Path
from typing import Any

import pandas as pd
from config import config
from constants import FeatureGroup
from secform4strategy_clients.feature_store import HopsworksClient, load_feature_group_catalog
from secform4strategy_clients.messaging import KafkaClient

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)


CATEGORICAL = [
    'company_cik',
    'ticker',
    'insider_cik',
    'insider_name',
    'owner_code',
    'exchange',
    'acquired_disposed',
    'coding',
    'sic',
    'ownership',
]
BOOLEAN = ['rule105b1', 'derivative', 'equity_swap']
NUMERIC = {
    'shares': 'int32',
    'price': 'float64',
    'remaining_shares': 'int32',
    'direct_holding': 'int64',
    'indirect_holding': 'int64',
    'market_cap': 'int64',
    'timestamp': 'int64',
}


def data_cleaning(data: list[dict[str, Any]]) -> pd.DataFrame:
    """Build a DataFrame from the consumed records, dropping duplicates and rows with NaN.

    Args:
        data: List of transaction dictionaries to clean.

    Returns:
        A DataFrame with duplicate rows and rows containing NaN removed.
    """
    return pd.DataFrame(data).drop_duplicates().dropna()


def validate_and_reduce_mem_storage(data: pd.DataFrame) -> pd.DataFrame:
    """Cast columns to memory-efficient dtypes, dropping rows whose numbers or dates fail to parse.

    Args:
        data: DataFrame of transactions to optimize.

    Returns:
        A DataFrame with columns cast to memory-efficient dtypes and invalid rows removed.
    """
    data = data.astype(
        {'link': 'str'}
        | {col: 'category' for col in CATEGORICAL if col in data}
        | {col: 'bool' for col in BOOLEAN if col in data}
    )
    numeric = {col: dtype for col, dtype in NUMERIC.items() if col in data}
    data[list(numeric)] = data[list(numeric)].apply(pd.to_numeric, errors='coerce')
    data['date'] = pd.to_datetime(data['date'], errors='coerce')

    # Drop unparseable rows before the integer casts, which cannot hold NaN
    return data.dropna(subset=[*numeric, 'date']).astype(numeric)


if __name__ == '__main__':
    # Connect to the Kafka topic and the feature store
    consumer = KafkaClient(
        broker_address=config.kafka_broker_address,
        consumer_group=config.consumer_group,
        auto_offset_reset=config.auto_offset_reset,
    )
    feature_store = HopsworksClient(
        config.project_name,
        config.hopsworks_api_key,
        load_feature_group_catalog(Path(__file__).parent / 'feature_groups.yaml', config.feature_group_version),
    )
    # Route to the correct feature group (inference / training)
    target = FeatureGroup.BI4 if config.system_inference else FeatureGroup.BT4

    logger.info(f'Connected to Kafka broker at {config.kafka_broker_address}')
    logger.info(f'Connected to input topic at {config.kafka_input_topic}')
    logger.info(f'Connected to Hopsworks project at {config.project_name}')
    logger.info(f'Connected to Hopsworks feature store at {target.name}')

    is_finished = False
    while not is_finished:
        is_finished, records = consumer.consume_batch(
            topic_name=config.kafka_input_topic,
            buffer_size=config.buffer_size,
            timeout=config.poll_timeout,
        )
        if records:
            feature_store.push(target, validate_and_reduce_mem_storage(data_cleaning(records)))

    logger.info('No more messages to consume. Exiting kafka-to-store...')

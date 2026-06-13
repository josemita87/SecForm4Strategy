"""Entry point that consumes Kafka transactions and pushes them to the feature store."""

import logging
from pathlib import Path

import pandas as pd
from config import config
from constants import FeatureGroup
from inside_out_clients.feature_store import HopsworksClient, load_feature_group_catalog
from inside_out_clients.messaging import KafkaClient

# Catalog of feature groups this service touches, loaded from the YAML spec template.
FEATURE_GROUPS = load_feature_group_catalog(Path(__file__).parent / 'feature_groups.yaml', config.feature_group_version)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)


def data_cleaning(data: list[dict]) -> pd.DataFrame:
    """Clean the consumed data by dropping duplicates and rows with NaN values.

    Args:
        data: List of transaction dictionaries to clean.

    Returns:
        A DataFrame with duplicate rows and rows containing NaN removed.
    """
    # Convert the list of dicts to a DataFrame
    data = pd.DataFrame(data)

    # Drop duplicate rows
    data = data.drop_duplicates()

    # Drop rows with NaN values in any column
    data = data.dropna()

    return data


def validate_and_reduce_mem_storage(data: pd.DataFrame) -> pd.DataFrame:
    """Reduce DataFrame memory usage by downcasting columns to efficient dtypes.

    Categorical, boolean, numeric and datetime conversions are applied where the
    relevant columns exist. Rows that fail a numeric or date conversion are dropped.

    Args:
        data: DataFrame of transactions to optimize.

    Returns:
        A DataFrame with columns cast to memory-efficient dtypes and invalid rows removed.
    """
    data['link'] = data['link'].astype('str')

    # Convert columns to categorical where appropriate
    for col in [
        'company_cik',
        'ticker',
        'insider_cik',
        'insider_name',
        'owner_code',
        'exchange',
        'acquired_disposed',
        'coding',
        'sic',
    ]:
        if col in data.columns:
            data[col] = data[col].astype('category')

    # Convert booleans to actual boolean dtype
    for col in ['rule105b1', 'derivative', 'equity_swap', 'ownership']:
        if col in data.columns:
            data[col] = data[col].astype('bool')

    # Convert numeric columns to more memory-efficient types
    numeric_columns = {
        'shares': 'int32',
        'price': 'float64',
        'remaining_shares': 'int32',
        'direct_holding': 'int64',
        'indirect_holding': 'int64',
        'market_cap': 'int64',
        'timestamp': 'int64',
    }
    for col, dtype in numeric_columns.items():
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors='coerce').astype(dtype)
            data = data.dropna(subset=[col])  # Drop rows where conversion failed

    # Convert 'date' to datetime
    data['date'] = pd.to_datetime(data['date'], errors='coerce')
    data = data.dropna(subset=['date'])  # Drop rows where 'date' conversion failed

    return data


if __name__ == '__main__':
    # Connect to the Kafka topic
    consumer = KafkaClient(
        broker_address=config.kafka_broker_address,
        consumer_group=config.consumer_group,
        auto_offset_reset=config.auto_offset_reset,
    )

    # Connect to the feature store
    feature_store = HopsworksClient(config.project_name, config.hopsworks_api_key, FEATURE_GROUPS)

    is_finished = False

    # Logs
    logger.info(f'Connected to Kafka broker at {config.kafka_broker_address}')
    logger.info(f'Connected to input topic at {config.kafka_input_topic}')
    logger.info(f'Connected to Hopsworks project at {config.project_name}')
    logger.info(f'Connected to Hopsworks feature store at {"BI4" if config.system_inference else "BT4"}')

    while not is_finished:
        is_finished, records = consumer.consume_batch(
            topic_name=config.kafka_input_topic,
            buffer_size=config.buffer_size,
            timeout=config.poll_timeout,
        )

        if records:
            # The Kafka client returns raw records; building the DataFrame, parsing
            # the date and downcasting dtypes are domain concerns owned here.
            data: pd.DataFrame = data_cleaning(records)
            data: pd.DataFrame = validate_and_reduce_mem_storage(data)

            # Route to the correct feature group (inference / training)
            if config.system_inference:
                feature_store.push(FeatureGroup.BI4, data)

            elif config.system_training:
                feature_store.push(FeatureGroup.BT4, data)

        else:
            logger.info('No more messages to consume. Exiting kafka-to-store...')
            break

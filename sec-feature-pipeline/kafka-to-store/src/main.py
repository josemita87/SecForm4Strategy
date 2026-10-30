"""Entry point: consumes Kafka transactions, cleans them, and pushes them to the feature store."""

import logging
from pathlib import Path

import pandas as pd
from config import config
from constants import Column, FeatureGroup
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.messaging import KafkaClient

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)


def validate_and_reduce_mem_storage(data: pd.DataFrame) -> pd.DataFrame:
    """Cast columns to memory-efficient dtypes, dropping rows whose numbers or dates fail to parse.

    Columns present are cast to category, bool or a numeric dtype. ``ownership`` holds 'D'/'I'
    (direct/indirect) and is categorical, since a bool cast would make both True.

    Args:
        data: DataFrame of transactions to optimize.

    Returns:
        A DataFrame with columns cast to memory-efficient dtypes and invalid rows removed.
    """
    data = data.astype(
        {Column.LINK: 'str'}
        | {
            col: 'category'
            for col in (
                Column.COMPANY_CIK,
                Column.TICKER,
                Column.INSIDER_CIK,
                Column.INSIDER_NAME,
                Column.OWNER_CODE,
                Column.ACQUIRED_DISPOSED,
                Column.CODING,
                Column.OWNERSHIP,
            )
            if col in data
        }
        | {col: 'bool' for col in (Column.RULE_10B5_1, Column.DERIVATIVE, Column.EQUITY_SWAP) if col in data}
    )
    numeric = {
        col: dtype
        for col, dtype in (
            (Column.SHARES, 'float64'),
            (Column.PRICE, 'float64'),
            (Column.REMAINING_SHARES, 'float64'),
            (Column.DIRECT_HOLDING, 'float64'),
            (Column.INDIRECT_HOLDING, 'float64'),
            (Column.TIMESTAMP, 'int64'),
        )
        if col in data
    }
    data[list(numeric)] = data[list(numeric)].apply(pd.to_numeric, errors='coerce')
    data[Column.DATE] = pd.to_datetime(data[Column.DATE], errors='coerce')

    # Drop unparseable rows before the integer casts, which cannot hold NaN
    return data.dropna(subset=[*numeric, Column.DATE]).astype(numeric)


if __name__ == '__main__':
    # Connect to the Kafka topic and the feature store
    consumer = KafkaClient(
        broker_address=config.kafka_broker_address,
        consumer_group=config.consumer_group,
        auto_offset_reset=config.auto_offset_reset,
    )
    feature_store = IcebergFeatureStore(
        load_catalog('default'),
        config.feature_store_namespace,
        load_feature_group_catalog(Path(__file__).parent / 'spec.yaml', config.feature_group_version),
    )
    # Route to the correct feature group (inference / training)
    target = FeatureGroup.BI4 if config.system_inference else FeatureGroup.BT4

    logger.info(f'Connected to Kafka broker at {config.kafka_broker_address}')
    logger.info(f'Connected to input topic at {config.kafka_input_topic}')
    logger.info(f'Writing to feature group {target.name} in namespace {config.feature_store_namespace}')

    is_finished = False
    while not is_finished:
        is_finished, records = consumer.consume_batch(
            topic_name=config.kafka_input_topic,
            buffer_size=config.buffer_size,
            timeout=config.poll_timeout,
        )
        if records:
            feature_store.push(
                target, validate_and_reduce_mem_storage(pd.DataFrame(records).drop_duplicates().dropna())
            )

    logger.info('No more messages to consume. Exiting kafka-to-store...')

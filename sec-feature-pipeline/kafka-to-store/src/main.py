"""Entry point: consumes Kafka transactions, cleans them, and pushes them to the feature store."""

import logging
from typing import TYPE_CHECKING

import pandas as pd
from config import config
from constants import Col, FeatureGroup
from paths import SPEC_PATH
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.messaging import KafkaClient

if TYPE_CHECKING:
    from secform4strategy_clients.ports import FeatureStore

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
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
        {Col.LINK: 'str'}
        | {
            col: 'category'
            for col in (
                Col.COMPANY_CIK,
                Col.TICKER,
                Col.INSIDER_CIK,
                Col.INSIDER_NAME,
                Col.OWNER_CODE,
                Col.ACQUIRED_DISPOSED,
                Col.CODING,
                Col.OWNERSHIP,
            )
            if col in data
        }
        | {col: 'bool' for col in (Col.RULE_10B5_1, Col.DERIVATIVE, Col.EQUITY_SWAP) if col in data}
    )
    numeric = {
        col: dtype
        for col, dtype in (
            (Col.SHARES, 'float64'),
            (Col.PRICE, 'float64'),
            (Col.POSITION, 'int32'),
            (Col.REMAINING_SHARES, 'float64'),
            (Col.DIRECT_HOLDING, 'float64'),
            (Col.INDIRECT_HOLDING, 'float64'),
        )
        if col in data
    }
    data[list(numeric)] = data[list(numeric)].apply(pd.to_numeric, errors='coerce')
    data[Col.DATE] = pd.to_datetime(data[Col.DATE], errors='coerce')
    data[Col.ACCEPTED_AT] = pd.to_datetime(data[Col.ACCEPTED_AT], utc=True, errors='coerce')

    # Drop unparseable rows before the integer casts, which cannot hold NaN
    return data.dropna(subset=[*numeric, Col.DATE, Col.ACCEPTED_AT]).astype(numeric)


if __name__ == '__main__':
    # Connect to the Kafka topic and the feature store
    consumer: KafkaClient = KafkaClient(
        broker_address=config.kafka_broker_address,
        consumer_group=config.consumer_group,
        auto_offset_reset=config.auto_offset_reset,
    )
    feature_store: FeatureStore = IcebergFeatureStore(
        load_catalog('default'),
        config.feature_store_namespace,
        load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
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
            rows = validate_and_reduce_mem_storage(pd.DataFrame(records).drop_duplicates().dropna())
            feature_store.push(target, rows)
            logger.info(f'Upserted {len(rows)} of {len(records)} transactions into {target.name}')

    logger.info('No more messages to consume. Exiting kafka-to-store...')

"""Entry point: consumes queued transactions, cleans them, and pushes them to the feature store."""

import logging
import sys
from collections.abc import Sequence
from contextlib import closing
from typing import TYPE_CHECKING

import pandas as pd
from config import Kafka, Sqs, config
from constants import Col, FeatureGroup
from paths import SPEC_PATH
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.messaging import KafkaClient
from secform4strategy_clients.ports import RetryableError
from secform4strategy_clients.sqs import SqsQueue

if TYPE_CHECKING:
    from secform4strategy_clients.ports import FeatureStore, MessageQueue
    from secform4strategy_clients.records import Message

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)


def validate_and_reduce_mem_storage(data: pd.DataFrame) -> pd.DataFrame:
    """Cast columns to memory-efficient dtypes, dropping rows that cannot be priced or labelled.

    A row is dropped when a number or date fails to parse, or when it has no ticker, direction
    (acquired or disposed) or transaction date. Other empty fields, such as the coding, are kept.

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

    # Drop unusable rows before the integer casts, which cannot hold NaN
    return data.dropna(subset=[*numeric, Col.DATE, Col.ACCEPTED_AT, Col.TICKER, Col.ACQUIRED_DISPOSED]).astype(numeric)


def store(messages: Sequence[Message], feature_store: FeatureStore, target: FeatureGroup) -> pd.DataFrame:
    """Clean the messages' transactions and upsert them into ``target``; the store's ``RetryableError`` propagates.

    Args:
        messages: Received transactions, at least one.
        feature_store: Where the rows are upserted.
        target: ``BT4`` for training runs, ``BI4`` for inference.

    Returns:
        The rows upserted.
    """
    records = pd.DataFrame([message.body for message in messages]).drop_duplicates()
    if dropped := len(records) - len(rows := validate_and_reduce_mem_storage(records)):
        logger.warning(f'{dropped} of {len(records)} dropped: missing ticker, direction or date, or unparseable')
    feature_store.push(target, rows)
    logger.info(f'Upserted {len(rows)} of {len(records)} transactions into {target.name}')
    return rows


if __name__ == '__main__':
    feature_store: FeatureStore = IcebergFeatureStore(
        load_catalog('default'),
        config.feature_store_namespace,
        load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
    )
    # Route to the correct feature group (inference / training)
    target = FeatureGroup.BI4 if config.system_inference else FeatureGroup.BT4
    logger.info(f'{config.input_queue} -> feature group {target.name} in {config.feature_store_namespace}')

    client: KafkaClient | SqsQueue
    match config.queue:
        case Kafka() as kafka:
            client = KafkaClient(kafka.broker_address, kafka.consumer_group, kafka.auto_offset_reset)
        case Sqs() as sqs:
            client = SqsQueue(sqs.region)

    # Closing a Kafka consumer leaves its group, so the next run gets its partitions at once
    queue: MessageQueue
    with closing(client) as queue:
        while messages := queue.receive(config.input_queue, config.buffer_size, config.poll_timeout):
            try:
                store(messages, feature_store, target)
            except RetryableError as error:
                # Unacked, the batch is delivered again to the next run
                sys.exit(f'Store unavailable, {len(messages)} messages left for a retry: {error}')
            queue.ack(config.input_queue, messages)
        # The last, empty receive may still have dropped non-JSON messages; acking commits past them
        queue.ack(config.input_queue, ())

    logger.info('No more messages to consume. Exiting kafka-to-store...')

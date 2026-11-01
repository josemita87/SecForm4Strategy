"""Entry point: consumes filing URLs, parses their Form 4s, and produces the transactions."""

import logging
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from config import config
from parser import parse_filing
from secform4strategy_clients.edgar import EdgarClient
from secform4strategy_clients.messaging import KafkaClient
from secform4strategy_clients.ports import Filing

if TYPE_CHECKING:
    from secform4strategy_clients.ports import Filings

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)


def select_filings(
    records: Iterable[tuple[dict[str, Any], int]], max_age: timedelta | None
) -> list[tuple[Filing, int]]:
    """Read ``(filing, offset)`` pairs from consumed master-index messages, dropping stale and malformed ones.

    Args:
        records: ``(record, offset)`` pairs as returned by the Kafka client.
        max_age: Filings older than this are skipped; None keeps every filing.

    Returns:
        The ``(filing, offset)`` pairs to parse.
    """
    selected = []
    for msg, offset in records:
        try:
            filing = Filing(msg['accession'], msg['file_path'], datetime.fromisoformat(msg['filed_at']))
        except KeyError, TypeError, ValueError, OverflowError, OSError:
            logger.error(f'Malformed filing message: {msg}')
            continue
        if max_age is not None and filing.filed_at < datetime.now(UTC) - max_age:
            logger.info(f'Filing too old: {filing.filed_at}')
            continue
        selected.append((filing, offset))
    return selected


if __name__ == '__main__':
    kafka: KafkaClient = KafkaClient(
        broker_address=config.kafka_broker_address,
        consumer_group=config.consumer_group,
        auto_offset_reset=config.auto_offset_reset,
    )
    edgar: Filings = EdgarClient(config.sec_user_agent, config.sec_max_rate)
    max_age = timedelta(days=config.days_back) if config.mode == 'live' else None

    def produce(records: list[dict[str, Any]]) -> None:
        """Publish transaction records to the output topic, keyed by their key (each carries its ``accepted_at``)."""
        kafka.produce(config.kafka_output_topic, ((record['key'], record) for record in records))
        logger.debug(f'Produced {len(records)} messages')

    time.sleep(config.delay)
    logger.info(f'Scraper started: {config.kafka_input_topic} -> {config.kafka_output_topic}')

    while True:
        records = kafka.consume_with_offsets(config.kafka_input_topic, config.buffer_size, config.poll_timeout)
        selected = select_filings(records, max_age)[: config.test_size if config.test_trial else None]

        # Commit per produced batch and at the end, so transaction-less filings are not re-consumed
        buffer: list[dict[str, Any]] = []
        for filing, offset in selected:
            buffer.extend(parse_filing(filing, edgar))
            if len(buffer) >= config.buffer_size:
                produce(buffer)
                kafka.commit_offset(offset)
                buffer = []
        if selected:
            if buffer:
                produce(buffer)
            kafka.commit_offset(selected[-1][1])

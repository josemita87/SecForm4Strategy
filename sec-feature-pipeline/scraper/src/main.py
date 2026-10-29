"""Entry point: consumes filing URLs, parses their Form 4s, and produces the transactions."""

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import xxhash
from config import config
from const import headers
from parser import parse_filing
from secform4strategy_clients.edgar import EdgarClient
from secform4strategy_clients.messaging import KafkaClient

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)


def select_urls(records: list[tuple[dict[str, Any], int]], max_age: timedelta | None) -> list[tuple[str, int]]:
    """Extract ``(url, offset)`` pairs from consumed records, dropping stale ones.

    Args:
        records: ``(record, offset)`` pairs as returned by the Kafka client.
        max_age: Filings older than this are skipped; None keeps every filing.

    Returns:
        The ``(url, offset)`` pairs to parse.
    """
    urls = []
    for msg, offset in records:
        try:
            date = datetime.fromtimestamp(msg['timestamp'] / 1000, tz=UTC)
            if max_age is not None and date < datetime.now(UTC) - max_age:
                logger.info(f'Filing too old: {date}')
                continue
            urls.append((msg['file_path'], offset))
        except KeyError, TypeError, ValueError, OverflowError, OSError:
            logger.error(f"Couldn't extract URL from message: {msg}")
    return urls


def build_messages(records: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], int]]:
    """Key transaction records as ``(key, record, timestamp)`` Kafka messages, stamped at acceptance.

    Args:
        records: Transaction records with ``link`` (ending in ``<accession>.txt``), ``remaining_shares``, ``derivative``
            and the filing's acceptance ``timestamp`` (epoch ms).

    Returns:
        The messages, with ``key`` also stored on each record.
    """
    for record in records:
        # Key on accession (the link's directory depends on the filer) + remaining shares + derivative flag
        accession = (record.get('link') or '').rsplit('/', 1)[-1].removesuffix('.txt')
        record['key'] = xxhash.xxh64(
            f'{accession}{record["remaining_shares"]:.15g}{int(record["derivative"])}'
        ).hexdigest()
    return [(record['key'], record, record['timestamp']) for record in records]


if __name__ == '__main__':
    kafka = KafkaClient(
        broker_address=config.kafka_broker_address,
        consumer_group=config.consumer_group,
        auto_offset_reset=config.auto_offset_reset,
    )
    filings = EdgarClient(headers=headers)
    max_age = timedelta(days=config.days_back) if config.mode == 'live' else None

    def produce(records: list[dict[str, Any]]) -> None:
        """Publish shaped records to the output topic."""
        kafka.produce(config.kafka_output_topic, messages := build_messages(records))
        logger.debug(f'Produced {len(messages)} messages')

    time.sleep(config.delay)
    logger.info(f'Scraper started: {config.kafka_input_topic} -> {config.kafka_output_topic}')

    while True:
        records = kafka.consume_with_offsets(config.kafka_input_topic, config.buffer_size, config.poll_timeout)
        urls = select_urls(records, max_age)[: config.test_size if config.test_trial else None]

        # Commit per produced batch and at the end, so transaction-less filings are not re-consumed
        buffer: list[dict[str, Any]] = []
        for url, offset in urls:
            buffer.extend(parse_filing(url, filings, config.sleep_time) or [])
            if len(buffer) >= config.buffer_size:
                produce(buffer)
                kafka.commit_offset(offset)
                buffer = []
        if urls:
            if buffer:
                produce(buffer)
            kafka.commit_offset(urls[-1][1])

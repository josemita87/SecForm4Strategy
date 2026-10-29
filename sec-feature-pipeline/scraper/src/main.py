"""Entry point: consumes filing URLs, parses their Form 4s, and produces the transactions."""

import logging
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import xxhash
from config import config
from const import headers
from parser import parse_filing
from secform4strategy_clients.edgar import EdgarClient
from secform4strategy_clients.messaging import KafkaClient

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)


def select_urls(records: list[tuple[dict[str, Any], int]], max_age: pd.Timedelta | None) -> list[tuple[str, int]]:
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
            date = pd.to_datetime(msg['timestamp'], unit='ms', utc=True)
            if max_age is not None and date < pd.Timestamp.now('UTC') - max_age:
                logger.info(f'Filing too old: {date}')
                continue
            urls.append((msg['file_path'], offset))
        except (KeyError, TypeError, ValueError):
            logger.error(f"Couldn't extract URL from message: {msg}")
    return urls


def build_messages(records: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], int]]:
    """Key and timestamp transaction records as ``(key, record, timestamp)`` Kafka messages.

    Records whose date cannot be parsed are logged and skipped.

    Args:
        records: Transaction records, each with date and link fields.

    Returns:
        The messages, with ``key`` and ``timestamp`` also stored on each record.
    """
    messages = []
    for record in records:
        # Dates may carry a timezone suffix ('2024-01-05-05:00'); the first 10 chars are the day
        try:
            day = datetime.strptime(record['date'][:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            logger.error(f'Unparseable date, skipping record: {record}')
            continue
        record['timestamp'] = int(day.timestamp()) * 1000

        # Key on link + remaining shares + derivative flag
        record['key'] = xxhash.xxh64(
            f'{record.get("link") or ""}{record["remaining_shares"]:.15g}{int(record["derivative"])}'
        ).hexdigest()
        messages.append((record['key'], record, record['timestamp']))
    return messages


if __name__ == '__main__':
    kafka = KafkaClient(
        broker_address=config.kafka_broker_address,
        consumer_group=config.consumer_group,
        auto_offset_reset=config.auto_offset_reset,
    )
    filings = EdgarClient(headers=headers)
    max_age = pd.Timedelta(days=config.days_back) if config.mode == 'live' else None

    def produce(records: list[dict[str, Any]]) -> None:
        """Publish shaped records to the output topic."""
        kafka.produce(config.kafka_output_topic, messages := build_messages(records))
        logger.debug(f'Produced {len(messages)} messages')

    time.sleep(config.delay)
    logger.info(f'Scraper started: {config.kafka_input_topic} -> {config.kafka_output_topic}')

    while True:
        records = kafka.consume_with_offsets(config.kafka_input_topic, config.buffer_size, config.poll_timeout)
        urls = select_urls(records, max_age)[: config.test_size if config.test_trial else None]

        # Commit after each produced batch and once at the end, so filings that
        # yield no transactions are not re-consumed on restart
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

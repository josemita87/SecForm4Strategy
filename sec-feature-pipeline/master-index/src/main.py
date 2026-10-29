"""Entry point: fetches SEC Form 4 filing links and produces them to Kafka."""

import logging
import time
from datetime import datetime, timezone
from typing import Any

import xxhash
from config import config
from secform4strategy_clients.edgar import EdgarClient
from secform4strategy_clients.messaging import KafkaClient

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)

HEADERS = {
    'User-Agent': 'CompanyE InvestmentServices admin@companye.com',
    'Accept-Encoding': 'gzip, deflate',
    'Host': 'www.sec.gov',
}
FORM_TYPE = '4'


def build_messages(filings: list[dict[str, str]], received_now: bool) -> list[tuple[str, dict[str, Any], int]]:
    """Key and timestamp filing records as ``(key, record, timestamp)`` Kafka messages.

    Args:
        filings: Filing records, with ``file_path`` (and ``date`` unless ``received_now``).
        received_now: Stamp every filing with the current time instead of its filing date.

    Returns:
        One message per filing, keyed by a hash of its file path.
    """
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    timestamps = [
        now_ms
        if received_now
        else int(datetime.strptime(filing['date'].strip(), '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()) * 1000
        for filing in filings
    ]
    return [
        (xxhash.xxh64(filing['file_path']).hexdigest(), {**filing, 'timestamp': ts}, ts)
        for filing, ts in zip(filings, timestamps, strict=True)
    ]


if __name__ == '__main__':
    filings = EdgarClient(headers=HEADERS)
    kafka = KafkaClient(config.kafka_broker_address, loglevel='CRITICAL')

    def produce(data: list[dict[str, str]], received_now: bool) -> None:
        """Publish filings to the output topic one batch at a time (each produce call flushes)."""
        messages = build_messages(data, received_now)
        for i in range(0, len(messages), config.buffer_size):
            kafka.produce(config.kafka_output_topic, messages[i : i + config.buffer_size])

    logger.info(f'Master index started in {config.mode} mode -> {config.kafka_output_topic}')

    if config.mode == 'live':
        for _ in range(config.live_iterations):
            produce([{'file_path': path} for path in filings.fetch_live_links(config.num_pages)], received_now=True)
            time.sleep(config.secs_between_iterations)
    else:
        now = datetime.now(timezone.utc)
        quarters = (
            [(year, f'QTR{q}') for year in range(now.year - config.years, now.year + 1) for q in range(1, 5)]
            if config.mode == 'historical'
            else [(now.year, f'QTR{(now.month - 1) // 3 + 1}')]
        )
        for year, quarter in quarters:
            if (index := filings.fetch_master_index(year, quarter)) is None:
                logger.warning(f'No master index for {year} {quarter}')
                continue
            produce([filing for filing in index if filing['form_type'] == FORM_TYPE], received_now=False)

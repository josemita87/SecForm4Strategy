"""Entry point: fetches SEC Form 4 filing links and produces them to Kafka."""

import logging
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from itertools import batched
from typing import TYPE_CHECKING

import xxhash
from config import config
from secform4strategy_clients.edgar import EdgarClient
from secform4strategy_clients.messaging import KafkaClient

if TYPE_CHECKING:
    from secform4strategy_clients.ports import Filing

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)

FORM_TYPE = '4'


def build_messages(filings: Iterable[Filing]) -> tuple[tuple[str, dict[str, str | int], int], ...]:
    """Key and timestamp filings as ``(key, record, timestamp)`` Kafka messages.

    The timestamp (epoch ms) is the filing's ``filed_at``, at the precision its source
    gives: the feed's time for live filings, the filing day for the master index.

    Args:
        filings: The filings to publish.

    Returns:
        One message per filing, keyed by a hash of its accession.
    """
    return tuple(
        (
            xxhash.xxh64(filing.accession).hexdigest(),
            {'accession': filing.accession, 'file_path': filing.file_path, 'timestamp': ts},
            ts,
        )
        for filing, ts in ((filing, int(filing.filed_at.timestamp() * 1000)) for filing in filings)
    )


if __name__ == '__main__':
    filings = EdgarClient(config.sec_user_agent)
    kafka = KafkaClient(config.kafka_broker_address, loglevel='CRITICAL')

    def produce(data: Iterable[Filing]) -> None:
        """Publish filings to the output topic one batch at a time (each produce call flushes)."""
        for batch in batched(build_messages(data), config.buffer_size, strict=False):
            kafka.produce(config.kafka_output_topic, batch)

    logger.info(f'Master index started in {config.mode} mode -> {config.kafka_output_topic}')

    if config.mode == 'live':
        # Each poll returns the newest filings again, so already-produced accessions are skipped
        seen: set[str] = set()
        for _ in range(config.live_iterations):
            new = [f for f in filings.fetch_current_filings(FORM_TYPE, config.num_pages) if f.accession not in seen]
            seen |= {filing.accession for filing in new}
            produce(new)
            time.sleep(config.secs_between_iterations)
    else:
        now = datetime.now(UTC)
        quarters = (
            tuple((year, q) for year in range(now.year - config.years, now.year + 1) for q in range(1, 5))
            if config.mode == 'historical'
            else ((now.year, (now.month - 1) // 3 + 1),)
        )
        for year, quarter in quarters:
            if (index := filings.fetch_master_index(year, quarter, FORM_TYPE)) is None:
                logger.warning(f'No master index for {year} QTR{quarter}')
                continue
            produce(index)

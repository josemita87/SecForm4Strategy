"""Entry point: fetches SEC Form 4 filing links and produces them to Kafka."""

import logging
import random
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import xxhash
from config import config
from secform4strategy_clients.edgar import EdgarClient
from secform4strategy_clients.messaging import KafkaClient

if TYPE_CHECKING:
    from secform4strategy_clients.ports import Filing, Filings

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)

# httpx logs every SEC request at INFO; progress is logged per batch instead
logging.getLogger('httpx').setLevel(logging.WARNING)

FORM_TYPE = '4'


def build_messages(filings: Iterable[Filing]) -> tuple[tuple[str, dict[str, str]], ...]:
    """Key filings as ``(key, record)`` messages.

    ``filed_at`` (ISO 8601) has the precision its source gives: the feed's time for live
    filings, the filing day for the master index.

    Args:
        filings: The filings to publish.

    Returns:
        One message per filing, keyed by a hash of its accession.
    """
    return tuple(
        (
            xxhash.xxh64(filing.accession).hexdigest(),
            {'accession': filing.accession, 'file_path': filing.file_path, 'filed_at': filing.filed_at.isoformat()},
        )
        for filing in filings
    )


if __name__ == '__main__':
    edgar: Filings = EdgarClient(config.sec_user_agent, config.sec_max_rate)
    kafka: KafkaClient = KafkaClient(config.kafka_broker_address, loglevel='CRITICAL')

    def produce(data: Iterable[Filing]) -> None:
        """Publish filings to the output topic one batch at a time (each produce call flushes)."""
        for start in range(0, len(messages := build_messages(data)), config.buffer_size):
            kafka.produce(config.kafka_output_topic, messages[start : start + config.buffer_size])
        logger.info(f'Published {len(messages)} filings')

    logger.info(f'Master index started in {config.mode} mode -> {config.kafka_output_topic}')

    if config.mode == 'live':
        # Each poll returns the newest filings again, so already-produced accessions are skipped
        seen: set[str] = set()
        for _ in range(config.live_iterations):
            new = [f for f in edgar.fetch_current_filings(FORM_TYPE, config.num_pages) if f.accession not in seen]
            seen |= {filing.accession for filing in new}
            produce(new)
            time.sleep(config.secs_between_iterations)
    else:
        now = datetime.now(UTC)
        match config.mode:
            case 'historical':
                quarters = tuple(
                    (year, q) for year in range(now.year - config.years, now.year + 1) for q in range(1, 5)
                )
            case 'quarter' if config.year and config.quarter:
                quarters = ((config.year, config.quarter),)
            case _:
                quarters = ((now.year, (now.month - 1) // 3 + 1),)
        for year, quarter in quarters:
            if (index := edgar.fetch_master_index(year, quarter, FORM_TYPE)) is None:
                logger.warning(f'No master index for {year} QTR{quarter}')
                continue
            produce(random.Random(0).sample(index, len(index)) if config.shuffle else index)

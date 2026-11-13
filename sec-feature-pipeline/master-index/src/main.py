"""Entry point: fetches SEC Form 4 filing links and sends them to the output queue."""

import logging
import random
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from itertools import batched
from typing import TYPE_CHECKING

import xxhash
from config import Kafka, Sqs, config
from secform4strategy_clients.edgar import EdgarClient
from secform4strategy_clients.messaging import KafkaClient
from secform4strategy_clients.ports import PermanentError, RetryableError
from secform4strategy_clients.sqs import SqsQueue

if TYPE_CHECKING:
    from secform4strategy_clients.ports import Filing, Filings, MessageQueue

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
    queue: MessageQueue
    match config.queue:
        case Kafka() as kafka:
            queue = KafkaClient(kafka.broker_address, loglevel='CRITICAL')
        case Sqs() as sqs:
            queue = SqsQueue(sqs.region)

    def publish(data: Iterable[Filing]) -> None:
        """Send filings to the output queue one batch at a time (each send flushes)."""
        for batch in batched(messages := build_messages(data), config.buffer_size, strict=False):
            queue.send(config.output_queue, batch)
        logger.info(f'Published {len(messages)} filings')

    logger.info(f'Master index started in {config.mode} mode -> {config.output_queue}')

    if config.mode == 'live':
        # Each poll returns the newest filings again, so already-produced accessions are skipped
        seen: set[str] = set()
        for _ in range(config.live_iterations):
            try:
                current = edgar.fetch_current_filings(FORM_TYPE, config.num_pages)
            except RetryableError as error:
                logger.warning(f'Poll failed, trying again next iteration: {error}')
            else:
                new = [filing for filing in current if filing.accession not in seen]
                seen |= {filing.accession for filing in new}
                publish(new)
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
        retry_later = []
        for year, quarter in quarters:
            try:
                index = edgar.fetch_master_index(year, quarter, FORM_TYPE)
            except PermanentError as error:
                logger.warning(f'No master index for {year} QTR{quarter}: {error}')
                continue
            except RetryableError as error:
                logger.error(f'Master index for {year} QTR{quarter} left for a retry: {error}')
                retry_later.append((year, quarter))
                continue
            publish(random.Random(0).sample(index, len(index)) if config.shuffle else index)
        # A non-zero exit has the scheduler run the task again; published quarters are upserted twice, harmlessly
        if retry_later:
            sys.exit(f'Quarters left for a retry: {retry_later}')

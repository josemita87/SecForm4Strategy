"""Entry point: fetches SEC Form 4 filing links and sends them to the output queue."""

import logging
import random
import sys
import time
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from itertools import batched
from typing import TYPE_CHECKING

import xxhash
from config import Kafka, Sqs, config
from secform4strategy_clients.edgar import EdgarClient
from secform4strategy_clients.messaging import KafkaClient
from secform4strategy_clients.ports import PermanentError, RetryableError
from secform4strategy_clients.sqs import SqsQueue

if TYPE_CHECKING:
    from secform4strategy_clients.ports import Filing, Filings, MessageQueue, SeenKeys

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


def quarters_between(start: date, end: date) -> tuple[tuple[int, int], ...]:
    """Return every ``(year, quarter)`` from ``start``'s quarter to ``end``'s, oldest first.

    Args:
        start: A day in the first quarter.
        end: A day in the last quarter.

    Returns:
        The quarters in order; empty if ``start`` is after ``end``.
    """
    first, last = (day.year * 4 + (day.month - 1) // 3 for day in (start, end))
    return tuple((index // 4, index % 4 + 1) for index in range(first, last + 1))


def filed_since(filings: Iterable[Filing], since: date | None) -> list[Filing]:
    """Keep the filings filed on or after ``since``; ``None`` keeps them all.

    Args:
        filings: The filings of one master index.
        since: The first filing day kept.

    Returns:
        The kept filings, in their original order.
    """
    return [filing for filing in filings if since is None or filing.filed_at.date() >= since]


def publish_new(filings: Iterable[Filing], seen: SeenKeys, queue: MessageQueue, output: str) -> int:
    """Send the filings ``seen`` does not hold to ``output``, then record them.

    Recording after sending means a failed send leaves the filings unrecorded, so the next poll
    sends them again; a failed record sends them twice, which the upserts and order IDs downstream absorb.

    Args:
        filings: The filings of one poll of the current-filings feed.
        seen: The accessions already sent.
        queue: Where the filings are sent.
        output: Queue the filings are sent to.

    Returns:
        How many filings were new.
    """
    by_accession = {filing.accession: filing for filing in filings}
    if new := seen.unseen(by_accession):
        queue.send(output, build_messages(by_accession[accession] for accession in new))
        seen.record(new)
    return len(new)


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
        today = datetime.now(UTC).date()
        since: date | None = None
        match config.mode:
            case 'historical':
                quarters = quarters_between(date(today.year - config.years, 1, 1), today)
            case 'quarter' if config.year and config.quarter:
                quarters = ((config.year, config.quarter),)
            case 'recent':
                quarters = quarters_between(since := today - timedelta(days=config.days_back), today)
            case _:
                quarters = quarters_between(today, today)
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
            kept = filed_since(index, since)
            publish(random.Random(0).sample(kept, len(kept)) if config.shuffle else kept)
        # A non-zero exit has the scheduler run the task again; published quarters are upserted twice, harmlessly
        if retry_later:
            sys.exit(f'Quarters left for a retry: {retry_later}')

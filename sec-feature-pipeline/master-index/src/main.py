"""Entry point: fetches SEC Form 4 filing links and produces them to Kafka."""

import logging
import time
from datetime import UTC, datetime
from itertools import batched
from typing import TYPE_CHECKING

import xxhash
from config import config
from secform4strategy_clients.edgar import EdgarClient
from secform4strategy_clients.messaging import KafkaClient

if TYPE_CHECKING:
    from typing import Any

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


def build_messages(filings: list[dict[str, str]]) -> list[tuple[str, dict[str, Any], int]]:
    """Key and timestamp filing records as ``(key, record, timestamp)`` Kafka messages.

    The timestamp is the filing's own ``date``, at the precision its source gives: a
    full ISO timestamp from the current-filings feed, a day (taken as UTC midnight)
    from the quarterly master index.

    Args:
        filings: Filing records with ``file_path`` and ``date``.

    Returns:
        One message per filing, keyed by a hash of its file path.
    """
    timestamps = [
        int(
            (
                filed if (filed := datetime.fromisoformat(filing['date'].strip())).tzinfo else filed.replace(tzinfo=UTC)
            ).timestamp()
            * 1000
        )
        for filing in filings
    ]
    return [
        (xxhash.xxh64(filing['file_path']).hexdigest(), {**filing, 'timestamp': ts}, ts)
        for filing, ts in zip(filings, timestamps, strict=True)
    ]


if __name__ == '__main__':
    filings = EdgarClient(headers=HEADERS)
    kafka = KafkaClient(config.kafka_broker_address, loglevel='CRITICAL')

    def produce(data: list[dict[str, str]]) -> None:
        """Publish filings to the output topic one batch at a time (each produce call flushes)."""
        for batch in batched(build_messages(data), config.buffer_size, strict=False):
            kafka.produce(config.kafka_output_topic, batch)

    logger.info(f'Master index started in {config.mode} mode -> {config.kafka_output_topic}')

    if config.mode == 'live':
        # Each poll returns the newest filings again, so already-produced accessions are skipped
        seen: set[str] = set()
        for _ in range(config.live_iterations):
            new = [f for f in filings.fetch_current_filings(FORM_TYPE, config.num_pages) if f['accession'] not in seen]
            seen |= {filing['accession'] for filing in new}
            produce(new)
            time.sleep(config.secs_between_iterations)
    else:
        now = datetime.now(UTC)
        quarters = (
            [(year, f'QTR{q}') for year in range(now.year - config.years, now.year + 1) for q in range(1, 5)]
            if config.mode == 'historical'
            else [(now.year, f'QTR{(now.month - 1) // 3 + 1}')]
        )
        for year, quarter in quarters:
            if (index := filings.fetch_master_index(year, quarter)) is None:
                logger.warning(f'No master index for {year} {quarter}')
                continue
            # The index lists a filing once per filer (issuer and each reporting owner): keep one per accession
            unique = {
                accession: filing | {'accession': accession}
                for filing in index
                if filing['form_type'] == FORM_TYPE
                and (accession := filing['file_path'].rsplit('/', 1)[-1].removesuffix('.txt'))
            }
            produce(list(unique.values()))

"""Lambda entry point for the live path: scrapes the filings of one SQS batch.

The image is the same as the batch task's; Lambda runs ``python -m awslambdaric handler.handle``
from ``src/`` instead of ``main.py``. The clients and the thread pool are built once per
execution environment, at import, and reused by every invocation it serves.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from config import Sqs, config
from main import scrape
from secform4strategy_clients.edgar import EdgarClient
from secform4strategy_clients.lambda_sqs import batch_failures, sqs_messages
from secform4strategy_clients.sqs import SqsQueue

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)
# The Lambda runtime installs its own log handler first, so main's basicConfig leaves the level at WARNING
logging.getLogger().setLevel(logging.INFO)

if not isinstance(config.queue, Sqs):
    raise TypeError('The Lambda handler sends to SQS: set QUEUE__BACKEND=sqs')
queue = SqsQueue(config.queue.region)
edgar = EdgarClient(config.sec_user_agent, config.sec_max_rate)
pool = ThreadPoolExecutor(config.fetch_workers)


def handle(event: Mapping[str, Any], _context: object) -> dict[str, list[dict[str, object]]]:
    """Scrape the event's filings and send their transactions; report the filings left for redelivery.

    Args:
        event: The SQS event, one record per filing.
        _context: The Lambda context, unused; Lambda passes it to every handler.

    Returns:
        The ``batchItemFailures`` response: the filings SQS delivers again.
    """
    messages = sqs_messages(event)
    done, sent = scrape(messages, edgar, queue, config.output_queue, config.max_age, pool)
    logger.info(f'{len(done)} of {len(messages)} filings -> {sent} transactions')
    return batch_failures(messages, done)

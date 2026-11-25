"""Lambda entry point for the live path: polls SEC's current-filings feed once and sends the new filings.

EventBridge Scheduler invokes it every minute of the trading day, and once before it with more feed
pages to collect the filings disclosed overnight. The image is the same as the batch task's; Lambda
runs ``python -m awslambdaric handler.handle`` from ``src/`` instead of ``main.py``. The clients
are built once per execution environment, at import, and reused by every invocation it serves.
"""

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from config import Sqs, config
from main import FORM_TYPE, PollEvent, publish_new
from secform4strategy_clients.dynamodb import DynamoSeenKeys
from secform4strategy_clients.edgar import EdgarClient
from secform4strategy_clients.sqs import SqsQueue

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)
# The Lambda runtime installs its own log handler first, so main's basicConfig leaves the level at WARNING
logging.getLogger().setLevel(logging.INFO)

if not isinstance(config.queue, Sqs) or config.seen_table is None:
    raise TypeError('The Lambda handler needs QUEUE__BACKEND=sqs and SEEN_TABLE')
queue = SqsQueue(config.queue.region)
seen = DynamoSeenKeys(config.queue.region, config.seen_table, int(timedelta(days=config.seen_days).total_seconds()))
edgar = EdgarClient(config.sec_user_agent, config.sec_max_rate)


def handle(event: Mapping[str, Any] | None, _context: object) -> dict[str, int]:
    """Poll the feed once and send the filings not sent before; a ``RetryableError`` fails the invocation.

    Args:
        event: The scheduler's event, if any; ``{"num_pages": n}`` reads ``n`` feed pages instead of ``NUM_PAGES``.
        _context: The Lambda context, unused; Lambda passes it to every handler.

    Returns:
        How many filings the feed listed and how many were new.
    """
    filings = edgar.fetch_current_filings(
        FORM_TYPE, PollEvent.model_validate(event or {}).num_pages or config.num_pages
    )
    new = publish_new(filings, seen, queue, config.output_queue)
    logger.info(f'{new} of {len(filings)} feed filings were new -> {config.output_queue}')
    if filings and new == len(filings):
        logger.warning('Every feed filing was new: older ones may lie past the pages read; raise num_pages')
    return {'listed': len(filings), 'new': new}

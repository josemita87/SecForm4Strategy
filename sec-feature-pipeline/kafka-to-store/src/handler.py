"""Lambda entry point for the live path: upserts the transactions of one SQS batch.

Each stored batch's tickers go to ``OUTPUT_QUEUE`` as one ``{"tickers": [...]}`` message; an
EventBridge Pipe starts the live-score state machine with it. The image is the same as the batch
task's; Lambda runs ``python -m awslambdaric handler.handle`` from ``src/`` instead of ``main.py``.
The clients are built once per execution environment, at import, and reused.
"""

import logging
from typing import TYPE_CHECKING, Any

from config import Sqs, config
from constants import Col, FeatureGroup
from main import store
from paths import SPEC_PATH
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.lambda_sqs import batch_failures, sqs_messages
from secform4strategy_clients.ports import RetryableError
from secform4strategy_clients.sqs import SqsQueue

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)
# The Lambda runtime installs its own log handler first, so main's basicConfig leaves the level at WARNING
logging.getLogger().setLevel(logging.INFO)

if not isinstance(config.queue, Sqs) or config.output_queue is None:
    raise TypeError('The Lambda handler needs QUEUE__BACKEND=sqs and OUTPUT_QUEUE')
queue = SqsQueue(config.queue.region)
feature_store = IcebergFeatureStore(
    load_catalog('default'),
    config.feature_store_namespace,
    load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
)
target = FeatureGroup.BI4 if config.system_inference else FeatureGroup.BT4


def handle(event: Mapping[str, Any], _context: object) -> dict[str, list[dict[str, object]]]:
    """Upsert the event's transactions and send their tickers on; a store failure redelivers them all.

    Args:
        event: The SQS event, one record per transaction.
        _context: The Lambda context, unused; Lambda passes it to every handler.

    Returns:
        The ``batchItemFailures`` response: the transactions SQS delivers again.
    """
    if not (messages := sqs_messages(event)):
        return batch_failures((), ())
    try:
        rows = store(messages, feature_store, target)
    except RetryableError as error:
        logger.warning(f'Store unavailable, {len(messages)} transactions left for redelivery: {error}')
        return batch_failures(messages, ())
    if (tickers := sorted(rows[Col.TICKER].astype(str).unique())) and config.output_queue:
        queue.send(config.output_queue, [(','.join(tickers), {'tickers': tickers})])
    return batch_failures(messages, messages)

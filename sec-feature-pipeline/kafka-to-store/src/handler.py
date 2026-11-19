"""Lambda entry point for the live path: upserts the transactions of one SQS batch.

The image is the same as the batch task's; Lambda runs ``python -m awslambdaric handler.handle``
from ``src/`` instead of ``main.py``. The feature store is opened once per execution
environment, at import, and reused by every invocation it serves.
"""

import logging
from typing import TYPE_CHECKING, Any

from config import config
from constants import FeatureGroup
from main import store
from paths import SPEC_PATH
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.lambda_sqs import batch_failures, sqs_messages
from secform4strategy_clients.ports import RetryableError

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)
# The Lambda runtime installs its own log handler first, so main's basicConfig leaves the level at WARNING
logging.getLogger().setLevel(logging.INFO)

feature_store = IcebergFeatureStore(
    load_catalog('default'),
    config.feature_store_namespace,
    load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
)
target = FeatureGroup.BI4 if config.system_inference else FeatureGroup.BT4


def handle(event: Mapping[str, Any], _context: object) -> dict[str, list[dict[str, object]]]:
    """Upsert the event's transactions; if the store is unavailable, report them all for redelivery.

    Args:
        event: The SQS event, one record per transaction.
        _context: The Lambda context, unused; Lambda passes it to every handler.

    Returns:
        The ``batchItemFailures`` response: the transactions SQS delivers again.
    """
    if not (messages := sqs_messages(event)):
        return batch_failures((), ())
    try:
        store(messages, feature_store, target)
    except RetryableError as error:
        logger.warning(f'Store unavailable, {len(messages)} transactions left for redelivery: {error}')
        return batch_failures(messages, ())
    return batch_failures(messages, messages)

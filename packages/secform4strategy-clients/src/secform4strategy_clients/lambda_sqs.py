"""SQS batches delivered to a Lambda function, as ``Message`` tuples.

The Lambda SQS event source receives the messages and deletes the ones the function finishes.
A handler reports the others in ``batchItemFailures``, so only they are delivered again; the
function needs ``ReportBatchItemFailures`` on its event source mapping.
"""

import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any

from secform4strategy_clients.records import Message

logger = logging.getLogger(__name__)


def sqs_messages(event: Mapping[str, Any]) -> tuple[Message, ...]:
    """Return the event's records as messages, each with its message ID as the receipt.

    A record whose body is not JSON can never be processed, so it is logged and left out; the
    event source then deletes it with the finished messages.

    Args:
        event: The SQS event Lambda passes to the handler.

    Returns:
        The messages, in the event's order.
    """
    messages = []
    for record in event['Records']:
        try:
            messages.append(Message(json.loads(record['body']), record['messageId']))
        except ValueError:
            logger.error(f'Dropping a message that is not JSON: {record["body"]!r:.200}')
    return tuple(messages)


def batch_failures(messages: Iterable[Message], done: Iterable[Message]) -> dict[str, list[dict[str, object]]]:
    """Return the handler's response: every message not in ``done`` is delivered again.

    Args:
        messages: The messages of the event.
        done: The messages the handler finished.

    Returns:
        The ``batchItemFailures`` response the SQS event source reads.
    """
    finished = {message.receipt for message in done}
    return {
        'batchItemFailures': [
            {'itemIdentifier': message.receipt} for message in messages if message.receipt not in finished
        ]
    }

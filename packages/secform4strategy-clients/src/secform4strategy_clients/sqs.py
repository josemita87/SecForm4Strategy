"""Amazon SQS client implementing the ``MessageQueue`` port.

Pure infrastructure: JSON bodies over standard queues addressed by name. A receipt is the
message's receipt handle. SQS hides a received message for the queue's visibility timeout and
delivers it again unless ``ack`` deletes it; after the queue's maximum receives, its redrive
policy moves it to the dead-letter queue. A message that is not JSON is logged and deleted at
once. botocore retries throttling and server errors itself, so the client only classifies the
failures left over with ``aws.classified`` (docs/error-handling.md).
"""

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from itertools import batched
from typing import TYPE_CHECKING, Any, NamedTuple

import boto3

from secform4strategy_clients.aws import RETRIES, classified
from secform4strategy_clients.ports import Message, PermanentError, RetryableError

if TYPE_CHECKING:
    from mypy_boto3_sqs import SQSClient
    from mypy_boto3_sqs.type_defs import BatchResultErrorEntryTypeDef

logger = logging.getLogger(__name__)

# SQS takes at most 10 entries per batch call
MAX_BATCH = 10


class SqsReceipt(NamedTuple):
    """The handle that deletes a received message."""

    handle: str


def raise_failed(failed: Sequence[BatchResultErrorEntryTypeDef], what: str) -> None:
    """Raise for the entries a batch call reports as failed, if any.

    Args:
        failed: The ``Failed`` entries of a batch response.
        what: What the call does, for the error message.

    Raises:
        PermanentError: Every failed entry is the sender's fault, so repeating cannot help.
        RetryableError: An entry failed on SQS's side.
    """
    if not failed:
        return
    reasons = sorted({entry['Code'] for entry in failed})
    if all(entry['SenderFault'] for entry in failed):
        raise PermanentError(f'{what}: {len(failed)} entries rejected: {reasons}')
    raise RetryableError(f'{what}: {len(failed)} entries failed: {reasons}')


class SqsQueue:
    """Send, receive and acknowledge JSON messages on SQS standard queues.

    Attributes:
        sqs: The SQS client, retrying throttling and server errors in botocore's standard mode.
    """

    def __init__(self, region: str, endpoint_url: str | None = None) -> None:
        """Open an SQS client; credentials come from the environment, as for any AWS SDK.

        Args:
            region: AWS region of the queues.
            endpoint_url: Alternative endpoint, e.g. a local SQS emulator; None uses AWS.
        """
        self.sqs: SQSClient = boto3.client('sqs', region_name=region, endpoint_url=endpoint_url, config=RETRIES)
        self._urls: dict[str, str] = {}

    def _url(self, queue: str) -> str:
        """Return a queue's URL, looked up once by name."""
        if (url := self._urls.get(queue)) is None:
            url = self._urls[queue] = classified(f'Look up {queue}', self.sqs.get_queue_url, QueueName=queue)[
                'QueueUrl'
            ]
        return url

    def send(self, queue: str, messages: Iterable[tuple[str, Mapping[str, Any]]]) -> None:
        """Send bodies as JSON, 10 per call; standard queues have no partitions, so keys are not used.

        Args:
            queue: Name of the queue.
            messages: Keys and JSON-serialisable bodies, shaped by the caller.
        """
        for batch in batched(messages, MAX_BATCH, strict=False):
            raise_failed(
                classified(
                    f'Send to {queue}',
                    self.sqs.send_message_batch,
                    QueueUrl=self._url(queue),
                    Entries=[{'Id': str(i), 'MessageBody': json.dumps(body)} for i, (_, body) in enumerate(batch)],
                ).get('Failed', []),
                f'Send to {queue}',
            )

    def receive(self, queue: str, max_messages: int, wait_seconds: float) -> tuple[Message, ...]:
        """Return up to ``max_messages`` messages, long-polling only for the first; then return what is already there.

        Args:
            queue: Name of the queue.
            max_messages: Most messages to return.
            wait_seconds: How long to wait for the first message (SQS waits at most 20 seconds).

        Returns:
            The messages received, possibly none.
        """
        messages: list[Message] = []
        while len(messages) < max_messages and (
            received := classified(
                f'Receive from {queue}',
                self.sqs.receive_message,
                QueueUrl=self._url(queue),
                MaxNumberOfMessages=min(MAX_BATCH, max_messages - len(messages)),
                # Once a batch has started, waiting for more would only delay the messages already received
                WaitTimeSeconds=0 if messages else int(min(wait_seconds, 20)),
            ).get('Messages', [])
        ):
            for record in received:
                try:
                    messages.append(Message(json.loads(record['Body']), SqsReceipt(record['ReceiptHandle'])))
                except ValueError:
                    logger.error(f'Deleting a message of {queue} that is not JSON: {record["Body"]!r:.200}')
                    self._delete(queue, [record['ReceiptHandle']])
        return tuple(messages)

    def ack(self, queue: str, messages: Iterable[Message]) -> None:
        """Delete the finished messages, 10 per call; the others come back after the visibility timeout.

        Args:
            queue: Name of the queue the messages were received from.
            messages: The finished messages.

        Raises:
            TypeError: A message was not received by an ``SqsQueue``.
        """
        handles = []
        for message in messages:
            if not isinstance(receipt := message.receipt, SqsReceipt):
                raise TypeError(f'Not an SqsQueue receipt: {receipt!r}')
            handles.append(receipt.handle)
        self._delete(queue, handles)

    def _delete(self, queue: str, handles: Sequence[str]) -> None:
        """Delete messages by receipt handle, 10 per call; no handles makes no call.

        Args:
            queue: Name of the queue.
            handles: Receipt handles of the messages.
        """
        for batch in batched(handles, MAX_BATCH, strict=False):
            raise_failed(
                classified(
                    f'Delete from {queue}',
                    self.sqs.delete_message_batch,
                    QueueUrl=self._url(queue),
                    Entries=[{'Id': str(i), 'ReceiptHandle': handle} for i, handle in enumerate(batch)],
                ).get('Failed', []),
                f'Delete from {queue}',
            )

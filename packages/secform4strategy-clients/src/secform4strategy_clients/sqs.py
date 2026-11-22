"""Amazon SQS client implementing the ``MessageQueue`` and ``Queues`` ports.

Pure infrastructure: JSON bodies over standard queues addressed by name. A receipt is the
message's receipt handle. SQS hides a received message for the queue's visibility timeout and
delivers it again unless ``ack`` deletes it; after the queue's maximum receives, its redrive
policy moves it to the dead-letter queue. A message that is not JSON is logged and deleted at
once. botocore retries throttling and server errors itself, so the client only classifies the
failures left over with ``aws.classified`` (docs/error-handling.md). SQS returns and deletes at
most 10 messages per call, so a large receive or ack runs its calls on a thread pool.
"""

import json
import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from itertools import batched
from typing import TYPE_CHECKING, Any, NamedTuple

import boto3
from botocore.config import Config

from secform4strategy_clients.aws import RETRIES, classified
from secform4strategy_clients.ports import PermanentError, RetryableError
from secform4strategy_clients.records import Message, QueueDepth

if TYPE_CHECKING:
    from mypy_boto3_sqs import SQSClient
    from mypy_boto3_sqs.type_defs import BatchResultErrorEntryTypeDef, MessageTypeDef

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
    """Send, receive and acknowledge JSON messages on SQS standard queues, and report their depths.

    Attributes:
        sqs: The SQS client, retrying throttling and server errors in botocore's standard mode.
    """

    def __init__(self, region: str, endpoint_url: str | None = None, workers: int = 10) -> None:
        """Open an SQS client; credentials come from the environment, as for any AWS SDK.

        Args:
            region: AWS region of the queues.
            endpoint_url: Alternative endpoint, e.g. a local SQS emulator; None uses AWS.
            workers: Receive and delete calls run at once; 1 makes them sequential.
        """
        self.sqs: SQSClient = boto3.client(
            'sqs',
            region_name=region,
            endpoint_url=endpoint_url,
            config=RETRIES.merge(Config(max_pool_connections=workers)),
        )
        self._urls: dict[str, str] = {}
        self._workers = workers
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='sqs')

    def _url(self, queue: str) -> str:
        """Return a queue's URL, looked up once by name."""
        if (url := self._urls.get(queue)) is None:
            url = self._urls[queue] = classified(f'Look up {queue}', self.sqs.get_queue_url, QueueName=queue)[
                'QueueUrl'
            ]
        return url

    def close(self) -> None:
        """Stop the thread pool and close the client's connections."""
        self._pool.shutdown()
        self.sqs.close()

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

        After the first call, each round runs up to ``workers`` calls at once, and the receive ends when a round
        returns nothing.

        Args:
            queue: Name of the queue.
            max_messages: Most messages to return.
            wait_seconds: How long to wait for the first message (SQS waits at most 20 seconds).

        Returns:
            The messages received, possibly none.
        """
        messages = self._parse(
            queue, self._receive_once(queue, min(MAX_BATCH, max_messages), int(min(wait_seconds, 20)))
        )
        while messages and (missing := max_messages - len(messages)) > 0:
            # Once a batch has started, waiting for more would only delay the messages already received
            sizes = [
                min(MAX_BATCH, missing - MAX_BATCH * i)
                for i in range(min(self._workers, math.ceil(missing / MAX_BATCH)))
            ]
            if not (
                received := [
                    record
                    for batch in self._pool.map(lambda size: self._receive_once(queue, size, 0), sizes)
                    for record in batch
                ]
            ):
                break
            messages.extend(self._parse(queue, received))
        return tuple(messages)

    def _receive_once(self, queue: str, size: int, wait: int) -> list[MessageTypeDef]:
        """Return the raw records of one receive call."""
        return classified(
            f'Receive from {queue}',
            self.sqs.receive_message,
            QueueUrl=self._url(queue),
            MaxNumberOfMessages=size,
            WaitTimeSeconds=wait,
        ).get('Messages', [])

    def _parse(self, queue: str, records: Iterable[MessageTypeDef]) -> list[Message]:
        """Return the JSON records as messages; a record that is not JSON is logged and deleted."""
        messages = []
        for record in records:
            try:
                messages.append(Message(json.loads(record['Body']), SqsReceipt(record['ReceiptHandle'])))
            except ValueError:
                logger.error(f'Deleting a message of {queue} that is not JSON: {record["Body"]!r:.200}')
                self._delete(queue, [record['ReceiptHandle']])
        return messages

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

    def depths(self, prefix: str) -> dict[str, QueueDepth]:
        """Return each queue whose name starts with ``prefix``, by name (a ``Queues`` read).

        Args:
            prefix: The queue-name prefix.

        Returns:
            Visible and in-flight messages per queue.
        """
        urls = classified(f'List queues {prefix}*', self.sqs.list_queues, QueueNamePrefix=prefix).get('QueueUrls', [])
        return {url.rsplit('/', 1)[1]: self._depth(url) for url in urls}

    def _depth(self, url: str) -> QueueDepth:
        """Read one queue's approximate counts."""
        attributes = classified(
            f'Read queue {url}',
            self.sqs.get_queue_attributes,
            QueueUrl=url,
            AttributeNames=['ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible'],
        )['Attributes']
        return QueueDepth(
            int(attributes['ApproximateNumberOfMessages']), int(attributes['ApproximateNumberOfMessagesNotVisible'])
        )

    def _delete(self, queue: str, handles: Sequence[str]) -> None:
        """Delete messages by receipt handle, 10 per call; no handles makes no call.

        Args:
            queue: Name of the queue.
            handles: Receipt handles of the messages.
        """
        if not handles:
            return
        url = self._url(queue)
        for failed in self._pool.map(
            lambda batch: classified(
                f'Delete from {queue}',
                self.sqs.delete_message_batch,
                QueueUrl=url,
                Entries=[{'Id': str(i), 'ReceiptHandle': handle} for i, handle in enumerate(batch)],
            ).get('Failed', []),
            batched(handles, MAX_BATCH, strict=False),
        ):
            raise_failed(failed, f'Delete from {queue}')

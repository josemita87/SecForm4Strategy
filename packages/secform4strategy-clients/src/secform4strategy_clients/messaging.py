"""Kafka client built on quixstreams, implementing the ``MessageQueue`` port.

Pure infrastructure: it sends JSON records and returns them as ``Message``s, with no field
parsing. A receipt is the message's ``(partition, offset)``. A Kafka commit covers every earlier
offset of its partition, so ``ack`` commits only up to the first message left unacknowledged and
seeks back to it: the next ``receive`` delivers it again, as a queue would. A message that is not
JSON can never be processed, so it is logged and counts as acknowledged.
"""

import json
import logging
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, NamedTuple

from confluent_kafka import KafkaException, TopicPartition
from quixstreams import Application

from secform4strategy_clients.ports import Message

if TYPE_CHECKING:
    from quixstreams.kafka import Consumer
    from quixstreams.models import Topic

logger = logging.getLogger(__name__)


class KafkaReceipt(NamedTuple):
    """Where a received message sits in its topic."""

    partition: int
    offset: int


class KafkaClient:
    """Manage a quixstreams Application for consuming and producing messages.

    Attributes:
        app: The quixstreams Application bound to the configured broker.
    """

    def __init__(
        self,
        broker_address: str,
        consumer_group: str | None = None,
        auto_offset_reset: str | None = None,
        loglevel: str | None = None,
        consumer_extra_config: Mapping[str, object] | None = None,
    ) -> None:
        """Initialize the underlying quixstreams Application.

        Only the arguments relevant to a given use are needed: a producer-only
        client can omit ``consumer_group``/``auto_offset_reset``; a consumer
        passes them. When consuming and no ``consumer_extra_config`` is given,
        ``enable.auto.offset.store`` is enabled by default.

        Args:
            broker_address: Kafka broker address.
            consumer_group: Consumer group id (required to consume).
            auto_offset_reset: Offset reset policy (e.g. ``"earliest"``).
            loglevel: Optional quixstreams log level (e.g. ``"CRITICAL"``).
            consumer_extra_config: Extra consumer config passed to the Application.
        """
        if consumer_extra_config is None and consumer_group is not None:
            consumer_extra_config = {'enable.auto.offset.store': True}
        app_kwargs = {
            'consumer_group': consumer_group,
            'auto_offset_reset': auto_offset_reset,
            'loglevel': loglevel,
            'consumer_extra_config': consumer_extra_config,
        }
        # Only forward the options given, so quixstreams keeps its own defaults for the rest
        self.app = Application(broker_address=broker_address, **{k: v for k, v in app_kwargs.items() if v is not None})
        self._topics: dict[str, Topic] = {}
        self._consumers: dict[str, Consumer] = {}
        self._received: dict[str, list[KafkaReceipt]] = {}
        self._undecodable: dict[str, set[KafkaReceipt]] = {}

    def receive(self, queue: str, max_messages: int, wait_seconds: float) -> tuple[Message, ...]:
        """Return up to ``max_messages`` messages, waiting only for the first; then return what is already there.

        Args:
            queue: Topic to read; one manual-commit consumer is kept per topic.
            max_messages: Most messages to return.
            wait_seconds: How long to wait for the first message.

        Returns:
            The messages read, possibly none.

        Raises:
            KafkaException: The poll returned a broker error instead of a message.
        """
        if (consumer := self._consumers.get(queue)) is None:
            consumer = self._consumers[queue] = self.app.get_consumer(auto_commit_enable=False)
            consumer.subscribe(topics=[queue])
        messages: list[Message] = []
        # Once a batch has started, waiting for more would only delay the messages already read
        while len(messages) < max_messages and (record := consumer.poll(0 if messages else wait_seconds)) is not None:
            if error := record.error():
                raise KafkaException(error)
            receipt = KafkaReceipt(record.partition(), record.offset())
            self._received.setdefault(queue, []).append(receipt)
            try:
                messages.append(Message(json.loads(record.value()), receipt))
            except TypeError, ValueError:
                logger.error(f'Dropping a message of {queue} that is not JSON at {receipt}: {record.value()!r:.200}')
                self._undecodable.setdefault(queue, set()).add(receipt)
        return tuple(messages)

    def ack(self, queue: str, messages: Iterable[Message]) -> None:
        """Commit, per partition, up to the first message received but not acknowledged, and seek back to it.

        Args:
            queue: Topic the messages were read from.
            messages: The finished messages among those received since the last ``ack`` on this topic.

        Raises:
            TypeError: A message was not received by a ``KafkaClient``.
            ValueError: A message was not received since the last ``ack`` on this topic.
            KafkaException: The broker failed to commit a partition.
        """
        acked = self._undecodable.pop(queue, set())
        for message in messages:
            if not isinstance(receipt := message.receipt, KafkaReceipt):
                raise TypeError(f'Not a KafkaClient receipt: {receipt!r}')
            acked.add(receipt)
        if unknown := acked - set(received := self._received.pop(queue, [])):
            raise ValueError(f'Not received from {queue} since the last ack: {sorted(unknown)}')
        end: dict[int, int] = {}
        first_unacked: dict[int, int] = {}
        for receipt in sorted(received):
            end[receipt.partition] = receipt.offset + 1
            if receipt not in acked:
                first_unacked.setdefault(receipt.partition, receipt.offset)
        if not end:
            return
        consumer = self._consumers[queue]
        committed = consumer.commit(
            offsets=[
                TopicPartition(queue, partition, first_unacked.get(partition, offset))
                for partition, offset in end.items()
            ],
            asynchronous=False,
        )
        if failed := [partition for partition in committed or () if partition.error]:
            raise KafkaException(f'Commit failed on {queue}: {failed}')
        for partition, offset in first_unacked.items():
            consumer.seek(TopicPartition(queue, partition, offset))

    def close(self) -> None:
        """Close every consumer, so it leaves its group and the next member gets its partitions at once."""
        for consumer in self._consumers.values():
            consumer.close()
        self._consumers.clear()

    def send(self, queue: str, messages: Iterable[tuple[str, Mapping[str, Any]]]) -> None:
        """Publish ``(key, body)`` pairs to a topic and flush, so they are stored when this returns.

        Args:
            queue: Topic to publish to.
            messages: Keys and JSON-serialisable bodies, shaped by the caller.
        """
        if (topic := self._topics.get(queue)) is None:
            topic = self._topics[queue] = self.app.topic(name=queue, value_serializer='json', key_serializer='string')
        with self.app.get_producer() as producer:
            for key, body in messages:
                serialized = topic.serialize(key=key, value=body)
                producer.produce(topic=topic.name, value=serialized.value, key=serialized.key)
            producer.flush()

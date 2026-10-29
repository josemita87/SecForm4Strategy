"""Kafka client built on quixstreams.

Pure infrastructure: one client per external service. ``KafkaClient`` covers both
consuming and producing. The consumer returns raw messages as a list of dicts and
performs no field parsing, DataFrame construction or cleaning. The producer
serializes and publishes records the caller has already shaped (keys, timestamps
and any value transformations are the caller's responsibility).
"""

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


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
        consumer_extra_config: dict | None = None,
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
        # Lazy import so the SDK is only required when this client is built.
        from quixstreams import Application

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
        self._produce_topics: dict = {}
        # Lazily created persistent manual-commit consumer (see consume_with_offsets).
        self._consumer = None
        self._input_topic = None

    def consume_batch(
        self,
        topic_name: str,
        buffer_size: int,
        timeout: int,
        auto_commit_enable: bool = True,
    ) -> tuple[bool, list]:
        """Consume messages from a topic and return one batch of raw records.

        Args:
            topic_name: The Kafka topic to consume from.
            buffer_size: Number of messages to accumulate before returning a batch.
            timeout: Poll timeout (seconds) for each consumer poll.
            auto_commit_enable: Whether the consumer auto-commits offsets.

        Returns:
            A tuple ``(finished, records)``. ``finished`` is ``True`` when the
            topic is exhausted (poll returned nothing) and ``False`` when the
            buffer filled up. ``records`` is a list of decoded message values.
        """
        topic = self.app.topic(name=topic_name, value_deserializer='json', key_deserializer='string')

        buffer: list = []
        with self.app.get_consumer(auto_commit_enable=auto_commit_enable) as consumer:
            consumer.subscribe(topics=[topic.name])

            while True:
                if not (message := consumer.poll(timeout)):
                    return True, buffer

                buffer.append(json.loads(message.value().decode('utf-8')))

                if len(buffer) >= buffer_size:
                    return False, buffer

    def consume_with_offsets(self, topic_name: str, buffer_size: int, timeout: int) -> list:
        """Consume a batch of ``(record, offset)`` pairs for manual-commit workflows.

        Unlike ``consume_batch``, this keeps a single persistent consumer with
        auto-commit disabled and surfaces each message's offset, so the caller can
        commit (via ``commit_offset``) only after the batch is successfully
        processed. Returns once the buffer is full, or once a poll returns nothing
        and the buffer is non-empty (it keeps polling while the buffer is empty).

        Args:
            topic_name: The Kafka topic to consume from.
            buffer_size: Number of messages to accumulate before returning.
            timeout: Poll timeout (seconds) for each consumer poll.

        Returns:
            A list of ``(record, offset)`` tuples.
        """
        if self._consumer is None:
            self._input_topic = self.app.topic(name=topic_name, value_deserializer='json', key_deserializer='string')
            self._consumer = self.app.get_consumer(auto_commit_enable=False)
            self._consumer.subscribe(topics=[self._input_topic.name])

        buffer: list = []
        while True:
            if (message := self._consumer.poll(timeout)) is None:
                if buffer:
                    return buffer
                continue

            try:
                buffer.append((json.loads(message.value().decode('utf-8')), message.offset()))
            except Exception:
                continue

            if len(buffer) >= buffer_size:
                return buffer

    def commit_offset(self, offset: int, partition: int = 0) -> None:
        """Commit ``offset + 1`` on the manual consumer's input-topic partition.

        Must be called after ``consume_with_offsets`` has created the consumer.

        Args:
            offset: Offset of the last processed message; the committed position
                is one past it.
            partition: Partition index to commit on.
        """
        from confluent_kafka import TopicPartition

        tp = TopicPartition(topic=self._input_topic.name, partition=partition, offset=offset + 1)
        self._consumer.commit(offsets=[tp])

    def produce(self, topic_name: str, messages: Iterable[tuple[object, dict, object]]) -> None:
        """Produce a batch of messages to a topic and flush.

        The caller owns all message shaping; this method only serializes and
        publishes. One producer is opened and flushed per call, so callers that
        batch should call this once per batch.

        Args:
            topic_name: The Kafka topic to publish to.
            messages: An iterable of ``(key, value, timestamp)`` tuples, where
                ``value`` is the record dict and ``timestamp`` is epoch millis (or
                ``None`` to let the broker assign one).
        """
        if (topic := self._produce_topics.get(topic_name)) is None:
            topic = self._produce_topics[topic_name] = self.app.topic(
                name=topic_name, value_serializer='json', key_serializer='string'
            )

        with self.app.get_producer() as producer:
            for key, value, timestamp in messages:
                serialized = topic.serialize(key=key, value=value)
                producer.produce(
                    topic=topic.name,
                    value=serialized.value,
                    key=serialized.key,
                    timestamp=timestamp,
                )
            producer.flush()

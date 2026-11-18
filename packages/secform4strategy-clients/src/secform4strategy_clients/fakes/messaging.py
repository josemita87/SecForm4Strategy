"""Fake of the ``MessageQueue`` port: in-memory queues that record what is sent and redeliver what is not acked."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from secform4strategy_clients.ports import Message

if TYPE_CHECKING:
    from secform4strategy_clients.ports import MessageQueue


@dataclass
class FakeMessageQueue:
    """In-memory queues; a message's receipt is its position in its receive.

    Messages received and left out of the next ``ack`` go back to the front of their queue.

    Attributes:
        pending: Queue -> bodies not yet received.
        sent: Queue -> every ``(key, body)`` sent.
        acked: Queue -> every message acknowledged.
        in_flight: Queue -> the messages of its last receive.
    """

    pending: dict[str, list[Mapping[str, Any]]] = field(default_factory=dict)
    sent: dict[str, list[tuple[str, Mapping[str, Any]]]] = field(default_factory=dict)
    acked: dict[str, list[Message]] = field(default_factory=dict)
    in_flight: dict[str, tuple[Message, ...]] = field(default_factory=dict)

    def send(self, queue: str, messages: Iterable[tuple[str, Mapping[str, Any]]]) -> None:
        """Record the messages as sent to ``queue``."""
        self.sent.setdefault(queue, []).extend(messages)

    def receive(self, queue: str, max_messages: int, wait_seconds: float) -> tuple[Message, ...]:
        """Return up to ``max_messages`` pending bodies of ``queue`` as messages."""
        bodies, self.pending[queue] = (
            self.pending.get(queue, [])[:max_messages],
            self.pending.get(queue, [])[max_messages:],
        )
        self.in_flight[queue] = tuple(Message(body, receipt) for receipt, body in enumerate(bodies))
        return self.in_flight[queue]

    def ack(self, queue: str, messages: Iterable[Message]) -> None:
        """Record the messages as acknowledged, and put the rest of the last receive back in the queue."""
        self.acked.setdefault(queue, []).extend(acked := list(messages))
        unacked = [message.body for message in self.in_flight.pop(queue, ()) if message not in acked]
        self.pending[queue] = unacked + self.pending.get(queue, [])


_conforms: MessageQueue = FakeMessageQueue()

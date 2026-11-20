"""An in-memory ``SeenKeys``."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from secform4strategy_clients.ports import SeenKeys


@dataclass
class FakeSeenKeys:
    """Keys held in a set, never expiring.

    Attributes:
        keys: The keys recorded.
    """

    keys: set[str] = field(default_factory=set)

    def unseen(self, keys: Iterable[str]) -> list[str]:
        """Return the keys not recorded yet, in their given order."""
        return [key for key in dict.fromkeys(keys) if key not in self.keys]

    def record(self, keys: Iterable[str]) -> None:
        """Record the keys."""
        self.keys |= set(keys)


_conforms: SeenKeys = FakeSeenKeys()

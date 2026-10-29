"""Ports: the contracts services depend on.

Services annotate their dependencies with these Protocols, never with a concrete
client, so swapping a backend (Hopsworks for S3, local disk for a bucket) is a new
adapter and no service change. The clients satisfy them structurally.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from typing import Any, TypeGuard

    import pandas as pd

#: Read filter: column -> required value; an iterable (non-string) value means "any of".
Filters = Mapping[str, object]


def is_many(value: object) -> TypeGuard[Iterable[object]]:
    """Return whether a filter value means "any of" (a non-string iterable) rather than equality.

    Args:
        value: A filter value.

    Returns:
        True for a non-string iterable.
    """
    return isinstance(value, Iterable) and not isinstance(value, str | bytes)


class FeatureStore(Protocol):
    """Versioned tables of features, read and upserted by reference."""

    def read(self, reference: str, where: Filters | None = None) -> pd.DataFrame:
        """Read the referenced table, optionally filtered."""
        ...

    def push(self, reference: str, data: pd.DataFrame) -> object:
        """Upsert ``data`` into the referenced table."""
        ...


class ModelStore(Protocol):
    """Storage for one trained model and its metadata."""

    def save(self, model: object, metadata: Mapping[str, Any]) -> None:
        """Persist the model together with its metadata."""
        ...

    def load(self) -> tuple[Any, dict[str, Any]]:
        """Return the stored model and its metadata."""
        ...


class Broker(Protocol):
    """Order placement."""

    def place_order(self, symbol: str, qty: str, side: str, order_type: str, time_in_force: str) -> object | None:
        """Submit an order; return it, or None if submission failed."""
        ...


class MarketData(Protocol):
    """Market-data lookups by ticker symbol."""

    def market_cap(self, symbol: str) -> int | None:
        """Return the market capitalization for ``symbol``."""
        ...

    def exchange(self, symbol: str) -> str | None:
        """Return the listing exchange for ``symbol``."""
        ...

    def close_histories(self, symbols: Iterable[str], starts: Mapping[str, object] | None = None) -> pd.DataFrame:
        """Return daily close history (``date``, ``close``, ``ticker``) for many symbols."""
        ...


class HttpResponse(Protocol):
    """The slice of an HTTP response that callers read."""

    @property
    def status_code(self) -> int:
        """HTTP status code."""
        ...

    @property
    def text(self) -> str:
        """Decoded body."""
        ...

    @property
    def content(self) -> bytes:
        """Raw body."""
        ...


class Filings(Protocol):
    """SEC filings source."""

    def get(self, url: str) -> HttpResponse:
        """GET a filing URL."""
        ...

    def fetch_master_index(self, year: int, quarter: str) -> pd.DataFrame | None:
        """Return a quarterly master index of filings, or None if unavailable."""
        ...

    def fetch_live_links(self, num_pages: int) -> set[str]:
        """Return the newest filing paths."""
        ...

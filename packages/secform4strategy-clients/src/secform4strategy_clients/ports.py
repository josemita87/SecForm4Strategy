"""Ports: the contracts services depend on, and the records, order vocabulary and errors they exchange.

Services annotate their dependencies with these Protocols, never with a concrete
client, so swapping a backend (a local catalog for AWS Glue, local disk for a bucket) is a new
adapter and no service change. The clients satisfy them structurally. Adapters retry transient
failures, then raise ``RetryableError`` or ``PermanentError`` (docs/error-handling.md).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, Protocol, TypeGuard

if TYPE_CHECKING:
    import pandas as pd

#: Read filter: column -> required value; an iterable (non-string) value means "any of".
Filters = Mapping[str, object]

#: Order vocabulary, as Alpaca spells it; ``opg``/``cls`` are the opening and closing auctions.
Side = Literal['buy', 'sell']
OrderType = Literal['market', 'limit', 'stop', 'stop_limit', 'trailing_stop']
TimeInForce = Literal['day', 'gtc', 'opg', 'cls', 'ioc', 'fok']


class RetryableError(Exception):
    """The operation failed for now, after the adapter's own retries, and is safe to repeat later."""


class PermanentError(Exception):
    """The operation fails however often it is repeated."""


class Filing(NamedTuple):
    """One SEC filing: its accession number, archive path of the full submission, and when it was filed."""

    accession: str
    file_path: str
    filed_at: datetime


class Message(NamedTuple):
    """One received message: its decoded JSON body, and the receipt that acknowledges it.

    The receipt is opaque to services; only the client that issued it reads it.
    """

    body: Mapping[str, Any]
    receipt: object


@dataclass(frozen=True, slots=True)
class Scores:
    """How a predictor did on the test set: error, and the mean return captured per short (positive is a profit)."""

    mae: float
    short_return: float
    n_shorts: int


@dataclass(frozen=True, slots=True)
class ModelMetadata:
    """The facts saved with a trained model to reproduce, audit and score with it.

    ``features`` is the column order the model's input arrays follow; ``metrics`` holds the
    model and its baselines by name, scored on the holdout. The defaulted fields record the
    walk-forward validation, per fold, and the threshold the shorts were scored at.
    """

    trained_at: datetime
    git_sha: str
    sklearn_version: str
    feature_group_version: int
    target: str
    features: tuple[str, ...]
    model_params: Mapping[str, bool | int | float | str]
    train_rows: int
    test_rows: int
    test_start: datetime
    metrics: Mapping[str, Scores]
    prediction_threshold: float | None = None
    validation_mae: tuple[float, ...] = ()
    validation_zero_mae: tuple[float, ...] = ()


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

    def save(self, model: object, metadata: ModelMetadata) -> None:
        """Persist the model together with its metadata."""
        ...

    def load(self) -> tuple[Any, ModelMetadata]:
        """Return the stored model and its metadata."""
        ...


class Broker(Protocol):
    """Order placement, idempotent by client order id."""

    def place_order(
        self,
        symbol: str,
        qty: float,
        side: Side,
        order_type: OrderType,
        time_in_force: TimeInForce,
        client_order_id: str,
    ) -> object | None:
        """Submit an order; return it, or None if an order with ``client_order_id`` was placed before."""
        ...


class MessageQueue(Protocol):
    """Named queues of JSON messages, delivered at least once.

    A consumer calls ``ack`` once after each ``receive``, with the messages it finished. The
    messages it leaves out are delivered again.
    """

    def send(self, queue: str, messages: Iterable[tuple[str, Mapping[str, Any]]]) -> None:
        """Send ``(key, body)`` pairs to the queue."""
        ...

    def receive(self, queue: str, max_messages: int, wait_seconds: float) -> tuple[Message, ...]:
        """Return up to ``max_messages`` messages, waiting up to ``wait_seconds`` for the first; empty if none came."""
        ...

    def ack(self, queue: str, messages: Iterable[Message]) -> None:
        """Mark the messages as processed, so they are not delivered again."""
        ...


class SeenKeys(Protocol):
    """Keys already handled, remembered for a while, so a source that repeats itself is handled once."""

    def unseen(self, keys: Iterable[str]) -> list[str]:
        """Return the keys not recorded yet, in their given order."""
        ...

    def record(self, keys: Iterable[str]) -> None:
        """Record the keys as handled."""
        ...


class MarketData(Protocol):
    """Daily price history by ticker symbol."""

    def price_histories(self, symbols: Iterable[str], starts: Mapping[str, date] | None = None) -> pd.DataFrame:
        """Return daily bars (``date``, adjusted ``open``/``close``, ``close_as_traded``, ``split``, ``dividend``)."""
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
        """GET a filing URL; any answer but 200 raises ``RetryableError`` or ``PermanentError``."""
        ...

    def fetch_master_index(self, year: int, quarter: int, form_type: str) -> tuple[Filing, ...]:
        """Return a quarter's filings of ``form_type``, one per accession."""
        ...

    def fetch_current_filings(self, form_type: str, num_pages: int = 1) -> tuple[Filing, ...]:
        """Return the newest filings of ``form_type``, one per accession."""
        ...

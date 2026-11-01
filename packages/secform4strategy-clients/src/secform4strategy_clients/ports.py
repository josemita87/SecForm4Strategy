"""Ports: the contracts services depend on, and the records and order vocabulary they exchange.

Services annotate their dependencies with these Protocols, never with a concrete
client, so swapping a backend (a local catalog for AWS Glue, local disk for a bucket) is a new
adapter and no service change. The clients satisfy them structurally.
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


class Filing(NamedTuple):
    """One SEC filing: its accession number, archive path of the full submission, and when it was filed."""

    accession: str
    file_path: str
    filed_at: datetime


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
    model and its baselines by name.
    """

    trained_at: datetime
    git_sha: str
    sklearn_version: str
    feature_group_version: int
    target: str
    features: tuple[str, ...]
    model_params: Mapping[str, float]
    train_rows: int
    test_rows: int
    test_start: datetime
    metrics: Mapping[str, Scores]


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
    """Order placement."""

    def place_order(
        self, symbol: str, qty: float, side: Side, order_type: OrderType, time_in_force: TimeInForce
    ) -> object | None:
        """Submit an order; return it, or None if submission failed."""
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
        """GET a filing URL."""
        ...

    def fetch_master_index(self, year: int, quarter: int, form_type: str) -> tuple[Filing, ...] | None:
        """Return a quarter's filings of ``form_type``, one per accession, or None if unavailable."""
        ...

    def fetch_current_filings(self, form_type: str, num_pages: int = 1) -> tuple[Filing, ...]:
        """Return the newest filings of ``form_type``, one per accession."""
        ...

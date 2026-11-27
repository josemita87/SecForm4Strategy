"""Ports: the contracts services depend on, their order vocabulary and filters, and the errors adapters raise.

Services annotate their dependencies with these Protocols, never with a concrete
client, so swapping a backend (a local catalog for AWS Glue, local disk for a bucket) is a new
adapter and no service change. The clients satisfy them structurally. Adapters retry transient
failures, then raise ``RetryableError`` or ``PermanentError`` (docs/error-handling.md). The records
the ports exchange are in ``records``.
"""

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeGuard

if TYPE_CHECKING:
    import pandas as pd

    from secform4strategy_clients.records import (
        BrokerAccount,
        BrokerOrder,
        BrokerPosition,
        EquityPoint,
        Execution,
        ExecutionSummary,
        Filing,
        FunctionMetrics,
        HistoryEvent,
        MarketSession,
        Message,
        ModelMetadata,
        QueueDepth,
    )

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


class UntradableError(PermanentError):
    """The broker does not trade, or does not short, the symbol."""


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

    def read(self, reference: str, where: Filters | None = None, columns: Iterable[str] | None = None) -> pd.DataFrame:
        """Read the referenced table, optionally filtered and with only ``columns``."""
        ...

    def push(self, reference: str, data: pd.DataFrame) -> object:
        """Upsert ``data`` into the referenced table."""
        ...

    def replace(self, reference: str, data: pd.DataFrame, where: Filters) -> None:
        """Delete the rows ``where`` selects and append ``data`` in their place, in one commit."""
        ...


class ModelFiles(Protocol):
    """The model store's pointer and metadata, read without the model itself."""

    def latest_version(self) -> str | None:
        """Return the version the pointer names, or None when no model was promoted."""
        ...

    def metadata(self, version: str) -> ModelMetadata:
        """Return a version's metadata."""
        ...


class ModelStore(ModelFiles, Protocol):
    """Versioned trained models with their metadata, and a pointer to the promoted one that ``load`` reads."""

    def save(self, model: object, metadata: ModelMetadata) -> str:
        """Persist the model together with its metadata as a new version, without promoting it; return the version."""
        ...

    def promote(self, version: str) -> None:
        """Point the store at a saved version, so ``load`` returns it."""
        ...

    def load(self) -> tuple[Any, ModelMetadata]:
        """Return the promoted model and its metadata."""
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


class TradingAccount(Protocol):
    """The broker account: balances, positions, orders and equity history."""

    @property
    def paper(self) -> bool:
        """Whether this is a paper-trading account."""
        ...

    def account(self) -> BrokerAccount:
        """Return the balances and status."""
        ...

    def positions(self) -> tuple[BrokerPosition, ...]:
        """Return the open positions."""
        ...

    def orders(self, limit: int) -> tuple[BrokerOrder, ...]:
        """Return up to ``limit`` orders of any status, newest first."""
        ...

    def equity_history(self, period: str, timeframe: str) -> tuple[EquityPoint, ...]:
        """Return the equity over ``period`` at ``timeframe`` resolution, oldest first."""
        ...


class MarketCalendar(Protocol):
    """The exchange's trading sessions, with holidays left out and early closes at their own time."""

    def sessions(self, start: date, end: date) -> tuple[MarketSession, ...]:
        """Return the sessions from ``start`` to ``end``, both included, oldest first, as tz-aware times."""
        ...


class RecentRows(Protocol):
    """Feature-group rows from an event time on, with only the columns asked for."""

    def read_since(self, reference: str, since: datetime, columns: Iterable[str]) -> pd.DataFrame:
        """Return the rows whose event time is at or after ``since``; none, with ``columns``, for a missing table."""
        ...


class Site(Protocol):
    """A static site's data files, by name."""

    def read(self, name: str) -> bytes | None:
        """Return a data file's content, or None when it does not exist."""
        ...

    def write(self, name: str, body: bytes) -> None:
        """Write a data file as JSON the browser must not cache."""
        ...


class Executions(Protocol):
    """Step Functions executions and their histories."""

    def latest(self, state_machine_arn: str, limit: int) -> tuple[ExecutionSummary, ...]:
        """Return up to ``limit`` executions, newest first."""
        ...

    def started_since(self, state_machine_arn: str, since: datetime) -> tuple[ExecutionSummary, ...]:
        """Return every execution started at or after ``since``, newest first."""
        ...

    def describe(self, execution_arn: str) -> Execution:
        """Return an execution with its input, output and error."""
        ...

    def history(self, execution_arn: str, newest_first: bool, limit: int) -> tuple[HistoryEvent, ...]:
        """Return up to ``limit`` history events."""
        ...


class Schedules(Protocol):
    """EventBridge Scheduler schedules."""

    def enabled(self, name: str) -> bool:
        """Return whether the schedule exists and is enabled."""
        ...


class Queues(Protocol):
    """Queue depths."""

    def depths(self, prefix: str) -> dict[str, QueueDepth]:
        """Return each queue whose name starts with ``prefix``, by name."""
        ...


class Metrics(Protocol):
    """CloudWatch metrics of Lambda functions."""

    def functions(self, names: Iterable[str], since: datetime, until: datetime) -> dict[str, FunctionMetrics]:
        """Return each function's invocations, errors and p90 duration over the window, by name."""
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

"""Records: the fixed-shape data the ports and adapters exchange."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, NamedTuple

#: Execution statuses Step Functions reports.
RunStatus = Literal['RUNNING', 'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED', 'PENDING_REDRIVE']


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


class Execution(NamedTuple):
    """One state-machine execution as DescribeExecution reports it; input and output are JSON text."""

    arn: str
    name: str
    status: RunStatus
    started_at: datetime
    stopped_at: datetime | None
    input: str | None
    output: str | None
    error: str | None
    cause: str | None


class ExecutionSummary(NamedTuple):
    """One execution as ListExecutions reports it."""

    arn: str
    name: str
    status: RunStatus
    started_at: datetime
    stopped_at: datetime | None


class HistoryEvent(NamedTuple):
    """One execution-history event: its type, time, and the state it entered or exited, if any."""

    type: str
    timestamp: datetime
    state: str | None


class QueueDepth(NamedTuple):
    """Messages waiting in a queue, and messages received but not yet deleted."""

    visible: int
    in_flight: int


class FunctionMetrics(NamedTuple):
    """One Lambda function's invocations and errors over a window, and its 90th-percentile duration."""

    invocations: int
    errors: int
    p90_duration_ms: float | None


class BrokerAccount(NamedTuple):
    """The broker account's balances and status."""

    equity: float
    cash: float
    buying_power: float
    status: str


class BrokerPosition(NamedTuple):
    """One open position; ``qty`` is negative for a short."""

    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float | None
    market_value: float | None
    unrealized_pl: float | None
    unrealized_plpc: float | None


class BrokerOrder(NamedTuple):
    """One broker order; ``client_order_id`` is the row key inference placed it under."""

    id: str
    client_order_id: str
    symbol: str
    side: str
    qty: float | None
    status: str
    submitted_at: datetime | None
    filled_at: datetime | None
    filled_avg_price: float | None


class EquityPoint(NamedTuple):
    """The account's equity at one time."""

    t: datetime
    equity: float


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
    walk-forward validation, per fold, the threshold the shorts were scored at, and the thresholds
    the holdout is reported at.
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
    report_thresholds: tuple[float, ...] = ()

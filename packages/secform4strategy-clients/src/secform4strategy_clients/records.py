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
    """How a predictor did on the holdout: how well it ranks short winners, and the shorts it takes at the cutoff.

    A captured return is minus the actual return, so positive is a profit; ``short_return`` is their mean, 0.0
    without shorts. A None score is undefined on the holdout (no shorts, or one class) or predates the field.
    """

    short_return: float
    n_shorts: int
    average_precision: float | None = None
    roc_auc: float | None = None
    precision: float | None = None
    median_return: float | None = None
    trimmed_return: float | None = None


class CutoffShorts(NamedTuple):
    """The holdout shorts at one probability cutoff: their count, and mean and median captured return (None if none)."""

    cutoff: float
    n_shorts: int
    mean_return: float | None
    median_return: float | None


@dataclass(frozen=True, slots=True)
class ModelMetadata:
    """The facts saved with a trained model to reproduce, audit and score with it.

    ``features`` is the column order the model's input arrays follow; ``metrics`` holds the model and its
    baselines by name, scored on the holdout. The defaulted fields are None or empty in models saved before
    them: the probability cutoff inference shorts at, the return a short winner falls below, the holdout's
    share of short winners, the per-fold validation average precision and share of short winners, and the
    holdout shorts at each cutoff of the grid.
    """

    trained_at: datetime
    git_sha: str
    sklearn_version: str
    feature_group_version: int
    target: str
    features: tuple[str, ...]
    model_params: Mapping[str, bool | int | float | str | None]
    train_rows: int
    test_rows: int
    test_start: datetime
    metrics: Mapping[str, Scores]
    probability_cutoff: float | None = None
    label_return: float | None = None
    base_rate: float | None = None
    validation_ap: tuple[float, ...] = ()
    validation_base_rate: tuple[float, ...] = ()
    cutoff_sweep: tuple[CutoffShorts, ...] = ()

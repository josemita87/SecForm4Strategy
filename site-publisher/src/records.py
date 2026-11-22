"""The published records of every data file, and what they share: their config and UTC timestamps."""

from datetime import UTC, date, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, PlainSerializer
from secform4strategy_clients.records import RunStatus


def _aware(moment: datetime) -> datetime:
    """Reject a naive timestamp, whose zone the site could only guess.

    Args:
        moment: A timestamp.

    Returns:
        The timestamp, unchanged.

    Raises:
        ValueError: If it has no time zone.
    """
    if moment.tzinfo is None:
        raise ValueError(f'Timestamp {moment} has no time zone')
    return moment


#: A time-zone-aware timestamp, written as ISO 8601 UTC to the second, e.g. ``2026-11-23T15:02:11Z``.
UtcTime = Annotated[
    datetime,
    AfterValidator(_aware),
    PlainSerializer(lambda moment: moment.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'), return_type=str),
]


class Record(BaseModel):
    """A published record: immutable, and NaN or infinity written as JSON ``null``."""

    model_config = ConfigDict(frozen=True, ser_json_inf_nan='null')


class DataFile(Record):
    """A data file's top-level record, stamped with when it was built."""

    generated_at: UtcTime


# live.json
class Step(Record):
    """One state the run entered: when, and how it ended."""

    name: str
    started_at: UtcTime
    stopped_at: UtcTime | None
    status: RunStatus


class OrderFill(Record):
    """The Alpaca order placed for a filing."""

    id: str
    status: str
    filled_avg_price: float | None


class Prediction(Record):
    """One row of inference's ``predictions`` output."""

    key: str
    ticker: str
    accepted_at: UtcTime
    prediction: float | None = None


class LiveFiling(Prediction):
    """One scored row: its prediction, whether it was shorted, and its order."""

    shorted: bool
    order: OrderFill | None


class Run(Record):
    """One live-score execution."""

    id: str
    status: RunStatus
    started_at: UtcTime
    stopped_at: UtcTime | None
    tickers: tuple[str, ...]
    steps: tuple[Step, ...]
    error: str | None
    filings: tuple[LiveFiling, ...]


class Live(DataFile):
    """live.json."""

    prediction_threshold: float
    poller_enabled: bool
    runs: tuple[Run, ...]


# health.json
class Switches(Record):
    """What runs: the stack (the publisher itself runs only while it is on), the live poller, the weekly retrain."""

    stack: bool
    live_poller: bool
    weekly_retrain: bool


class QueueHealth(Record):
    """One queue's messages waiting and in flight, and its dead-letter queue's depth."""

    name: str
    depth: int
    in_flight: int
    dlq_depth: int | None


class LambdaHealth(Record):
    """One live Lambda's last hour."""

    name: str
    invocations_1h: int
    errors_1h: int
    p90_duration_ms: float | None


class RunCounts(Record):
    """Live-score runs of the last 24 hours by outcome; failed counts timeouts and aborts too."""

    succeeded: int
    failed: int
    running: int


class Retrain(Record):
    """The latest retrain execution and the state it is in, or ended in."""

    name: str
    status: RunStatus
    step: str | None
    started_at: UtcTime
    stopped_at: UtcTime | None


class ModelInfo(Record):
    """The model the live path trades with."""

    version: str
    trained_at: UtcTime
    git_sha: str


class Health(DataFile):
    """health.json."""

    switches: Switches
    queues: tuple[QueueHealth, ...]
    lambdas: tuple[LambdaHealth, ...]
    live_runs_24h: RunCounts
    retrain: Retrain | None
    model: ModelInfo | None


# portfolio.json
class AccountSummary(Record):
    """The account's balances and status."""

    equity: float
    cash: float
    buying_power: float
    status: str
    paper: bool


class Equity(Record):
    """The account's equity at one time."""

    t: UtcTime
    equity: float


class Position(Record):
    """One open position; a short has a negative quantity."""

    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float | None
    market_value: float | None
    unrealized_pl: float | None
    unrealized_plpc: float | None


class Order(Record):
    """One order of any status."""

    id: str
    client_order_id: str
    symbol: str
    side: str
    qty: float | None
    status: str
    submitted_at: UtcTime | None
    filled_at: UtcTime | None
    filled_avg_price: float | None


class Portfolio(DataFile):
    """portfolio.json."""

    account: AccountSummary
    equity_history: tuple[Equity, ...]
    positions: tuple[Position, ...]
    orders: tuple[Order, ...]


# model.json
class Fold(Record):
    """One walk-forward fold: the chosen model's error against predicting no move."""

    fold: int
    model_mae: float
    zero_mae: float


class Metric(Record):
    """A predictor's holdout error and the mean return of the shorts it takes."""

    mae: float
    short_return: float
    n_shorts: int


class ThresholdRow(Record):
    """The holdout shorts at one threshold; ``mean_return`` is null when none is taken."""

    threshold: float
    n_shorts: int
    mean_return: float | None


class CumulativePoint(Record):
    """Cumulative return by entry date: each short summed once, and the benchmark shorted over the same windows."""

    date: date
    strategy: float
    spy: float | None


class Importance(Record):
    """A feature's permutation importance: the rise in holdout MAE when it is shuffled."""

    feature: str
    importance: float


class ModelReport(DataFile):
    """model.json."""

    version: str
    trained_at: UtcTime
    git_sha: str
    params: dict[str, bool | int | float | str]
    train_rows: int
    test_rows: int
    test_start: UtcTime
    prediction_threshold: float
    folds: tuple[Fold, ...]
    metrics: dict[str, Metric]
    thresholds: tuple[ThresholdRow, ...]
    cumulative: tuple[CumulativePoint, ...]
    importance: tuple[Importance, ...]


# explore.json
class Sale(Record):
    """One insider sale."""

    ticker: str
    insider: str | None
    role: str | None
    shares: float
    value: float
    accepted_at: UtcTime


class Cluster(Record):
    """A ticker sold by several insiders within the window."""

    ticker: str
    insiders: int
    value: float
    first_accepted_at: UtcTime
    last_accepted_at: UtcTime


class Explore(DataFile):
    """explore.json."""

    largest_sales_7d: tuple[Sale, ...]
    clusters_7d: tuple[Cluster, ...]

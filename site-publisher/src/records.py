"""The published records of every data file, and what they share: their config and UTC timestamps."""

from datetime import UTC, date, datetime
from typing import Annotated

from constants import Skip
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
    """One row of inference's ``predictions`` output: its probability of a short winner, None in older runs."""

    key: str
    ticker: str
    accepted_at: UtcTime
    probability: float | None = None


class LiveFiling(Prediction):
    """One scored row: its probability, whether it was shorted, and its order."""

    shorted: bool
    order: OrderFill | None


class Run(Record):
    """One live-score execution."""

    id: str
    status: RunStatus
    started_at: UtcTime
    stopped_at: UtcTime | None
    # From the output, or from the messages that started the run when the output names none
    tickers: tuple[str, ...]
    steps: tuple[Step, ...]
    error: str | None
    # Why the run scored nothing; None for runs that scored, are running or failed
    skip: Skip | None
    # The cutoff inference shorted at; None for runs that scored nothing, or predate the classifier
    probability_cutoff: float | None
    filings: tuple[LiveFiling, ...]


class Live(DataFile):
    """live.json."""

    probability_cutoff: float | None
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
    """One walk-forward fold: the chosen model's average precision against the fold's share of short winners."""

    fold: int
    model_ap: float
    base_rate: float


class Metric(Record):
    """A predictor's holdout ranking scores and the captured returns of the shorts it takes at the cutoff."""

    average_precision: float | None
    roc_auc: float | None
    precision: float | None
    short_return: float
    median_return: float | None
    trimmed_return: float | None
    n_shorts: int


class CutoffRow(Record):
    """The holdout shorts at one probability cutoff; the returns are null when none is taken."""

    cutoff: float
    n_shorts: int
    mean_return: float | None
    median_return: float | None


class CumulativePoint(Record):
    """Cumulative return by entry date: each short summed once, and the benchmark shorted over the same windows."""

    date: date
    strategy: float
    spy: float | None


class Importance(Record):
    """A feature's permutation importance: the fall in holdout average precision when it is shuffled."""

    feature: str
    importance: float


class ModelReport(DataFile):
    """model.json."""

    version: str
    trained_at: UtcTime
    git_sha: str
    params: dict[str, bool | int | float | str | None]
    train_rows: int
    test_rows: int
    test_start: UtcTime
    probability_cutoff: float
    label_return: float | None
    base_rate: float | None
    folds: tuple[Fold, ...]
    metrics: dict[str, Metric]
    cutoffs: tuple[CutoffRow, ...]
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

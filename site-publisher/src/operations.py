"""The per-minute reads of what the system is doing now, building live.json, health.json and portfolio.json.

The reads are AWS state machines, schedules, queues and Lambda metrics, and the Alpaca account.
"""

import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, NamedTuple

from constants import Skip
from records import (
    AccountSummary,
    Equity,
    Health,
    LambdaHealth,
    Live,
    LiveFiling,
    ModelInfo,
    Order,
    OrderFill,
    Portfolio,
    Position,
    Prediction,
    QueueHealth,
    Retrain,
    Run,
    RunCounts,
    Step,
    Switches,
)

if TYPE_CHECKING:
    from secform4strategy_clients.ports import Executions, Metrics, ModelFiles, Queues, Schedules, TradingAccount
    from secform4strategy_clients.records import (
        BrokerAccount,
        BrokerOrder,
        BrokerPosition,
        EquityPoint,
        Execution,
        ExecutionSummary,
        FunctionMetrics,
        HistoryEvent,
        ModelMetadata,
        QueueDepth,
        RunStatus,
    )

logger = logging.getLogger(__name__)


# live.json


class RunSource(NamedTuple):
    """What one run is built from: the execution and its history, oldest event first."""

    execution: Execution
    events: tuple[HistoryEvent, ...]


def steps(events: Iterable[HistoryEvent], status: RunStatus) -> tuple[Step, ...]:
    """Return the states a run entered, in order; a state not exited yet takes the run's status.

    Args:
        events: The run's history, oldest first.
        status: The run's status.

    Returns:
        One step per state entered.
    """
    entered: dict[str, HistoryEvent] = {}
    exited: dict[str, HistoryEvent] = {}
    for event in events:
        if event.state is not None and event.type.endswith('StateEntered'):
            entered[event.state] = event
        elif event.state is not None and event.type.endswith('StateExited'):
            exited[event.state] = event
    return tuple(
        Step(
            name=name,
            started_at=start.timestamp,
            stopped_at=end.timestamp if (end := exited.get(name)) else None,
            status='SUCCEEDED' if end else status,
        )
        for name, start in entered.items()
    )


def started_tickers(input_text: str | None) -> tuple[str, ...]:
    """Return the tickers of the SQS messages that started the run.

    Args:
        input_text: The execution input: the Pipe's SQS records, each body ``{"tickers": [...]}``.

    Returns:
        The distinct tickers, in order.
    """
    payload = json.loads(input_text) if input_text else []
    records = payload if isinstance(payload, list) else [payload]
    bodies = [json.loads(body) if isinstance(body := record.get('body', record), str) else body for record in records]
    return tuple(dict.fromkeys(ticker for body in bodies for ticker in body.get('tickers', ())))


def tickers(input_text: str | None, output: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the run's tickers: from its output, or from the SQS messages that started it when the output names none.

    Args:
        input_text: The execution input: the Pipe's SQS records, each body ``{"tickers": [...]}``.
        output: The parsed execution output; empty while running or after a failure.

    Returns:
        The distinct tickers, in order.
    """
    return tuple(output.get('tickers', ())) or started_tickers(input_text)


def skip(input_text: str | None, output: Mapping[str, Any]) -> Skip | None:
    """Return why a finished run scored nothing.

    Target outputs no tickers for a filing without a sale; inference returns its tickers unscored when no row is fresh.

    Args:
        input_text: The execution input: the Pipe's SQS records, each body ``{"tickers": [...]}``.
        output: The parsed execution output, inference's payload; empty while running or after a failure.

    Returns:
        ``NO_SALE``, ``STALE``, or None when the run scored, is running or failed.
    """
    if 'tickers' not in output:
        return None
    if not output['tickers']:
        return Skip.NO_SALE if started_tickers(input_text) else None
    return Skip.STALE if output.get('scored') == 0 and not output.get('predictions') else None


def error_message(error: str | None, cause: str | None, limit: int = 300) -> str | None:
    """Return a short message for a failed run: the error name and the handler's message.

    ARNs and 12-digit account IDs become ``…``, because every site viewer reads the message.

    Args:
        error: The execution's error name, such as ``States.TaskFailed``.
        cause: The cause: a Lambda error as JSON with ``errorMessage``, or plain text.
        limit: Longest message kept.

    Returns:
        The message, or None when the run has no error.
    """
    if not error and not cause:
        return None
    try:
        detail = json.loads(cause or '').get('errorMessage', cause)
    except ValueError, AttributeError:
        detail = cause
    return re.sub(
        r'arn:aws[\w-]*:[^\s"\']+|\b\d{12}\b',
        '…',
        ': '.join(part for part in (error, detail) if part),
    )[:limit]


def filings(output: Mapping[str, Any], orders: Mapping[str, BrokerOrder]) -> tuple[LiveFiling, ...]:
    """Return the filings inference scored, each with its short flag and the order placed under its key.

    Args:
        output: The parsed execution output, inference's payload; outputs before ``predictions`` was added have none.
        orders: Alpaca orders by client order id.

    Returns:
        One filing per prediction.
    """
    shorts = set(output.get('shorts', ()))
    return tuple(
        LiveFiling(
            **prediction.model_dump(),
            shorted=prediction.key in shorts,
            order=OrderFill(id=order.id, status=order.status, filled_avg_price=order.filled_avg_price)
            if (order := orders.get(prediction.key))
            else None,
        )
        for prediction in map(Prediction.model_validate, output.get('predictions', ()))
    )


def run(source: RunSource, orders: Mapping[str, BrokerOrder]) -> Run:
    """Return one live-score run as the site shows it.

    Args:
        source: The execution and its history.
        orders: Alpaca orders by client order id.

    Returns:
        The run.
    """
    execution = source.execution
    output = json.loads(execution.output) if execution.output else {}
    return Run(
        id=execution.name,
        status=execution.status,
        started_at=execution.started_at,
        stopped_at=execution.stopped_at,
        tickers=tickers(execution.input, output),
        steps=steps(source.events, execution.status),
        error=error_message(execution.error, execution.cause),
        skip=skip(execution.input, output),
        probability_cutoff=output.get('probability_cutoff'),
        filings=filings(output, orders),
    )


def live_report(
    generated_at: datetime,
    poller_enabled: bool,
    sources: Sequence[RunSource],
    orders: Iterable[BrokerOrder],
) -> Live:
    """Return live.json from the runs, newest first, and the account's orders.

    The report's probability cutoff is the newest run's that reports one: the cutoff inference shorts at now.

    Args:
        generated_at: When the report is built.
        poller_enabled: Whether the live poller's schedule is enabled.
        sources: The runs, newest first.
        orders: Alpaca orders; a filing's order is the one whose client order id is its key.

    Returns:
        The report.
    """
    by_key = {order.client_order_id: order for order in orders}
    runs = tuple(run(source, by_key) for source in sources)
    return Live(
        generated_at=generated_at,
        probability_cutoff=next((r.probability_cutoff for r in runs if r.probability_cutoff is not None), None),
        poller_enabled=poller_enabled,
        runs=runs,
    )


class LiveSpec(NamedTuple):
    """Where live.json reads from: the state machine and the poller schedule, and how many runs."""

    state_machine_arn: str
    poller_schedule: str
    runs: int


def build_live(
    now: datetime,
    spec: LiveSpec,
    executions: Executions,
    schedules: Schedules,
    orders: Callable[[], Iterable[BrokerOrder]],
) -> Live:
    """Read the newest runs, their histories and the account's orders, and build live.json.

    The runs are described in parallel threads; Alpaca failing leaves the filings without orders.

    Args:
        now: When the report is built.
        spec: Where to read from.
        executions: Step Functions executions.
        schedules: EventBridge schedules.
        orders: The Alpaca account's latest orders.

    Returns:
        The report.
    """
    summaries = executions.latest(spec.state_machine_arn, spec.runs)
    with ThreadPoolExecutor(max_workers=8) as pool:
        sources = tuple(
            pool.map(
                lambda arn: RunSource(executions.describe(arn), executions.history(arn, False, 1000)),
                (summary.arn for summary in summaries),
            )
        )
    try:
        placed = orders()
    except Exception:
        logger.exception('Alpaca orders unavailable; live.json lists the filings without their orders')
        placed = ()
    return live_report(now, schedules.enabled(spec.poller_schedule), sources, placed)


# health.json

#: Suffix of a dead-letter queue's name.
DLQ_SUFFIX = '-dlq'


def queues(depths: Mapping[str, QueueDepth], prefix: str) -> tuple[QueueHealth, ...]:
    """Return each main queue with its dead-letter queue's depth, named without the prefix.

    Args:
        depths: Every queue's depth by full name, dead-letter queues included.
        prefix: The name prefix to strip, such as ``secform4strategy-``.

    Returns:
        One entry per main queue, sorted by name; ``dlq_depth`` is None for a queue without one.
    """
    return tuple(
        QueueHealth(
            name=name.removeprefix(prefix),
            depth=depth.visible,
            in_flight=depth.in_flight,
            dlq_depth=dlq.visible if (dlq := depths.get(f'{name}{DLQ_SUFFIX}')) else None,
        )
        for name, depth in sorted(depths.items())
        if not name.endswith(DLQ_SUFFIX)
    )


def lambdas(metrics: Mapping[str, FunctionMetrics], prefix: str) -> tuple[LambdaHealth, ...]:
    """Return each function's last hour, named without the prefix.

    Args:
        metrics: Metrics by full function name.
        prefix: The name prefix to strip, such as ``secform4strategy-live-``.

    Returns:
        One entry per function, in the given order.
    """
    return tuple(
        LambdaHealth(
            name=name.removeprefix(prefix),
            invocations_1h=metric.invocations,
            errors_1h=metric.errors,
            p90_duration_ms=metric.p90_duration_ms,
        )
        for name, metric in metrics.items()
    )


def run_counts(runs: Iterable[ExecutionSummary]) -> RunCounts:
    """Count runs by outcome.

    Args:
        runs: The runs.

    Returns:
        Succeeded, failed (timed out and aborted included) and running.
    """
    statuses = [run.status for run in runs]
    return RunCounts(
        succeeded=statuses.count('SUCCEEDED'),
        failed=sum(status in {'FAILED', 'TIMED_OUT', 'ABORTED'} for status in statuses),
        running=statuses.count('RUNNING'),
    )


def current_step(events_newest_first: Iterable[HistoryEvent]) -> str | None:
    """Return the state the run entered last.

    Args:
        events_newest_first: History events, newest first.

    Returns:
        The state's name, or None before the first state.
    """
    return next(
        (event.state for event in events_newest_first if event.type.endswith('StateEntered') and event.state), None
    )


def retrain(latest: ExecutionSummary | None, events_newest_first: Iterable[HistoryEvent]) -> Retrain | None:
    """Return the latest retrain and its current step.

    Args:
        latest: The latest retrain execution, if any.
        events_newest_first: Its history, newest first.

    Returns:
        The retrain, or None when none ran.
    """
    if latest is None:
        return None
    return Retrain(
        name=latest.name,
        status=latest.status,
        step=current_step(events_newest_first),
        started_at=latest.started_at,
        stopped_at=latest.stopped_at,
    )


def model_info(version: str | None, metadata: ModelMetadata | None) -> ModelInfo | None:
    """Return the current model's version, training time and commit.

    Args:
        version: The version ``latest.json`` names, if any.
        metadata: That version's metadata.

    Returns:
        The model, or None when none was saved.
    """
    if version is None or metadata is None:
        return None
    return ModelInfo(version=version, trained_at=metadata.trained_at, git_sha=metadata.git_sha[:7])


class HealthSpec(NamedTuple):
    """Where health.json reads from."""

    name_prefix: str
    live_functions: tuple[str, ...]
    live_score_state_machine_arn: str
    retrain_state_machine_arn: str
    poller_schedule: str
    retrain_schedule: str


def build_health(
    now: datetime,
    spec: HealthSpec,
    executions: Executions,
    schedules: Schedules,
    queue_depths: Queues,
    metrics: Metrics,
    models: ModelFiles,
    metadata: Callable[[str], ModelMetadata],
) -> Health:
    """Read the stack's state and build health.json.

    Args:
        now: When the report is built.
        spec: Where to read from.
        executions: Step Functions executions.
        schedules: EventBridge schedules.
        queue_depths: SQS queues.
        metrics: CloudWatch metrics.
        models: The model store's pointer.
        metadata: A version's metadata; the caller may cache it, as a saved version never changes.

    Returns:
        The report.
    """
    latest = next(iter(executions.latest(spec.retrain_state_machine_arn, 1)), None)
    version = models.latest_version()
    live_prefix = f'{spec.name_prefix}-live-'
    return Health(
        generated_at=now,
        switches=Switches(
            stack=True,
            live_poller=schedules.enabled(spec.poller_schedule),
            weekly_retrain=schedules.enabled(spec.retrain_schedule),
        ),
        queues=queues(queue_depths.depths(f'{spec.name_prefix}-'), f'{spec.name_prefix}-'),
        lambdas=lambdas(
            metrics.functions((f'{live_prefix}{name}' for name in spec.live_functions), now - timedelta(hours=1), now),
            live_prefix,
        ),
        live_runs_24h=run_counts(executions.started_since(spec.live_score_state_machine_arn, now - timedelta(days=1))),
        retrain=retrain(latest, executions.history(latest.arn, True, 100) if latest else ()),
        model=model_info(version, metadata(version) if version else None),
    )


# portfolio.json


def portfolio_report(
    generated_at: datetime,
    account: BrokerAccount,
    paper: bool,
    history: Iterable[EquityPoint],
    positions: Iterable[BrokerPosition],
    orders: Iterable[BrokerOrder],
) -> Portfolio:
    """Return portfolio.json, positions by symbol and orders newest first.

    Args:
        generated_at: When the report is built.
        account: Balances and status.
        paper: Whether it is a paper account.
        history: Equity over time, oldest first.
        positions: Open positions.
        orders: Orders of any status.

    Returns:
        The report.
    """
    return Portfolio(
        generated_at=generated_at,
        account=AccountSummary(**account._asdict(), paper=paper),
        equity_history=tuple(Equity(**point._asdict()) for point in history),
        positions=tuple(Position(**position._asdict()) for position in sorted(positions, key=lambda p: p.symbol)),
        orders=tuple(
            Order(**order._asdict())
            for order in sorted(orders, key=lambda o: o.submitted_at or generated_at, reverse=True)
        ),
    )


class PortfolioSpec(NamedTuple):
    """The equity history's period and resolution."""

    equity_period: str
    equity_timeframe: str


def build_portfolio(
    now: datetime, spec: PortfolioSpec, account: TradingAccount, orders: Callable[[], Iterable[BrokerOrder]]
) -> Portfolio:
    """Read the Alpaca account and build portfolio.json.

    Args:
        now: When the report is built.
        spec: The equity history's period and resolution.
        account: The Alpaca account.
        orders: Its latest orders.

    Returns:
        The report.
    """
    return portfolio_report(
        now,
        account.account(),
        account.paper,
        account.equity_history(spec.equity_period, spec.equity_timeframe),
        account.positions(),
        orders(),
    )

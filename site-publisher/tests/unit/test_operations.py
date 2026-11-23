"""live.json, health.json and portfolio.json: runs and their filings, stack health, the Alpaca account.

Under test:
    site-publisher/src/operations.py
"""

import json
from datetime import UTC, datetime, timedelta

from constants import Skip
from operations import (
    RunSource,
    current_step,
    error_message,
    filings,
    lambdas,
    live_report,
    model_info,
    portfolio_report,
    queues,
    retrain,
    run_counts,
    skip,
    steps,
    tickers,
)
from records import QueueHealth
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
    Scores,
)

T0 = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)

# live.json

KEY = 'DBX@2026-11-23T14:30Z'
ORDER = BrokerOrder('o-1', KEY, 'DBX', 'sell', 1.0, 'filled', T0, T0, 27.1)
OUTPUT = {
    'tickers': ['DBX', 'TGT'],
    'scored': 2,
    'shorts': [KEY],
    'probability_cutoff': 0.55,
    'predictions': [
        {'key': KEY, 'ticker': 'DBX', 'accepted_at': '2026-11-23T14:30:00Z', 'probability': 0.71},
        {'key': 'TGT@2026-11-23T14:31Z', 'ticker': 'TGT', 'accepted_at': '2026-11-23T14:31:00Z', 'probability': 0.2},
    ],
}
INPUT = json.dumps([{'body': json.dumps({'tickers': ['DBX']})}, {'body': json.dumps({'tickers': ['TGT', 'DBX']})}])


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def history(*states: tuple[str, int, int | None]) -> tuple[HistoryEvent, ...]:
    """Entered and (optionally) exited events per state, with the task events between them."""
    events = [HistoryEvent('ExecutionStarted', T0, None)]
    for name, entered, exited in states:
        entered_at = at(entered)
        events += [HistoryEvent('TaskStateEntered', entered_at, name), HistoryEvent('TaskScheduled', entered_at, None)]
        if exited is not None:
            exited_at = at(exited)
            events += [HistoryEvent('TaskSucceeded', exited_at, None), HistoryEvent('TaskStateExited', exited_at, name)]
    return tuple(events)


def execution(status: str, output: dict[str, object] | None, error: str | None = None, cause: str | None = None):
    return Execution(
        'arn:x', 'run-1', status, T0, None if status == 'RUNNING' else at(30), INPUT,
        json.dumps(output) if output is not None else None, error, cause,
    )  # fmt: skip


class TestSteps:
    """Each state entered becomes a step; one not exited takes the run's status."""

    def test_a_finished_run_has_every_step_succeeded_in_order(self) -> None:
        result = steps(history(('yahoo-connect', 0, 3), ('target', 3, 7), ('inference', 7, 12)), 'SUCCEEDED')
        assert [(s.name, s.status, s.started_at, s.stopped_at) for s in result] == [
            ('yahoo-connect', 'SUCCEEDED', at(0), at(3)),
            ('target', 'SUCCEEDED', at(3), at(7)),
            ('inference', 'SUCCEEDED', at(7), at(12)),
        ]

    def test_the_step_in_progress_is_running(self) -> None:
        result = steps(history(('yahoo-connect', 0, 3), ('target', 3, None)), 'RUNNING')
        assert [(s.name, s.status, s.stopped_at) for s in result] == [
            ('yahoo-connect', 'SUCCEEDED', at(3)),
            ('target', 'RUNNING', None),
        ]

    def test_the_step_that_failed_takes_the_run_status(self) -> None:
        result = steps(history(('yahoo-connect', 0, 3), ('target', 3, 7), ('inference', 7, None)), 'FAILED')
        assert result[-1].status == 'FAILED'

    def test_no_state_entered_yet_has_no_steps(self) -> None:
        assert steps(history(), 'RUNNING') == ()


class TestTickers:
    """Tickers come from the output, or from the SQS messages while there is none."""

    def test_from_the_output(self) -> None:
        assert tickers(INPUT, OUTPUT) == ('DBX', 'TGT')

    def test_from_the_input_messages_distinct_in_order(self) -> None:
        assert tickers(INPUT, {}) == ('DBX', 'TGT')

    def test_from_a_plain_input(self) -> None:
        assert tickers('{"tickers": ["AMZN"]}', {}) == ('AMZN',)

    def test_no_input_no_tickers(self) -> None:
        assert tickers(None, {}) == ()

    def test_empty_output_falls_back_to_the_input(self) -> None:
        assert tickers(INPUT, {'tickers': [], 'scored': 0}) == ('DBX', 'TGT')


class TestSkip:
    """A finished run that scored nothing names why: no sale in the filing, or no fresh row."""

    def test_no_output_tickers_is_no_sale(self) -> None:
        assert skip(INPUT, {'tickers': [], 'scored': 0, 'shorts': [], 'predictions': []}) is Skip.NO_SALE

    def test_tickers_unscored_is_stale(self) -> None:
        assert skip(INPUT, {'tickers': ['DBX'], 'scored': 0, 'shorts': [], 'predictions': []}) is Skip.STALE

    def test_scored_run_is_not_skipped(self) -> None:
        assert skip(INPUT, OUTPUT) is None

    def test_running_or_failed_run_is_not_skipped(self) -> None:
        assert skip(INPUT, {}) is None

    def test_no_tickers_anywhere_is_not_skipped(self) -> None:
        assert skip(None, {'tickers': [], 'scored': 0}) is None


class TestErrorMessage:
    """A failure reads as the error name and the handler's message."""

    def test_lambda_cause_gives_its_error_message(self) -> None:
        cause = json.dumps({'errorMessage': 'Orders left for a retry', 'errorType': 'RetryableError'})
        assert error_message('RetryableError', cause) == 'RetryableError: Orders left for a retry'

    def test_plain_cause_is_kept(self) -> None:
        assert error_message('States.Timeout', 'took too long') == 'States.Timeout: took too long'

    def test_long_messages_are_cut(self) -> None:
        assert len(error_message('E', 'x' * 1000) or '') == 300

    def test_no_error(self) -> None:
        assert error_message(None, None) is None

    def test_arns_and_account_ids_are_redacted(self) -> None:
        cause = json.dumps({'errorMessage': 'AccessDenied on arn:aws:s3:::bucket/key for account 123456789012'})
        assert error_message('States.TaskFailed', cause) == 'States.TaskFailed: AccessDenied on … for account …'


class TestFilings:
    """Each prediction is a filing, flagged when shorted and matched to its order by key."""

    def test_shorted_filing_carries_its_order(self) -> None:
        dbx, tgt = filings(OUTPUT, {KEY: ORDER})
        assert (dbx.shorted, dbx.probability) == (True, 0.71)
        assert dbx.order is not None
        assert (dbx.order.id, dbx.order.status, dbx.order.filled_avg_price) == ('o-1', 'filled', 27.1)
        assert (tgt.shorted, tgt.order) == (False, None)

    def test_a_short_without_an_order_has_none(self) -> None:
        assert filings(OUTPUT, {})[0].order is None

    def test_outputs_before_predictions_have_no_filings(self) -> None:
        assert filings({'tickers': ['DBX'], 'scored': 1, 'shorts': [KEY]}, {KEY: ORDER}) == ()

    def test_no_fresh_rows_no_filings(self) -> None:
        assert filings({'tickers': ['DBX'], 'scored': 0, 'shorts': [], 'predictions': []}, {}) == ()


class TestLiveReport:
    """The report lists the runs as given, newest first, with orders matched across them."""

    def test_report_json(self) -> None:
        sources = [
            RunSource(execution('RUNNING', None), history(('yahoo-connect', 0, None))),
            RunSource(execution('SUCCEEDED', OUTPUT), history(('inference', 0, 5))),
            RunSource(
                execution('FAILED', None, 'ValueError', json.dumps({'errorMessage': 'boom'})),
                history(('target', 0, None)),
            ),
        ]
        report = json.loads(live_report(T0, True, sources, [ORDER]).model_dump_json())
        assert report['generated_at'] == '2026-11-23T15:00:00Z'
        assert (report['probability_cutoff'], report['poller_enabled']) == (0.55, True)
        running, succeeded, failed = report['runs']
        assert (running['status'], running['stopped_at'], running['tickers'], running['filings']) == (
            'RUNNING',
            None,
            ['DBX', 'TGT'],
            [],
        )
        assert succeeded['filings'][0] == {
            'key': KEY,
            'ticker': 'DBX',
            'accepted_at': '2026-11-23T14:30:00Z',
            'probability': 0.71,
            'shorted': True,
            'order': {'id': 'o-1', 'status': 'filled', 'filled_avg_price': 27.1},
        }
        assert (failed['error'], failed['steps'][0]['status']) == ('ValueError: boom', 'FAILED')
        assert [run['skip'] for run in report['runs']] == [None, None, None]

    def test_a_run_without_a_sale_shows_its_input_tickers(self) -> None:
        output = {'tickers': [], 'scored': 0, 'shorts': [], 'predictions': []}
        (run,) = live_report(T0, True, [RunSource(execution('SUCCEEDED', output), ())], []).runs
        assert (run.tickers, run.skip) == (('DBX', 'TGT'), Skip.NO_SALE)


# health.json


def summary(status: str, name: str = 'run') -> ExecutionSummary:
    return ExecutionSummary(f'arn:{name}', name, status, T0, None)  # type: ignore[arg-type]


class TestQueues:
    """Each main queue is reported with its dead-letter queue's depth, without the name prefix."""

    def test_dead_letter_queues_pair_with_their_queue(self) -> None:
        depths = {
            'secform4strategy-live-filings': QueueDepth(3, 1),
            'secform4strategy-live-filings-dlq': QueueDepth(2, 0),
            'secform4strategy-filings': QueueDepth(500, 10),
        }
        assert queues(depths, 'secform4strategy-') == (
            QueueHealth(name='filings', depth=500, in_flight=10, dlq_depth=None),
            QueueHealth(name='live-filings', depth=3, in_flight=1, dlq_depth=2),
        )


class TestLambdas:
    """Functions keep their order and lose the prefix; no invocations means no duration."""

    def test_metrics_per_function(self) -> None:
        result = lambdas(
            {
                'secform4strategy-live-inference': FunctionMetrics(12, 1, 5400.0),
                'secform4strategy-live-target': FunctionMetrics(0, 0, None),
            },
            'secform4strategy-live-',
        )
        assert [(f.name, f.invocations_1h, f.errors_1h, f.p90_duration_ms) for f in result] == [
            ('inference', 12, 1, 5400.0),
            ('target', 0, 0, None),
        ]


class TestRunCounts:
    """Timeouts and aborts count as failures."""

    def test_counts_by_outcome(self) -> None:
        statuses = ['SUCCEEDED', 'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED', 'RUNNING']
        counts = run_counts(summary(status) for status in statuses)
        assert (counts.succeeded, counts.failed, counts.running) == (2, 3, 1)


class TestRetrain:
    """The retrain reports the state it entered last."""

    def test_current_step_is_the_last_state_entered(self) -> None:
        newest_first = (
            HistoryEvent('TaskStarted', T0, None),
            HistoryEvent('TaskStateEntered', T0, 'scraper'),
            HistoryEvent('TaskStateExited', T0, 'master-index'),
            HistoryEvent('TaskStateEntered', T0, 'master-index'),
        )
        assert current_step(newest_first) == 'scraper'

    def test_before_the_first_state_there_is_no_step(self) -> None:
        assert current_step((HistoryEvent('ExecutionStarted', T0, None),)) is None

    def test_retrain_record(self) -> None:
        result = retrain(summary('RUNNING', 'backfill-5y'), (HistoryEvent('TaskStateEntered', T0, 'target'),))
        assert result is not None
        assert (result.name, result.status, result.step) == ('backfill-5y', 'RUNNING', 'target')
        assert result.stopped_at is None

    def test_no_retrain_yet(self) -> None:
        assert retrain(None, ()) is None


class TestModelInfo:
    """The model is its version, training time and short commit."""

    def test_model_info(self) -> None:
        metadata = ModelMetadata(
            trained_at=T0,
            git_sha='19a068a5aa6f',
            sklearn_version='1.9.1',
            feature_group_version=1,
            target='pct_change',
            features=('a',),
            model_params={},
            train_rows=1,
            test_rows=1,
            test_start=T0,
            metrics={'model': Scores(0.1, 0.0, 0)},
        )
        info = model_info('20261123T150000Z', metadata)
        assert info is not None
        assert info.model_dump(mode='json') == {
            'version': '20261123T150000Z',
            'trained_at': '2026-11-23T15:00:00Z',
            'git_sha': '19a068a',
        }

    def test_no_model(self) -> None:
        assert model_info(None, None) is None


# portfolio.json


def order(name: str, submitted: datetime | None) -> BrokerOrder:
    return BrokerOrder(name, f'{name}@key', name, 'sell', 1.0, 'filled', submitted, submitted, 27.1)


class TestPortfolio:
    """The report serialises the account's records as the contract names them."""

    def test_report_json(self) -> None:
        report = json.loads(
            portfolio_report(
                T0,
                BrokerAccount(100_012.5, 101_200.0, 380_000.0, 'ACTIVE'),
                True,
                (EquityPoint(T0 - timedelta(days=1), 100_000.0),),
                (
                    BrokerPosition('ZZZ', 2, 10.0, None, None, None, None),
                    BrokerPosition('DBX', -1, 27.1, 26.8, -26.8, 0.3, 0.011),
                ),
                (order('old', T0 - timedelta(hours=1)), order('unsent', None), order('new', T0)),
            ).model_dump_json()
        )
        assert report['account'] == {
            'equity': 100_012.5,
            'cash': 101_200.0,
            'buying_power': 380_000.0,
            'status': 'ACTIVE',
            'paper': True,
        }
        assert report['equity_history'] == [{'t': '2026-11-22T15:00:00Z', 'equity': 100_000.0}]
        assert [p['symbol'] for p in report['positions']] == ['DBX', 'ZZZ']
        assert report['positions'][0]['qty'] == -1
        assert [o['id'] for o in report['orders']] == ['unsent', 'new', 'old']
        assert report['orders'][1]['submitted_at'] == '2026-11-23T15:00:00Z'

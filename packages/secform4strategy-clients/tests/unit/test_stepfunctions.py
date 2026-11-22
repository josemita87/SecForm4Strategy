"""Step Functions executions and histories come back as records.

Under test:
    packages/secform4strategy-clients/src/secform4strategy_clients/stepfunctions.py
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from botocore.stub import Stubber
from secform4strategy_clients.records import HistoryEvent
from secform4strategy_clients.stepfunctions import StepFunctions

T0 = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)


@pytest.fixture
def sfn(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[StepFunctions, Stubber]]:
    """Step Functions whose client answers from a stubber, which must use every answer it is given."""
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'test')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'test')
    executions = StepFunctions('us-east-1')
    with Stubber(executions.sfn) as stubber:
        yield executions, stubber
        stubber.assert_no_pending_responses()


def test_history_names_the_states(sfn: tuple[StepFunctions, Stubber]) -> None:
    executions, stubber = sfn
    stubber.add_response(
        'get_execution_history',
        {
            'events': [
                {'id': 1, 'type': 'ExecutionStarted', 'timestamp': T0},
                {'id': 2, 'type': 'TaskStateEntered', 'timestamp': T0, 'stateEnteredEventDetails': {'name': 'target'}},
                {'id': 3, 'type': 'TaskStateExited', 'timestamp': T0, 'stateExitedEventDetails': {'name': 'target'}},
            ]
        },
        {'executionArn': 'arn:x', 'reverseOrder': False, 'maxResults': 1000},
    )
    assert executions.history('arn:x', False, 1000) == (
        HistoryEvent('ExecutionStarted', T0, None),
        HistoryEvent('TaskStateEntered', T0, 'target'),
        HistoryEvent('TaskStateExited', T0, 'target'),
    )


def test_started_since_stops_at_older_runs(sfn: tuple[StepFunctions, Stubber]) -> None:
    executions, stubber = sfn
    run = {'stateMachineArn': 'arn:m', 'status': 'SUCCEEDED'}
    stubber.add_response(
        'list_executions',
        {
            'executions': [
                {**run, 'executionArn': 'arn:new', 'name': 'new', 'startDate': T0},
                {**run, 'executionArn': 'arn:old', 'name': 'old', 'startDate': datetime(2026, 11, 1, tzinfo=UTC)},
            ]
        },
        {'stateMachineArn': 'arn:m'},
    )
    assert [run.name for run in executions.started_since('arn:m', datetime(2026, 11, 22, tzinfo=UTC))] == ['new']

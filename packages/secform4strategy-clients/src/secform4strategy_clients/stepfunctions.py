"""AWS Step Functions executions and their histories (an ``Executions`` adapter).

botocore retries throttling and server errors itself, so the client only classifies the failures left over
with ``aws.classified`` (docs/error-handling.md).
"""

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

import boto3

from secform4strategy_clients.aws import RETRIES, classified
from secform4strategy_clients.records import Execution, ExecutionSummary, HistoryEvent

if TYPE_CHECKING:
    from mypy_boto3_stepfunctions import SFNClient


def _summary(execution: Mapping[str, Any]) -> ExecutionSummary:
    """Return a ListExecutions item as a record."""
    return ExecutionSummary(
        execution['executionArn'],
        execution['name'],
        execution['status'],
        execution['startDate'],
        execution.get('stopDate'),
    )


class StepFunctions:
    """Read state-machine executions and their histories.

    Attributes:
        sfn: The Step Functions client, retrying throttling and server errors in botocore's standard mode.
    """

    def __init__(self, region: str) -> None:
        """Open a Step Functions client; credentials come from the environment, as for any AWS SDK.

        Args:
            region: AWS region of the state machines.
        """
        self.sfn: SFNClient = boto3.client('stepfunctions', region_name=region, config=RETRIES)

    def latest(self, state_machine_arn: str, limit: int) -> tuple[ExecutionSummary, ...]:
        """Return up to ``limit`` executions, newest first.

        Args:
            state_machine_arn: The state machine.
            limit: At most this many, up to 1000.

        Returns:
            The executions.
        """
        page = classified(
            f'List executions of {state_machine_arn}',
            self.sfn.list_executions,
            stateMachineArn=state_machine_arn,
            maxResults=limit,
        )
        return tuple(_summary(execution) for execution in page['executions'])

    def started_since(self, state_machine_arn: str, since: datetime) -> tuple[ExecutionSummary, ...]:
        """Return every execution started at or after ``since``, newest first.

        Args:
            state_machine_arn: The state machine.
            since: The earliest start included.

        Returns:
            The executions.
        """
        return classified(f'List executions of {state_machine_arn}', self._started_since, state_machine_arn, since)

    def _started_since(self, state_machine_arn: str, since: datetime) -> tuple[ExecutionSummary, ...]:
        """Page through the executions, newest first, until one started before ``since``."""
        found: list[ExecutionSummary] = []
        for page in self.sfn.get_paginator('list_executions').paginate(stateMachineArn=state_machine_arn):
            for execution in page['executions']:
                if execution['startDate'] < since:
                    return tuple(found)
                found.append(_summary(execution))
        return tuple(found)

    def describe(self, execution_arn: str) -> Execution:
        """Return an execution with its input, output and error.

        Args:
            execution_arn: The execution.

        Returns:
            The execution.
        """
        described = classified(
            f'Describe execution {execution_arn}', self.sfn.describe_execution, executionArn=execution_arn
        )
        return Execution(
            execution_arn,
            described['name'],
            described['status'],
            described['startDate'],
            described.get('stopDate'),
            described.get('input'),
            described.get('output'),
            described.get('error'),
            described.get('cause'),
        )

    def history(self, execution_arn: str, newest_first: bool, limit: int) -> tuple[HistoryEvent, ...]:
        """Return up to ``limit`` history events, each with the state it entered or exited.

        Args:
            execution_arn: The execution.
            newest_first: Whether the newest event comes first.
            limit: At most this many events, up to 1000.

        Returns:
            The events.
        """
        events = classified(
            f'Read the history of {execution_arn}',
            self.sfn.get_execution_history,
            executionArn=execution_arn,
            reverseOrder=newest_first,
            maxResults=limit,
        )['events']
        return tuple(
            HistoryEvent(
                event['type'],
                event['timestamp'],
                details['name']
                if (details := event.get('stateEnteredEventDetails') or event.get('stateExitedEventDetails'))
                else None,
            )
            for event in events
        )

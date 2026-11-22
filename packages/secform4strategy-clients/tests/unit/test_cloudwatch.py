"""Lambda metrics come back per function from one GetMetricData call.

Under test:
    packages/secform4strategy-clients/src/secform4strategy_clients/cloudwatch.py
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from botocore.stub import ANY, Stubber
from secform4strategy_clients.cloudwatch import CloudWatchMetrics
from secform4strategy_clients.records import FunctionMetrics

T0 = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)


@pytest.fixture
def cloudwatch(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[CloudWatchMetrics, Stubber]]:
    """CloudWatch metrics whose client answers from a stubber, which must use every answer it is given."""
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'test')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'test')
    metrics = CloudWatchMetrics('us-east-1')
    with Stubber(metrics.cloudwatch) as stubber:
        yield metrics, stubber
        stubber.assert_no_pending_responses()


def test_function_metrics(cloudwatch: tuple[CloudWatchMetrics, Stubber]) -> None:
    metrics, stubber = cloudwatch
    stubber.add_response(
        'get_metric_data',
        {
            'MetricDataResults': [
                {'Id': 'm0_invocations', 'Values': [12.0]},
                {'Id': 'm0_errors', 'Values': []},
                {'Id': 'm0_duration', 'Values': [5400.0]},
            ]
        },
        {'MetricDataQueries': ANY, 'StartTime': ANY, 'EndTime': ANY},
    )
    assert metrics.functions(['f'], T0, T0) == {'f': FunctionMetrics(12, 0, 5400.0)}

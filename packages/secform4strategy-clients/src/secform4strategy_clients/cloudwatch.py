"""Lambda metrics from Amazon CloudWatch (a ``Metrics`` adapter).

botocore retries throttling and server errors itself, so the client only classifies the failures left over
with ``aws.classified`` (docs/error-handling.md).
"""

from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING

import boto3

from secform4strategy_clients.aws import RETRIES, classified
from secform4strategy_clients.records import FunctionMetrics

if TYPE_CHECKING:
    from mypy_boto3_cloudwatch import CloudWatchClient
    from mypy_boto3_cloudwatch.type_defs import MetricDataQueryTypeDef

#: CloudWatch statistic per Lambda metric read.
FUNCTION_STATISTICS = {'Invocations': 'Sum', 'Errors': 'Sum', 'Duration': 'p90'}


class CloudWatchMetrics:
    """Read Lambda functions' invocations, errors and durations.

    Attributes:
        cloudwatch: The CloudWatch client, retrying throttling and server errors in botocore's standard mode.
    """

    def __init__(self, region: str) -> None:
        """Open a CloudWatch client; credentials come from the environment, as for any AWS SDK.

        Args:
            region: AWS region of the functions.
        """
        self.cloudwatch: CloudWatchClient = boto3.client('cloudwatch', region_name=region, config=RETRIES)

    def functions(self, names: Iterable[str], since: datetime, until: datetime) -> dict[str, FunctionMetrics]:
        """Return each function's invocations, errors and p90 duration over the window, in one GetMetricData call.

        Args:
            names: Full function names.
            since: Window start.
            until: Window end.

        Returns:
            The metrics per function name; a function with no invocations has zero counts and no duration.
        """
        names = tuple(names)
        queries: list[MetricDataQueryTypeDef] = [
            {
                'Id': f'm{index}_{metric.lower()}',
                'MetricStat': {
                    'Metric': {
                        'Namespace': 'AWS/Lambda',
                        'MetricName': metric,
                        'Dimensions': [{'Name': 'FunctionName', 'Value': name}],
                    },
                    'Period': max(60, int((until - since).total_seconds())),
                    'Stat': statistic,
                },
            }
            for index, name in enumerate(names)
            for metric, statistic in FUNCTION_STATISTICS.items()
        ]
        results = classified(
            'Read Lambda metrics',
            self.cloudwatch.get_metric_data,
            MetricDataQueries=queries,
            StartTime=since,
            EndTime=until,
        )['MetricDataResults']
        values = {result['Id']: result.get('Values', []) for result in results}
        return {
            name: FunctionMetrics(
                int(sum(values.get(f'm{index}_invocations', []))),
                int(sum(values.get(f'm{index}_errors', []))),
                max(durations) if (durations := values.get(f'm{index}_duration')) else None,
            )
            for index, name in enumerate(names)
        }

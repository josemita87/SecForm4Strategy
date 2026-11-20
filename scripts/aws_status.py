# /// script
# requires-python = ">=3.14"
# dependencies = ["boto3>=1.35,<2"]
# ///
"""Print the AWS stack's state in one screen: switches, runs, queues with rates and ETAs, tasks, errors, model.

Run with ``make status`` (``uv run scripts/aws_status.py`` with ``AWS_PROFILE=secform4``). It only reads.
A queue's rate is the messages deleted in the last 10 minutes; its ETA is its depth divided by that rate.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

import boto3

NAME = 'secform4strategy'
REGION = 'us-east-1'
QUEUES = ('filings', 'transactions', 'live-filings', 'live-transactions', 'live-tickers')
SERVICES = ('master-index', 'scraper', 'kafka-to-store', 'yahoo-connect', 'target', 'training', 'inference')
LIVE_SERVICES = ('master-index', 'scraper', 'kafka-to-store', 'yahoo-connect', 'target', 'inference')
RATE_WINDOW = timedelta(minutes=10)
ERROR_WINDOW = timedelta(hours=1)
#: Error lines counted per log group; more show as this number with a plus.
ERROR_CAP = 5000


class Clients(NamedTuple):
    """The AWS clients the report reads from."""

    sqs: Any
    cloudwatch: Any
    logs: Any
    ecs: Any
    sfn: Any
    scheduler: Any
    ec2: Any
    s3: Any
    account: str


def duration(span: timedelta) -> str:
    """Return ``span`` as ``1d2h``, ``2h44m`` or ``5m``."""
    minutes = int(span.total_seconds() // 60)
    days, hours = divmod(minutes // 60, 24)
    return f'{days}d{hours}h' if days else f'{hours}h{minutes % 60:02d}m' if hours else f'{minutes}m'


def switches(aws: Clients) -> str:
    """Return whether the NAT gateway exists and whether each schedule is enabled."""
    nat = aws.ec2.describe_nat_gateways(
        Filters=[{'Name': 'tag:Project', 'Values': [NAME]}, {'Name': 'state', 'Values': ['available', 'pending']}]
    )['NatGateways']
    states = {
        label: aws.scheduler.get_schedule(Name=f'{NAME}-{schedule}')['State'].lower()
        for label, schedule in (('weekly', 'weekly-retrain'), ('live', 'live-poll'))
    }
    return f'stack: {"on" if nat else "off"}   weekly retrain: {states["weekly"]}   live poller: {states["live"]}'


def current_step(aws: Clients, execution_arn: str) -> str:
    """Return the step a running execution is in, how long it has run, and its attempt number."""
    events = aws.sfn.get_execution_history(executionArn=execution_arn, reverseOrder=True, maxResults=200)['events']
    entered = next((e for e in events if e['type'] == 'TaskStateEntered'), None)
    if entered is None:
        return 'starting'
    attempts = sum(1 for e in events if e['id'] > entered['id'] and e['type'] == 'TaskScheduled')
    since = datetime.now(UTC) - entered['timestamp']
    return f'step: {entered["stateEnteredEventDetails"]["name"]} ({duration(since)}, attempt {max(attempts, 1)})'


def runs(aws: Clients) -> list[str]:
    """Return one line per running ``retrain`` execution, or the latest one when none runs."""
    machine = f'arn:aws:states:{REGION}:{aws.account}:stateMachine:{NAME}-retrain'
    if running := aws.sfn.list_executions(stateMachineArn=machine, statusFilter='RUNNING')['executions']:
        return [
            f'{run["name"]:<22} RUNNING {duration(datetime.now(UTC) - run["startDate"]):>7}   '
            f'{current_step(aws, run["executionArn"])}'
            for run in running
        ]
    latest = aws.sfn.list_executions(stateMachineArn=machine, maxResults=1)['executions']
    return [
        f'{run["name"]:<22} {run["status"]} {run.get("stopDate", run["startDate"]):%Y-%m-%d %H:%M}' for run in latest
    ]


def deleted_per_second(aws: Clients, queue: str) -> float:
    """Return the messages deleted from ``queue`` per second over the last 10 minutes."""
    end = datetime.now(UTC)
    points = aws.cloudwatch.get_metric_statistics(
        Namespace='AWS/SQS',
        MetricName='NumberOfMessagesDeleted',
        Dimensions=[{'Name': 'QueueName', 'Value': queue}],
        StartTime=end - RATE_WINDOW,
        EndTime=end,
        Period=60,
        Statistics=['Sum'],
    )['Datapoints']
    return sum(point['Sum'] for point in points) / RATE_WINDOW.total_seconds()


def depth(aws: Clients, queue: str) -> int:
    """Return the messages in ``queue``, visible and in flight."""
    url = aws.sqs.get_queue_url(QueueName=queue)['QueueUrl']
    attributes = aws.sqs.get_queue_attributes(
        QueueUrl=url, AttributeNames=['ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible']
    )['Attributes']
    return sum(int(value) for value in attributes.values())


def queues(aws: Clients) -> list[str]:
    """Return one line per queue: depth, rate, ETA and dead-letter depth."""
    lines = []
    for key in QUEUES:
        queue = f'{NAME}-{key}'
        messages, rate, dead = depth(aws, queue), deleted_per_second(aws, queue), depth(aws, f'{queue}-dlq')
        eta = f'ETA {duration(timedelta(seconds=messages / rate))}' if messages and rate else ''
        flow = f'{rate:6.1f}/s' if rate else ''
        lines.append(f'{key:<18} {messages:>10,}  {flow:>8}  {eta:<12} DLQ {dead}{"  <- check" if dead else ""}')
    return lines


def tasks(aws: Clients) -> list[str]:
    """Return one line per running ECS task: service, revision, who started it, and for how long."""
    arns = aws.ecs.list_tasks(cluster=NAME, desiredStatus='RUNNING')['taskArns']
    described = aws.ecs.describe_tasks(cluster=NAME, tasks=arns)['tasks'] if arns else []
    return [
        f'{task["group"].removeprefix(f"family:{NAME}-"):<16} rev {task["taskDefinitionArn"].rsplit(":", 1)[1]:<3} '
        f'{"retrain run" if task.get("startedBy") else "started by hand":<16} '
        f'{duration(datetime.now(UTC) - task.get("startedAt", task["createdAt"]))}'
        for task in described
    ] or ['none running']


def error_lines(aws: Clients, group: str) -> int:
    """Return the ``ERROR`` lines ``group`` received in the last hour, at most ``ERROR_CAP``."""
    since = int((datetime.now(UTC) - ERROR_WINDOW).timestamp() * 1000)
    count, token = 0, None
    try:
        while count < ERROR_CAP:
            page = aws.logs.filter_log_events(
                logGroupName=group, startTime=since, filterPattern='ERROR', **({'nextToken': token} if token else {})
            )
            count += len(page['events'])
            if not (token := page.get('nextToken')):
                break
    except aws.logs.exceptions.ResourceNotFoundException:
        return 0
    return count


def errors(aws: Clients) -> list[str]:
    """Return the error-line counts of the last hour, per service, for the tasks and the Lambdas."""
    counts = {
        'tasks': {service: error_lines(aws, f'/{NAME}/{service}') for service in SERVICES},
        'lambdas': {service: error_lines(aws, f'/aws/lambda/{NAME}-live-{service}') for service in LIVE_SERVICES},
    }
    return [
        f'{kind:<8} '
        + (
            ' · '.join(f'{service} {n:,}{"+" if n >= ERROR_CAP else ""}' for service, n in by_service.items() if n)
            or 'none'
        )
        for kind, by_service in counts.items()
    ]


def live_scores(aws: Clients) -> str:
    """Return the ``live-score`` runs of the last 24 hours by outcome, and the latest failure's error."""
    machine = f'arn:aws:states:{REGION}:{aws.account}:stateMachine:{NAME}-live-score'
    since = datetime.now(UTC) - timedelta(days=1)
    recent = [
        run
        for run in aws.sfn.list_executions(stateMachineArn=machine, maxResults=100)['executions']
        if run['startDate'] >= since
    ]
    outcomes = {
        status: sum(1 for run in recent if run['status'] == status) for status in ('SUCCEEDED', 'RUNNING', 'FAILED')
    }
    line = f'last 24h: {outcomes["SUCCEEDED"]} ok, {outcomes["RUNNING"]} running, {outcomes["FAILED"]} failed'
    if failed := next((run for run in recent if run['status'] == 'FAILED'), None):
        cause = aws.sfn.describe_execution(executionArn=failed['executionArn']).get('cause', '')
        message = json.loads(cause).get('errorMessage', cause) if cause.startswith('{') else cause
        line += f'   latest failure {failed["startDate"]:%H:%M}: {message[:90]}'
    return line


def model(aws: Clients) -> str:
    """Return the folder ``latest.json`` names in the models prefix, and when it was written."""
    bucket = f'{NAME}-data-{aws.account}'
    try:
        latest = aws.s3.get_object(Bucket=bucket, Key='models/latest.json')
    except aws.s3.exceptions.NoSuchKey:
        return 'none yet'
    return (
        f'{json.loads(latest["Body"].read()).get("version", "?")} (written {latest["LastModified"]:%Y-%m-%d %H:%M} UTC)'
    )


def main() -> None:
    """Print the report."""
    session = boto3.Session(region_name=REGION)
    aws = Clients(
        *(
            session.client(name)
            for name in ('sqs', 'cloudwatch', 'logs', 'ecs', 'stepfunctions', 'scheduler', 'ec2', 's3')
        ),
        account=session.client('sts').get_caller_identity()['Account'],
    )
    sections = {
        'retrain': runs(aws),
        'queues': queues(aws),
        'tasks': tasks(aws),
        'errors': [f'{line}   (last hour)' for line in errors(aws)],
        'live': [live_scores(aws)],
        'model': [model(aws)],
    }
    print(f'{datetime.now(UTC):%Y-%m-%d %H:%M} UTC   {switches(aws)}\n')
    for title, lines in sections.items():
        for index, line in enumerate(lines):
            print(f'{title if index == 0 else "":<9}{line}')
        print()


if __name__ == '__main__':
    main()

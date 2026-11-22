"""SQS queues carry JSON bodies in batches of 10, and SQS failures surface as the port's two errors.

Under test:
    packages/secform4strategy-clients/src/secform4strategy_clients/sqs.py
"""

import json
from collections.abc import Iterator

import pytest
from botocore.stub import ANY, Stubber
from secform4strategy_clients.ports import PermanentError, RetryableError
from secform4strategy_clients.records import Message, QueueDepth
from secform4strategy_clients.sqs import SqsQueue, SqsReceipt

URL = 'https://sqs.us-east-1.amazonaws.com/100118195151/tmi'


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[SqsQueue, Stubber]]:
    """An SQS queue whose client answers from a stubber, which must use every answer it is given."""
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'test')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'test')
    sqs = SqsQueue('us-east-1')
    with Stubber(sqs.sqs) as stubber:
        yield sqs, stubber
        stubber.assert_no_pending_responses()


def looked_up(stubber: Stubber) -> Stubber:
    """Answer the queue's one URL lookup."""
    stubber.add_response('get_queue_url', {'QueueUrl': URL}, {'QueueName': 'tmi'})
    return stubber


def received(*bodies: str) -> dict[str, object]:
    """A receive_message response carrying the bodies, each with a receipt handle of its own."""
    return {
        'Messages': [
            {'MessageId': f'm{i}', 'ReceiptHandle': f'h{i}', 'Body': body, 'MD5OfBody': ''}
            for i, body in enumerate(bodies)
        ]
    }


class TestSend:
    """Bodies go out as JSON, 10 per call."""

    def test_bodies_are_sent_as_json_in_batches_of_ten(self, queue: tuple[SqsQueue, Stubber]) -> None:
        sqs, stubber = queue[0], looked_up(queue[1])
        bodies = [{'n': n} for n in range(12)]
        for batch in (bodies[:10], bodies[10:]):
            stubber.add_response(
                'send_message_batch',
                {'Successful': [], 'Failed': []},
                {
                    'QueueUrl': URL,
                    'Entries': [{'Id': str(i), 'MessageBody': json.dumps(body)} for i, body in enumerate(batch)],
                },
            )
        sqs.send('tmi', [(f'k{n}', body) for n, body in enumerate(bodies)])

    def test_entries_rejected_as_the_senders_fault_are_permanent(self, queue: tuple[SqsQueue, Stubber]) -> None:
        sqs, stubber = queue[0], looked_up(queue[1])
        failed = [{'Id': '0', 'SenderFault': True, 'Code': 'InvalidMessageContents'}]
        stubber.add_response('send_message_batch', {'Successful': [], 'Failed': failed})
        with pytest.raises(PermanentError, match='InvalidMessageContents'):
            sqs.send('tmi', [('k', {'n': 1})])

    def test_entries_failed_on_the_service_side_are_retryable(self, queue: tuple[SqsQueue, Stubber]) -> None:
        sqs, stubber = queue[0], looked_up(queue[1])
        failed = [{'Id': '0', 'SenderFault': False, 'Code': 'InternalError'}]
        stubber.add_response('send_message_batch', {'Successful': [], 'Failed': failed})
        with pytest.raises(RetryableError, match='InternalError'):
            sqs.send('tmi', [('k', {'n': 1})])


class TestReceive:
    """Calls long-poll for up to 10 messages until enough arrive or a call comes back empty."""

    def test_calls_repeat_until_a_call_is_empty(self, queue: tuple[SqsQueue, Stubber]) -> None:
        sqs, stubber = queue[0], looked_up(queue[1])
        stubber.add_response(
            'receive_message',
            received('{"a": 1}', '{"a": 2}'),
            {'QueueUrl': URL, 'MaxNumberOfMessages': 10, 'WaitTimeSeconds': 20},
        )
        # Once messages are in hand, the next call does not wait
        stubber.add_response('receive_message', {}, {'QueueUrl': URL, 'MaxNumberOfMessages': 8, 'WaitTimeSeconds': 0})
        messages = sqs.receive('tmi', max_messages=10, wait_seconds=150)
        assert messages == (Message({'a': 1}, SqsReceipt('h0')), Message({'a': 2}, SqsReceipt('h1')))

    def test_a_message_that_is_not_json_is_deleted_and_not_returned(self, queue: tuple[SqsQueue, Stubber]) -> None:
        sqs, stubber = queue[0], looked_up(queue[1])
        stubber.add_response('receive_message', received('not json', '{"a": 1}'))
        stubber.add_response(
            'delete_message_batch',
            {'Successful': [{'Id': '0'}], 'Failed': []},
            {'QueueUrl': URL, 'Entries': [{'Id': '0', 'ReceiptHandle': 'h0'}]},
        )
        assert [m.body for m in sqs.receive('tmi', max_messages=1, wait_seconds=1)] == [{'a': 1}]

    def test_throttling_is_retryable(self, queue: tuple[SqsQueue, Stubber]) -> None:
        sqs, stubber = queue[0], looked_up(queue[1])
        stubber.add_client_error('receive_message', service_error_code='ThrottlingException', http_status_code=400)
        with pytest.raises(RetryableError, match='ThrottlingException'):
            sqs.receive('tmi', max_messages=1, wait_seconds=1)

    def test_a_server_error_is_retryable(self, queue: tuple[SqsQueue, Stubber]) -> None:
        sqs, stubber = queue[0], looked_up(queue[1])
        stubber.add_client_error('receive_message', service_error_code='InternalError', http_status_code=500)
        with pytest.raises(RetryableError, match='500'):
            sqs.receive('tmi', max_messages=1, wait_seconds=1)

    def test_rounds_run_parallel_calls_until_a_round_is_empty(self, queue: tuple[SqsQueue, Stubber]) -> None:
        sqs, stubber = queue[0], looked_up(queue[1])
        stubber.add_response('receive_message', received(*(f'{{"a": {i}}}' for i in range(10))))
        # 25 missing: one round of three calls (10, 10, 5) runs at once, then an empty round ends the receive
        for response in (received(*(f'{{"b": {i}}}' for i in range(10))), received('{"c": 0}'), {}, {}, {}):
            stubber.add_response('receive_message', response)
        messages = sqs.receive('tmi', max_messages=35, wait_seconds=1)
        assert len(messages) == 21
        assert sorted(next(iter(m.body)) for m in messages) == ['a'] * 10 + ['b'] * 10 + ['c']


class TestAck:
    """Finished messages are deleted, 10 per call; anything else is the caller's mistake."""

    def test_finished_messages_are_deleted_by_handle(self, queue: tuple[SqsQueue, Stubber]) -> None:
        sqs, stubber = queue[0], looked_up(queue[1])
        stubber.add_response(
            'delete_message_batch',
            {'Successful': [{'Id': '0'}, {'Id': '1'}], 'Failed': []},
            {'QueueUrl': URL, 'Entries': [{'Id': '0', 'ReceiptHandle': 'h0'}, {'Id': '1', 'ReceiptHandle': 'h1'}]},
        )
        sqs.ack('tmi', [Message({}, SqsReceipt('h0')), Message({}, SqsReceipt('h1'))])

    def test_many_messages_are_deleted_in_parallel_batches_of_ten(self, queue: tuple[SqsQueue, Stubber]) -> None:
        sqs, stubber = queue[0], looked_up(queue[1])
        for _ in range(3):
            stubber.add_response('delete_message_batch', {'Successful': [], 'Failed': []})
        sqs.ack('tmi', [Message({}, SqsReceipt(f'h{i}')) for i in range(25)])

    def test_no_messages_makes_no_call(self, queue: tuple[SqsQueue, Stubber]) -> None:
        queue[0].ack('tmi', ())

    def test_a_receipt_from_another_client_is_rejected(self, queue: tuple[SqsQueue, Stubber]) -> None:
        with pytest.raises(TypeError, match='Not an SqsQueue receipt'):
            queue[0].ack('tmi', [Message({}, (0, 5))])


class TestLookup:
    """A queue that does not exist is a permanent failure."""

    def test_an_unknown_queue_is_permanent(self, queue: tuple[SqsQueue, Stubber]) -> None:
        sqs, stubber = queue
        stubber.add_client_error(
            'get_queue_url', service_error_code='AWS.SimpleQueueService.NonExistentQueue', http_status_code=400
        )
        with pytest.raises(PermanentError, match='NonExistentQueue'):
            sqs.send('tmi', [('k', {'n': 1})])


class TestDepths:
    """Queues are listed by prefix and reported by name."""

    def test_queue_depths_by_name(self, queue: tuple[SqsQueue, Stubber]) -> None:
        sqs, stubber = queue
        url = 'https://sqs.us-east-1.amazonaws.com/1/secform4strategy-filings'
        stubber.add_response('list_queues', {'QueueUrls': [url]}, {'QueueNamePrefix': 'secform4strategy-'})
        stubber.add_response(
            'get_queue_attributes',
            {'Attributes': {'ApproximateNumberOfMessages': '5', 'ApproximateNumberOfMessagesNotVisible': '2'}},
            {'QueueUrl': url, 'AttributeNames': ANY},
        )
        assert sqs.depths('secform4strategy-') == {'secform4strategy-filings': QueueDepth(5, 2)}

"""SQS events become messages, and unfinished messages become the batch failures Lambda redelivers.

Under test:
    packages/secform4strategy-clients/src/secform4strategy_clients/lambda_sqs.py
"""

import json

from secform4strategy_clients.lambda_sqs import batch_failures, sqs_messages
from secform4strategy_clients.records import Message


def event(*bodies: str) -> dict[str, list[dict[str, str]]]:
    """An SQS event with one record per body, numbered as message IDs."""
    return {'Records': [{'messageId': f'id-{i}', 'body': body} for i, body in enumerate(bodies)]}


class TestSqsMessages:
    """Each JSON record becomes a message whose receipt is its message ID."""

    def test_records_become_messages_in_order(self) -> None:
        messages = sqs_messages(event(json.dumps({'a': 1}), json.dumps({'b': 2})))
        assert messages == (Message({'a': 1}, 'id-0'), Message({'b': 2}, 'id-1'))

    def test_a_record_that_is_not_json_is_dropped(self) -> None:
        assert sqs_messages(event('not json', json.dumps({'b': 2}))) == (Message({'b': 2}, 'id-1'),)


class TestBatchFailures:
    """Only the messages left unfinished are reported, so only they are delivered again."""

    def test_unfinished_messages_are_reported_by_message_id(self) -> None:
        messages = sqs_messages(event(*(json.dumps({'n': n}) for n in range(3))))
        assert batch_failures(messages, messages[:1]) == {
            'batchItemFailures': [{'itemIdentifier': 'id-1'}, {'itemIdentifier': 'id-2'}]
        }

    def test_a_finished_batch_reports_no_failures(self) -> None:
        messages = sqs_messages(event(json.dumps({'n': 1})))
        assert batch_failures(messages, messages) == {'batchItemFailures': []}

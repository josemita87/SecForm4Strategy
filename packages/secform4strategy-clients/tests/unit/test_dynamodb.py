"""Keys are looked up and recorded in DynamoDB in batches; unprocessed ones are sent again.

Under test:
    packages/secform4strategy-clients/src/secform4strategy_clients/dynamodb.py
"""

from collections.abc import Iterator

import pytest
from botocore.stub import ANY, Stubber
from secform4strategy_clients.dynamodb import DynamoSeenKeys
from secform4strategy_clients.ports import PermanentError

TABLE = 'seen'


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[DynamoSeenKeys, Stubber]]:
    """A seen-keys table whose client answers from a stubber, which must use every answer it is given."""
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'test')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'test')
    monkeypatch.setattr('secform4strategy_clients.dynamodb.time.sleep', lambda _: None)
    table = DynamoSeenKeys('us-east-1', TABLE, ttl_seconds=60)
    with Stubber(table.dynamodb) as stubber:
        yield table, stubber
        stubber.assert_no_pending_responses()


def held(*keys: str) -> list[dict[str, dict[str, str]]]:
    """The items DynamoDB returns for ``keys``."""
    return [{'key': {'S': key}} for key in keys]


class TestUnseen:
    """Keys the table does not hold come back, in their given order."""

    def test_keys_the_table_holds_are_left_out(self, seen: tuple[DynamoSeenKeys, Stubber]) -> None:
        table, stubber = seen
        stubber.add_response('batch_get_item', {'Responses': {TABLE: held('b')}}, {'RequestItems': ANY})
        assert table.unseen(['a', 'b', 'c', 'a']) == ['a', 'c']

    def test_unprocessed_keys_are_read_again(self, seen: tuple[DynamoSeenKeys, Stubber]) -> None:
        table, stubber = seen
        unprocessed = {TABLE: {'Keys': [{'key': {'S': 'c'}}]}}
        stubber.add_response(
            'batch_get_item', {'Responses': {TABLE: held('a')}, 'UnprocessedKeys': unprocessed}, {'RequestItems': ANY}
        )
        stubber.add_response('batch_get_item', {'Responses': {TABLE: held('c')}}, {'RequestItems': unprocessed})
        assert table.unseen(['a', 'b', 'c']) == ['b']

    def test_a_missing_table_raises_permanent_error(self, seen: tuple[DynamoSeenKeys, Stubber]) -> None:
        table, stubber = seen
        stubber.add_client_error('batch_get_item', 'ResourceNotFoundException', http_status_code=400)
        with pytest.raises(PermanentError, match='ResourceNotFoundException'):
            table.unseen(['a'])


class TestRecord:
    """Keys are written with an expiry, 25 per call, and unprocessed ones are written again."""

    def test_keys_are_written_with_their_expiry(self, seen: tuple[DynamoSeenKeys, Stubber]) -> None:
        table, stubber = seen
        stubber.add_response('batch_write_item', {}, {'RequestItems': {TABLE: ANY}})
        table.record(['a', 'b'])

    def test_more_than_25_keys_take_two_calls(self, seen: tuple[DynamoSeenKeys, Stubber]) -> None:
        table, stubber = seen
        stubber.add_response('batch_write_item', {}, {'RequestItems': ANY})
        stubber.add_response('batch_write_item', {}, {'RequestItems': ANY})
        table.record(f'k{n}' for n in range(30))

    def test_unprocessed_items_are_written_again(self, seen: tuple[DynamoSeenKeys, Stubber]) -> None:
        table, stubber = seen
        left = {TABLE: [{'PutRequest': {'Item': {'key': {'S': 'b'}, 'expires_at': {'N': '1'}}}}]}
        stubber.add_response('batch_write_item', {'UnprocessedItems': left}, {'RequestItems': ANY})
        stubber.add_response('batch_write_item', {}, {'RequestItems': left})
        table.record(['a', 'b'])

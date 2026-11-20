"""Amazon DynamoDB as the ``SeenKeys`` of the live path.

Each key is one item ``{"key": <key>, "expires_at": <epoch seconds>}``. DynamoDB's time to
live deletes an item after ``expires_at``, so the table only holds recent keys.
"""

import time
from collections.abc import Iterable, Mapping, Sequence
from itertools import batched
from typing import TYPE_CHECKING

import boto3

from secform4strategy_clients.aws import RETRIES, classified
from secform4strategy_clients.ports import RetryableError

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import KeysAndAttributesUnionTypeDef, WriteRequestUnionTypeDef

#: Most keys per BatchGetItem and per BatchWriteItem call, as DynamoDB allows.
GET_BATCH, WRITE_BATCH = 100, 25
#: Calls per batch that may return unprocessed keys before the batch fails.
ATTEMPTS = 5


class DynamoSeenKeys:
    """Record keys in a DynamoDB table and look up which of them are new.

    Attributes:
        dynamodb: The DynamoDB client, retrying throttling and server errors in botocore's standard mode.
        table: The table name; its partition key is the string attribute ``key``.
        ttl_seconds: How long a recorded key is remembered.
    """

    def __init__(self, region: str, table: str, ttl_seconds: int) -> None:
        """Open a DynamoDB client; credentials come from the environment, as for any AWS SDK.

        Args:
            region: AWS region of the table.
            table: The table name.
            ttl_seconds: How long a recorded key is remembered.
        """
        self.dynamodb: DynamoDBClient = boto3.client('dynamodb', region_name=region, config=RETRIES)
        self.table = table
        self.ttl_seconds = ttl_seconds

    def unseen(self, keys: Iterable[str]) -> list[str]:
        """Return the keys the table does not hold, in their given order.

        Args:
            keys: The keys to look up.

        Returns:
            The new keys.

        Raises:
            RetryableError: DynamoDB left keys unprocessed after ``ATTEMPTS`` calls.
        """
        wanted = list(dict.fromkeys(keys))
        seen: set[str] = set()
        for batch in batched(wanted, GET_BATCH, strict=False):
            request: Mapping[str, KeysAndAttributesUnionTypeDef] = {
                self.table: {
                    'Keys': [{'key': {'S': key}} for key in batch],
                    'ProjectionExpression': '#k',
                    'ExpressionAttributeNames': {'#k': 'key'},
                }
            }
            for _ in range(ATTEMPTS):
                response = classified(f'Read {self.table}', self.dynamodb.batch_get_item, RequestItems=request)
                seen |= {item['key']['S'] for item in response['Responses'].get(self.table, [])}
                if not (request := response.get('UnprocessedKeys', {})):
                    break
                time.sleep(0.1)
            else:
                raise RetryableError(f'Read {self.table}: keys left unprocessed after {ATTEMPTS} calls')
        return [key for key in wanted if key not in seen]

    def record(self, keys: Iterable[str]) -> None:
        """Record the keys, each to expire ``ttl_seconds`` from now.

        Args:
            keys: The keys handled.

        Raises:
            RetryableError: DynamoDB left items unprocessed after ``ATTEMPTS`` calls.
        """
        expires_at = str(int(time.time()) + self.ttl_seconds)
        for batch in batched(dict.fromkeys(keys), WRITE_BATCH, strict=False):
            request: Mapping[str, Sequence[WriteRequestUnionTypeDef]] = {
                self.table: [
                    {'PutRequest': {'Item': {'key': {'S': key}, 'expires_at': {'N': expires_at}}}} for key in batch
                ]
            }
            for _ in range(ATTEMPTS):
                response = classified(f'Write {self.table}', self.dynamodb.batch_write_item, RequestItems=request)
                if not (request := response.get('UnprocessedItems', {})):
                    break
                time.sleep(0.1)
            else:
                raise RetryableError(f'Write {self.table}: items left unprocessed after {ATTEMPTS} calls')

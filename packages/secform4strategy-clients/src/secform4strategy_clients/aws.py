"""What every AWS adapter shares: botocore's retry settings, and how its leftover failures map to the port's errors.

botocore retries throttling, server and connection errors itself, so AWS adapters do not wrap
calls in ``with_backoff``; they only classify what is left (docs/error-handling.md).
"""

from collections.abc import Callable
from http import HTTPStatus
from typing import TYPE_CHECKING

from botocore.config import Config
from botocore.exceptions import ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotocoreConnectionError

from secform4strategy_clients.ports import PermanentError, RetryableError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

#: Client config for every AWS adapter: botocore's standard retry mode, 5 attempts per call.
RETRIES = Config(retries={'mode': 'standard', 'max_attempts': 5})


def classified[**P, T](what: str, call: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Return ``call(*args, **kwargs)``, raising the port's errors for the failures botocore did not retry away.

    Args:
        what: What the call does, for the error message.
        call: An AWS API call.
        *args: Its positional arguments.
        **kwargs: Its keyword arguments.

    Returns:
        What ``call`` returns.

    Raises:
        RetryableError: The connection failed, or AWS throttled or answered 5xx after botocore's retries.
        PermanentError: AWS rejected the call, e.g. a missing resource or permission.
    """
    try:
        return call(*args, **kwargs)
    except (BotocoreConnectionError, HTTPClientError) as error:
        raise RetryableError(f'{what}: {error}') from error
    except ClientError as error:
        code = error.response.get('Error', {}).get('Code', '')
        status = error.response.get('ResponseMetadata', {}).get('HTTPStatusCode', 0)
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR or 'Throttl' in code:
            raise RetryableError(f'{what}: {code} (HTTP {status})') from error
        raise PermanentError(f'{what}: {code} (HTTP {status}) {error}') from error


def read_optional(s3: S3Client, bucket: str, key: str) -> bytes | None:
    """Return an S3 object's body, or None when the key does not exist; callers wrap it in ``classified``.

    Args:
        s3: The S3 client.
        bucket: The bucket.
        key: The object key.

    Returns:
        The body, or None on ``NoSuchKey``.
    """
    try:
        return s3.get_object(Bucket=bucket, Key=key)['Body'].read()
    except s3.exceptions.NoSuchKey:
        return None

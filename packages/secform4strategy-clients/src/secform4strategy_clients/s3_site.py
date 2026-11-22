"""A static site's data files in Amazon S3 (a ``Site`` adapter).

botocore retries throttling and server errors itself, so the client only classifies the failures left over
with ``aws.classified`` (docs/error-handling.md).
"""

from typing import TYPE_CHECKING

import boto3

from secform4strategy_clients.aws import RETRIES, classified, read_optional

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class S3Site:
    """Read and write JSON data files under one prefix of a site bucket.

    Attributes:
        s3: The S3 client, retrying throttling and server errors in botocore's standard mode.
        bucket: The site bucket.
        prefix: The key prefix of the data files.
    """

    def __init__(self, region: str, bucket: str, prefix: str = 'data') -> None:
        """Open an S3 client; credentials come from the environment, as for any AWS SDK.

        Args:
            region: AWS region of the bucket.
            bucket: The site bucket.
            prefix: Key prefix without a trailing slash.
        """
        self.s3: S3Client = boto3.client('s3', region_name=region, config=RETRIES)
        self.bucket = bucket
        self.prefix = prefix

    def read(self, name: str) -> bytes | None:
        """Return a data file's content, or None when it does not exist.

        Args:
            name: The file name, such as ``model.json``.

        Returns:
            The content.
        """
        return classified(f'Read site file {name}', read_optional, self.s3, self.bucket, f'{self.prefix}/{name}')

    def write(self, name: str, body: bytes) -> None:
        """Write a data file as JSON the browser revalidates on every load.

        Args:
            name: The file name, such as ``live.json``.
            body: The JSON content.
        """
        classified(
            f'Write site file {name}',
            self.s3.put_object,
            Bucket=self.bucket,
            Key=f'{self.prefix}/{name}',
            Body=body,
            ContentType='application/json',
            CacheControl='no-cache',
        )

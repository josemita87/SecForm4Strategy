"""Amazon S3 model store (a ``ModelStore`` adapter).

Each save writes the same pair as ``LocalModelStore`` (``model.joblib`` + ``model.meta.json``)
under a folder named after the training time, then points ``latest.json`` at that folder.
Writing the pointer last means a load never pairs a new model with old metadata, and every
earlier model stays in the bucket for audit and rollback.
"""

import json
from datetime import UTC
from io import BytesIO
from typing import TYPE_CHECKING, Any

import boto3
import joblib

from secform4strategy_clients.aws import RETRIES, classified
from secform4strategy_clients.model_store import METADATA

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

    from secform4strategy_clients.ports import ModelMetadata

# The layout under the prefix: one folder per version, and the pointer to the current one
POINTER = 'latest.json'
MODEL = 'model.joblib'
METADATA_FILE = 'model.meta.json'


class S3ModelStore:
    """Save and load versioned models plus their metadata under one S3 prefix.

    Attributes:
        s3: The S3 client, retrying throttling and server errors in botocore's standard mode.
        bucket: The bucket holding the models.
        prefix: The key prefix the version folders and the pointer sit under.
    """

    def __init__(self, region: str, bucket: str, prefix: str) -> None:
        """Open an S3 client; credentials come from the environment, as for any AWS SDK.

        Args:
            region: AWS region of the bucket.
            bucket: The bucket holding the models.
            prefix: Key prefix without a trailing slash, e.g. ``models``.
        """
        self.s3: S3Client = boto3.client('s3', region_name=region, config=RETRIES)
        self.bucket = bucket
        self.prefix = prefix

    def save(self, model: object, metadata: ModelMetadata) -> None:
        """Write the model and its metadata to a new version folder, then point ``latest.json`` at it.

        Args:
            model: The fitted model to persist.
            metadata: The facts about the model; ``trained_at`` names the version.
        """
        version = metadata.trained_at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')
        joblib.dump(model, buffer := BytesIO())
        self._write(f'{version}/{MODEL}', buffer.getvalue())
        self._write(f'{version}/{METADATA_FILE}', METADATA.dump_json(metadata, indent=2))
        self._write(POINTER, json.dumps({'version': version}).encode())

    def load(self) -> tuple[Any, ModelMetadata]:
        """Read the model ``latest.json`` points at, and its validated metadata; no saved model is a ``PermanentError``.

        Returns:
            The model and its metadata.
        """
        version = json.loads(self._read(POINTER))['version']
        return (
            joblib.load(BytesIO(self._read(f'{version}/{MODEL}'))),
            METADATA.validate_json(self._read(f'{version}/{METADATA_FILE}')),
        )

    def _write(self, name: str, body: bytes) -> None:
        """Write one object under the prefix."""
        classified(
            f'Write s3://{self.bucket}/{self.prefix}/{name}',
            self.s3.put_object,
            Bucket=self.bucket,
            Key=f'{self.prefix}/{name}',
            Body=body,
        )

    def _read(self, name: str) -> bytes:
        """Read one object under the prefix, its body included, so a broken download is classified too."""
        return classified(f'Read s3://{self.bucket}/{self.prefix}/{name}', self._get, f'{self.prefix}/{name}')

    def _get(self, key: str) -> bytes:
        """Download one object's body."""
        return self.s3.get_object(Bucket=self.bucket, Key=key)['Body'].read()

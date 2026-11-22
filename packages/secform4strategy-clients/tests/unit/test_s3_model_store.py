"""Models are saved to versioned S3 folders behind a pointer, and loaded through it.

Under test:
    packages/secform4strategy-clients/src/secform4strategy_clients/s3_model_store.py
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from io import BytesIO

import joblib
import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber
from secform4strategy_clients.model_store import METADATA
from secform4strategy_clients.ports import PermanentError, RetryableError
from secform4strategy_clients.records import ModelMetadata, Scores
from secform4strategy_clients.s3_model_store import S3ModelStore

METADATA_RECORD = ModelMetadata(
    trained_at=datetime(2026, 11, 13, 6, 0, tzinfo=UTC),
    git_sha='abc123',
    sklearn_version='1.7.0',
    feature_group_version=1,
    target='return_5d',
    features=('a', 'b'),
    model_params={'alpha': 0.5},
    train_rows=100,
    test_rows=20,
    test_start=datetime(2026, 10, 1, tzinfo=UTC),
    metrics={'model': Scores(mae=0.1, short_return=0.02, n_shorts=5)},
)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[S3ModelStore, Stubber]]:
    """A model store whose client answers from a stubber, which must use every answer it is given."""
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'test')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'test')
    s3 = S3ModelStore('us-east-1', 'bucket', 'models')
    with Stubber(s3.s3) as stubber:
        yield s3, stubber
        stubber.assert_no_pending_responses()


def stored(stubber: Stubber, key: str, body: bytes) -> None:
    """Answer one get_object for ``key`` with ``body``."""
    stubber.add_response(
        'get_object',
        {'Body': StreamingBody(BytesIO(body), len(body))},
        {'Bucket': 'bucket', 'Key': key},
    )


class TestSave:
    """The model and metadata go to a folder named after the training time, and the pointer last."""

    def test_the_pair_is_written_before_the_pointer(self, store: tuple[S3ModelStore, Stubber]) -> None:
        s3, stubber = store
        for key, body in (
            ('models/20261113T060000Z/model.joblib', ANY),
            ('models/20261113T060000Z/model.meta.json', METADATA.dump_json(METADATA_RECORD, indent=2)),
            ('models/latest.json', b'{"version": "20261113T060000Z"}'),
        ):
            stubber.add_response('put_object', {}, {'Bucket': 'bucket', 'Key': key, 'Body': body})
        s3.save({'weights': [1, 2]}, METADATA_RECORD)


class TestLoad:
    """The pointer names the folder the model and metadata are read from."""

    def test_the_pointed_model_and_its_metadata_are_returned(self, store: tuple[S3ModelStore, Stubber]) -> None:
        s3, stubber = store
        joblib.dump({'weights': [1, 2]}, model := BytesIO())
        stored(stubber, 'models/latest.json', json.dumps({'version': 'v1'}).encode())
        stored(stubber, 'models/v1/model.joblib', model.getvalue())
        stored(stubber, 'models/v1/model.meta.json', METADATA.dump_json(METADATA_RECORD))
        assert s3.load() == ({'weights': [1, 2]}, METADATA_RECORD)

    def test_no_saved_model_is_permanent(self, store: tuple[S3ModelStore, Stubber]) -> None:
        s3, stubber = store
        stubber.add_client_error('get_object', service_error_code='NoSuchKey', http_status_code=404)
        with pytest.raises(PermanentError, match='NoSuchKey'):
            s3.load()

    def test_a_server_error_is_retryable(self, store: tuple[S3ModelStore, Stubber]) -> None:
        s3, stubber = store
        stubber.add_client_error('get_object', service_error_code='InternalError', http_status_code=500)
        with pytest.raises(RetryableError, match=r'latest\.json'):
            s3.load()


class TestModelFiles:
    """The pointer and the metadata are read without the model."""

    def test_the_pointer_names_the_version(self, store: tuple[S3ModelStore, Stubber]) -> None:
        s3, stubber = store
        stored(stubber, 'models/latest.json', b'{"version": "20261120T210617Z"}')
        assert s3.latest_version() == '20261120T210617Z'

    def test_no_pointer_is_no_version(self, store: tuple[S3ModelStore, Stubber]) -> None:
        s3, stubber = store
        stubber.add_client_error('get_object', service_error_code='NoSuchKey', http_status_code=404)
        assert s3.latest_version() is None

    def test_a_denied_pointer_is_permanent(self, store: tuple[S3ModelStore, Stubber]) -> None:
        s3, stubber = store
        stubber.add_client_error('get_object', service_error_code='AccessDenied', http_status_code=403)
        with pytest.raises(PermanentError, match='AccessDenied'):
            s3.latest_version()

    def test_metadata_is_validated(self, store: tuple[S3ModelStore, Stubber]) -> None:
        s3, stubber = store
        stored(stubber, 'models/v1/model.meta.json', METADATA.dump_json(METADATA_RECORD))
        assert s3.metadata('v1') == METADATA_RECORD

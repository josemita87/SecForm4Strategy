"""Site data files are written as uncached JSON, and a missing file reads as None.

Under test:
    packages/secform4strategy-clients/src/secform4strategy_clients/s3_site.py
"""

from collections.abc import Iterator

import pytest
from botocore.stub import Stubber
from secform4strategy_clients.s3_site import S3Site


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[S3Site, Stubber]]:
    """A site whose client answers from a stubber, which must use every answer it is given."""
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'test')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'test')
    files = S3Site('us-east-1', 'site')
    with Stubber(files.s3) as stubber:
        yield files, stubber
        stubber.assert_no_pending_responses()


def test_site_files_are_uncached_json(site: tuple[S3Site, Stubber]) -> None:
    files, stubber = site
    stubber.add_response(
        'put_object',
        {},
        {
            'Bucket': 'site',
            'Key': 'data/live.json',
            'Body': b'{}',
            'ContentType': 'application/json',
            'CacheControl': 'no-cache',
        },
    )
    files.write('live.json', b'{}')


def test_a_missing_file_reads_as_none(site: tuple[S3Site, Stubber]) -> None:
    files, stubber = site
    stubber.add_client_error('get_object', 'NoSuchKey', http_status_code=404)
    assert files.read('explore.json') is None

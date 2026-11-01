"""Filings become messages keyed by accession, with their filing time as ISO 8601.

Under test:
    sec-feature-pipeline/master-index/src/main.py
"""

from datetime import UTC, datetime

import xxhash
from main import build_messages
from secform4strategy_clients.ports import Filing

LIVE = Filing('0001-23-000001', 'edgar/data/1/0001-23-000001.txt', datetime.fromisoformat('2023-03-22T21:52:41-04:00'))
INDEXED = Filing('0001-23-000002', 'edgar/data/2/0001-23-000002.txt', datetime(2023, 3, 22, tzinfo=UTC))


class TestBuildMessages:
    """One message per filing; the scraper reads it back with ``select_filings``."""

    def test_messages_are_keyed_by_a_hash_of_the_accession(self) -> None:
        (key, _), *_ = build_messages([LIVE])
        assert key == xxhash.xxh64(LIVE.accession).hexdigest()

    def test_a_message_carries_the_filing_with_its_time_as_iso(self) -> None:
        (_, record), *_ = build_messages([LIVE])
        assert record == {
            'accession': LIVE.accession,
            'file_path': LIVE.file_path,
            'filed_at': '2023-03-22T21:52:41-04:00',
        }
        assert datetime.fromisoformat(record['filed_at']) == LIVE.filed_at

    def test_a_master_index_filing_is_timed_at_utc_midnight(self) -> None:
        (_, record), *_ = build_messages([INDEXED])
        assert record['filed_at'] == '2023-03-22T00:00:00+00:00'

    def test_every_filing_gets_its_own_message(self) -> None:
        assert len({key for key, _ in build_messages([LIVE, INDEXED])}) == 2

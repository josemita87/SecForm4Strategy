"""Filings become messages keyed by accession, with their filing time as ISO 8601.

Under test:
    sec-feature-pipeline/master-index/src/main.py
"""

from datetime import UTC, date, datetime

import xxhash
from main import build_messages, filed_since, publish_new, quarters_between
from secform4strategy_clients.fakes.messaging import FakeMessageQueue
from secform4strategy_clients.fakes.seen import FakeSeenKeys
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


class TestQuartersBetween:
    """The master indexes a run reads, from its first day's quarter to its last day's."""

    def test_one_day_is_its_own_quarter(self) -> None:
        assert quarters_between(date(2026, 11, 19), date(2026, 11, 19)) == ((2026, 4),)

    def test_a_week_across_new_year_reads_both_quarters(self) -> None:
        assert quarters_between(date(2025, 12, 29), date(2026, 1, 5)) == ((2025, 4), (2026, 1))

    def test_years_run_oldest_first_and_stop_at_the_end_quarter(self) -> None:
        quarters = quarters_between(date(2024, 1, 1), date(2025, 5, 2))
        assert quarters == ((2024, 1), (2024, 2), (2024, 3), (2024, 4), (2025, 1), (2025, 2))

    def test_a_start_after_the_end_reads_nothing(self) -> None:
        assert quarters_between(date(2026, 4, 1), date(2026, 3, 31)) == ()


class TestFiledSince:
    """A recent run keeps only the filings of its window."""

    def test_filings_before_the_window_are_dropped(self) -> None:
        assert filed_since([INDEXED], date(2023, 3, 23)) == []

    def test_the_first_day_of_the_window_is_kept(self) -> None:
        assert filed_since([INDEXED], date(2023, 3, 22)) == [INDEXED]

    def test_no_window_keeps_every_filing(self) -> None:
        assert filed_since([INDEXED, LIVE], None) == [INDEXED, LIVE]


class TestPublishNew:
    """Each poll sends only the filings not sent before, then records them."""

    def test_new_filings_are_sent_and_recorded(self) -> None:
        queue, seen = FakeMessageQueue(), FakeSeenKeys()
        assert publish_new([LIVE, INDEXED], seen, queue, 'filings') == 2
        assert [body['accession'] for _, body in queue.sent['filings']] == [LIVE.accession, INDEXED.accession]
        assert seen.keys == {LIVE.accession, INDEXED.accession}

    def test_filings_sent_before_are_skipped(self) -> None:
        queue, seen = FakeMessageQueue(), FakeSeenKeys({LIVE.accession})
        assert publish_new([LIVE, INDEXED], seen, queue, 'filings') == 1
        assert [body['accession'] for _, body in queue.sent['filings']] == [INDEXED.accession]

    def test_a_poll_with_nothing_new_sends_nothing(self) -> None:
        queue = FakeMessageQueue()
        assert publish_new([LIVE], FakeSeenKeys({LIVE.accession}), queue, 'filings') == 0
        assert queue.sent == {}

"""Master-index messages become filings to parse; stale and malformed ones are dropped.

Under test:
    sec-feature-pipeline/scraper/src/main.py
"""

from datetime import UTC, datetime, timedelta

from main import select_filings
from secform4strategy_clients.ports import Filing

NOW = datetime.now(UTC)


def message(accession: str, filed_at: datetime) -> dict[str, str]:
    """A master-index message, as ``build_messages`` publishes it."""
    return {'accession': accession, 'file_path': f'edgar/data/1/{accession}.txt', 'filed_at': filed_at.isoformat()}


class TestSelectFilings:
    """Each message is read back as a ``Filing`` with its offset."""

    def test_a_message_round_trips_to_a_filing(self) -> None:
        filed = datetime.fromisoformat('2026-11-01T10:15:02-05:00')
        assert select_filings([(message('a-1', filed), 7)], None) == [(Filing('a-1', 'edgar/data/1/a-1.txt', filed), 7)]

    def test_live_mode_drops_filings_older_than_max_age(self) -> None:
        records = [(message('old', NOW - timedelta(days=3)), 0), (message('new', NOW - timedelta(hours=1)), 1)]
        assert [f.accession for f, _ in select_filings(records, timedelta(days=1))] == ['new']

    def test_a_malformed_message_is_skipped(self) -> None:
        records = [({'file_path': 'x.txt'}, 0), (message('ok', NOW), 1)]
        assert [f.accession for f, _ in select_filings(records, None)] == ['ok']

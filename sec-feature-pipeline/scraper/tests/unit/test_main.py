"""Master-index messages become filings to parse; each message is finished or left for redelivery.

Under test:
    sec-feature-pipeline/scraper/src/main.py
"""

from datetime import UTC, datetime, timedelta

from main import read_filing, scrape
from secform4strategy_clients.fakes.filings import FakeFilings
from secform4strategy_clients.fakes.messaging import FakeMessageQueue
from secform4strategy_clients.ports import Filing, Message, RetryableError
from tests.paths import FOOTNOTE_10B5_1

NOW = datetime.now(UTC)


def message(accession: str, filed_at: datetime = NOW) -> Message:
    """A master-index message, with the body ``build_messages`` publishes."""
    body = {'accession': accession, 'file_path': f'edgar/data/1/{accession}.txt', 'filed_at': filed_at.isoformat()}
    return Message(body, receipt=accession)


def url(accession: str) -> str:
    """The submission URL the scraper requests for a message."""
    return f'https://www.sec.gov/Archives/edgar/data/1/{accession}.txt'


class TestReadFiling:
    """A message is read back as its ``Filing``, unless it is malformed or stale."""

    def test_a_message_round_trips_to_a_filing(self) -> None:
        filed = datetime.fromisoformat('2026-11-01T10:15:02-05:00')
        assert read_filing(message('a-1', filed), None) == Filing('a-1', 'edgar/data/1/a-1.txt', filed)

    def test_live_mode_drops_filings_older_than_max_age(self) -> None:
        assert read_filing(message('old', NOW - timedelta(days=3)), timedelta(days=1)) is None
        assert read_filing(message('new', NOW - timedelta(hours=1)), timedelta(days=1)) is not None

    def test_a_malformed_message_is_skipped(self) -> None:
        assert read_filing(Message({'file_path': 'x.txt'}, receipt=None), None) is None


class TestScrape:
    """Each message is finished, or left unfinished so the queue delivers it again."""

    def test_parsed_filings_send_their_transactions_keyed_in_one_send(self) -> None:
        queue = FakeMessageQueue()
        done, sent = scrape(
            (ok := message('ok'),), FakeFilings({url('ok'): FOOTNOTE_10B5_1.read_text()}), queue, 'td', None
        )
        assert done == [ok]
        assert sent == len(queue.sent['td']) > 0
        assert all(key == body['key'] for key, body in queue.sent['td'])

    def test_a_retryable_failure_stops_the_batch_and_leaves_the_rest_unfinished(self) -> None:
        served = {url('ok'): FOOTNOTE_10B5_1.read_text(), url('next'): FOOTNOTE_10B5_1.read_text()}
        filings = FakeFilings(served, errors={url('busy'): RetryableError('503')})
        ok, busy, after = message('ok'), message('busy'), message('next')
        done, _ = scrape((ok, busy, after), filings, FakeMessageQueue(), 'td', None)
        assert done == [ok]
        assert url('next') not in filings.requests

    def test_unfinished_messages_are_delivered_again_after_the_ack(self) -> None:
        queue = FakeMessageQueue(pending={'tmi': [message('ok').body, message('busy').body]})
        filings = FakeFilings({url('ok'): FOOTNOTE_10B5_1.read_text()}, errors={url('busy'): RetryableError('503')})
        done, _ = scrape(queue.receive('tmi', 10, 0), filings, queue, 'td', None)
        queue.ack('tmi', done)
        assert [m.body['accession'] for m in queue.receive('tmi', 10, 0)] == ['busy']

    def test_a_permanent_failure_finishes_the_message_without_transactions(self) -> None:
        queue = FakeMessageQueue()
        done, sent = scrape((gone := message('gone'),), FakeFilings({}), queue, 'td', None)
        assert (done, sent, queue.sent) == ([gone], 0, {})

    def test_skipped_messages_are_finished_without_a_request(self) -> None:
        filings = FakeFilings({})
        stale, malformed = message('old', NOW - timedelta(days=3)), Message({}, receipt='m')
        done, _ = scrape((stale, malformed), filings, FakeMessageQueue(), 'td', timedelta(days=1))
        assert (done, filings.requests) == ([stale, malformed], [])

"""Master-index messages become filings to parse; each message is finished or left for redelivery.

Under test:
    sec-feature-pipeline/scraper/src/main.py
"""

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from main import read_filing, scrape
from secform4strategy_clients.fakes.filings import FakeFilings
from secform4strategy_clients.fakes.messaging import FakeMessageQueue
from secform4strategy_clients.ports import RetryableError
from secform4strategy_clients.records import Filing, Message
from tests.paths import FOOTNOTE_10B5_1

if TYPE_CHECKING:
    from secform4strategy_clients.fakes.filings import FakeResponse

NOW = datetime.now(UTC)


@pytest.fixture
def pool() -> Iterator[ThreadPoolExecutor]:
    """Threads the filings download on, as in the service."""
    with ThreadPoolExecutor(4) as executor:
        yield executor


@dataclass
class GatedFilings(FakeFilings):
    """EDGAR where the first filing answers only after the second was requested, so the second finishes first.

    Attributes:
        finished: Accessions in the order their downloads finished.
    """

    finished: list[str] = field(default_factory=list)
    _second_requested: threading.Event = field(default_factory=threading.Event)

    def get(self, url: str) -> FakeResponse:
        """Hold the first filing until the second is requested, then serve as ``FakeFilings`` does."""
        if url.endswith('second.txt'):
            self._second_requested.set()
        else:
            self._second_requested.wait(timeout=5)
        response = super().get(url)
        self.finished.append(url.rsplit('/', 1)[-1].removesuffix('.txt'))
        return response


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

    def test_parsed_filings_send_their_transactions_keyed_in_one_send(self, pool: ThreadPoolExecutor) -> None:
        queue = FakeMessageQueue()
        done, sent = scrape(
            (ok := message('ok'),), FakeFilings({url('ok'): FOOTNOTE_10B5_1.read_text()}), queue, 'td', None, pool
        )
        assert done == [ok]
        assert sent == len(queue.sent['td']) > 0
        assert all(key == body['key'] for key, body in queue.sent['td'])

    def test_a_retryable_failure_stops_the_batch_and_leaves_the_rest_unfinished(self, pool: ThreadPoolExecutor) -> None:
        served = {url('ok'): FOOTNOTE_10B5_1.read_text(), url('next'): FOOTNOTE_10B5_1.read_text()}
        filings = FakeFilings(served, errors={url('busy'): RetryableError('503')})
        ok, busy, after = message('ok'), message('busy'), message('next')
        queue = FakeMessageQueue()
        done, sent = scrape((ok, busy, after), filings, queue, 'td', None, pool)
        assert done == [ok]
        # Only the finished filing's transactions are sent, even if a later filing was already downloaded
        assert sent == scrape((ok,), FakeFilings(served), FakeMessageQueue(), 'td', None, pool)[1]

    def test_unfinished_messages_are_delivered_again_after_the_ack(self, pool: ThreadPoolExecutor) -> None:
        queue = FakeMessageQueue(pending={'tmi': [message('ok').body, message('busy').body]})
        filings = FakeFilings({url('ok'): FOOTNOTE_10B5_1.read_text()}, errors={url('busy'): RetryableError('503')})
        done, _ = scrape(queue.receive('tmi', 10, 0), filings, queue, 'td', None, pool)
        queue.ack('tmi', done)
        assert [m.body['accession'] for m in queue.receive('tmi', 10, 0)] == ['busy']

    def test_a_permanent_failure_finishes_the_message_without_transactions(self, pool: ThreadPoolExecutor) -> None:
        queue = FakeMessageQueue()
        done, sent = scrape((gone := message('gone'),), FakeFilings({}), queue, 'td', None, pool)
        assert (done, sent, queue.sent) == ([gone], 0, {})

    def test_skipped_messages_are_finished_without_a_request(self, pool: ThreadPoolExecutor) -> None:
        filings = FakeFilings({})
        stale, malformed = message('old', NOW - timedelta(days=3)), Message({}, receipt='m')
        done, _ = scrape((stale, malformed), filings, FakeMessageQueue(), 'td', timedelta(days=1), pool)
        assert (done, filings.requests) == ([stale, malformed], [])

    def test_filings_download_in_parallel_and_finish_in_message_order(self, pool: ThreadPoolExecutor) -> None:
        text = FOOTNOTE_10B5_1.read_text()
        filings = GatedFilings({url('first'): text, url('second'): text})
        done, _ = scrape(
            (first := message('first'), second := message('second')), filings, FakeMessageQueue(), 'td', None, pool
        )
        assert filings.finished == ['second', 'first']
        assert done == [first, second]

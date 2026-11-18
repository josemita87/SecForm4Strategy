"""EDGAR answers become filings or one of the two error classes; transient answers are retried.

Under test:
    packages/secform4strategy-clients/src/secform4strategy_clients/edgar.py
"""

import httpx
import pytest
from secform4strategy_clients.edgar import EdgarClient
from secform4strategy_clients.ports import PermanentError, RetryableError

URL = 'https://www.sec.gov/Archives/edgar/data/1/a.txt'


def edgar(monkeypatch: pytest.MonkeyPatch, *answers: httpx.Response | Exception) -> tuple[EdgarClient, list[str]]:
    """An EDGAR client whose transport gives each answer in turn, without waiting between attempts."""
    requests: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if isinstance(answer := answers[len(requests) - 1], Exception):
            raise answer
        return answer

    monkeypatch.setattr('secform4strategy_clients.retry.time.sleep', lambda _: None)
    client = EdgarClient('Jane Doe jane@example.com', max_rate=1000)
    client.http = httpx.Client(transport=httpx.MockTransport(handle))
    return client, requests


class TestGet:
    """Only a 200 is returned; other answers raise by class, transient ones after retries."""

    def test_a_200_is_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = edgar(monkeypatch, httpx.Response(200, text='filing'))
        assert client.get(URL).text == 'filing'

    @pytest.mark.parametrize(
        'answer',
        [
            httpx.Response(503),
            httpx.Response(429),
            httpx.ConnectTimeout('timed out'),
        ],
    )
    def test_a_transient_answer_is_retried(self, monkeypatch: pytest.MonkeyPatch, answer: object) -> None:
        client, requests = edgar(monkeypatch, answer, httpx.Response(200, text='filing'))
        assert client.get(URL).text == 'filing'
        assert len(requests) == 2

    def test_the_rate_limit_lockout_raises_retryable_error_without_retrying(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, requests = edgar(monkeypatch, httpx.Response(403, text='<h1>Request Rate Threshold Exceeded</h1>'))
        with pytest.raises(RetryableError, match='rate limit'):
            client.get(URL)
        assert len(requests) == 1

    def test_a_transient_answer_that_persists_raises_retryable_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, requests = edgar(monkeypatch, *(httpx.Response(503),) * 4)
        with pytest.raises(RetryableError, match='503'):
            client.get(URL)
        assert len(requests) == 4

    @pytest.mark.parametrize('answer', [httpx.Response(404), httpx.Response(403, text='Undeclared Automated Tool')])
    def test_any_other_answer_raises_permanent_error_at_once(
        self, monkeypatch: pytest.MonkeyPatch, answer: httpx.Response
    ) -> None:
        client, requests = edgar(monkeypatch, answer)
        with pytest.raises(PermanentError, match=str(answer.status_code)):
            client.get(URL)
        assert len(requests) == 1

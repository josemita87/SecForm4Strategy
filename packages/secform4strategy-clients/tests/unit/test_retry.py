"""Adapters repeat a transient failure with growing, jittered waits, and give up after a bounded number of attempts.

Under test:
    packages/secform4strategy-clients/src/secform4strategy_clients/retry.py
"""

from collections.abc import Callable

import pytest
from secform4strategy_clients.ports import PermanentError, RetryableError
from secform4strategy_clients.retry import with_backoff


def flaky(*outcomes: object) -> tuple[list[int], Callable[[], object]]:
    """A call that raises or returns each outcome in turn, and the list counting its calls."""
    calls: list[int] = []

    def call() -> object:
        calls.append(1)
        if isinstance(outcome := outcomes[len(calls) - 1], Exception):
            raise outcome
        return outcome

    return calls, call


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record each wait instead of sleeping."""
    recorded: list[float] = []
    monkeypatch.setattr('secform4strategy_clients.retry.time.sleep', recorded.append)
    return recorded


class TestWithBackoff:
    """A ``RetryableError`` is repeated; anything else, or the last failure, reaches the caller."""

    def test_a_transient_failure_is_repeated_until_it_succeeds(self, waits: list[float]) -> None:
        calls, call = flaky(RetryableError('503'), RetryableError('503'), 'ok')
        assert with_backoff(call, attempts=4, base_seconds=1) == 'ok'
        assert len(calls) == 3
        assert 0 <= waits[0] <= 1
        assert 0 <= waits[1] <= 2

    def test_the_last_failure_is_raised_after_every_attempt(self, waits: list[float]) -> None:
        calls, call = flaky(*(RetryableError('503'),) * 3)
        with pytest.raises(RetryableError):
            with_backoff(call, attempts=3)
        assert (len(calls), len(waits)) == (3, 2)

    def test_a_permanent_failure_is_not_repeated(self, waits: list[float]) -> None:
        calls, call = flaky(PermanentError('404'), 'ok')
        with pytest.raises(PermanentError):
            with_backoff(call)
        assert (len(calls), waits) == (1, [])

    def test_waits_are_capped(self, waits: list[float]) -> None:
        _, call = flaky(*(RetryableError('503'),) * 5, 'ok')
        with_backoff(call, attempts=6, base_seconds=10, max_seconds=15)
        assert max(waits) <= 15

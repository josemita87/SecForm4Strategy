"""A schedule is on only when it exists and is enabled.

Under test:
    packages/secform4strategy-clients/src/secform4strategy_clients/scheduler.py
"""

from collections.abc import Iterator

import pytest
from botocore.stub import Stubber
from secform4strategy_clients.scheduler import Scheduler


@pytest.fixture
def scheduler(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Scheduler, Stubber]]:
    """A scheduler whose client answers from a stubber, which must use every answer it is given."""
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'test')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'test')
    schedules = Scheduler('us-east-1')
    with Stubber(schedules.scheduler) as stubber:
        yield schedules, stubber
        stubber.assert_no_pending_responses()


def test_a_missing_schedule_is_off(scheduler: tuple[Scheduler, Stubber]) -> None:
    schedules, stubber = scheduler
    stubber.add_client_error('get_schedule', 'ResourceNotFoundException', http_status_code=404)
    assert schedules.enabled('secform4strategy-live-poll') is False

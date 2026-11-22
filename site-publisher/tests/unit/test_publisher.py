"""Which files an invocation publishes, and one failing file sparing the others.

Under test:
    site-publisher/src/publisher.py
"""

from datetime import UTC, datetime

import pytest
from constants import Mode, Section
from publisher import due, publish
from records import RunCounts


class MemorySite:
    """The site's data files in memory."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def read(self, name: str) -> bytes | None:
        return self.files.get(name)

    def write(self, name: str, body: bytes) -> None:
        self.files[name] = body


def at_minute(minute: int) -> datetime:
    return datetime(2026, 11, 23, 15, minute, tzinfo=UTC)


def fails() -> RunCounts:
    raise RuntimeError('source down')


class TestDue:
    """live publishes live.json; all publishes everything, explore.json every 15 minutes or when absent."""

    def test_live_mode(self) -> None:
        assert due(Mode.LIVE, at_minute(15), False, 15) == (Section.LIVE,)

    def test_all_mode_off_the_interval_skips_explore(self) -> None:
        assert Section.EXPLORE not in due(Mode.ALL, at_minute(7), True, 15)
        assert set(due(Mode.ALL, at_minute(7), True, 15)) == set(Section) - {Section.EXPLORE}

    @pytest.mark.parametrize(('minute', 'published'), [(0, True), (45, True), (7, False)])
    def test_all_mode_includes_explore(self, minute: int, published: bool) -> None:
        assert Section.EXPLORE in due(Mode.ALL, at_minute(minute), published, 15)


class TestPublish:
    """Each section is written on its own; only every section failing fails the invocation."""

    def test_one_failure_spares_the_others(self) -> None:
        site = MemorySite()
        outcomes = publish(
            {
                Section.LIVE: fails,
                Section.HEALTH: lambda: RunCounts(succeeded=1, failed=0, running=0),
                Section.MODEL: lambda: None,
            },
            site,
        )
        assert outcomes == {Section.LIVE: 'failed', Section.HEALTH: 'written', Section.MODEL: 'current'}
        assert site.files == {'health.json': b'{"succeeded":1,"failed":0,"running":0}'}

    def test_every_section_failing_raises(self) -> None:
        with pytest.raises(RuntimeError, match='No data file published'):
            publish({Section.LIVE: fails, Section.HEALTH: fails}, MemorySite())

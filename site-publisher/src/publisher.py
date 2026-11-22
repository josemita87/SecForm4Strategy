"""Which data files an invocation publishes, and publishing them so that one failing file spares the others."""

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from constants import Mode, Section, file_name

if TYPE_CHECKING:
    from pydantic import BaseModel
    from secform4strategy_clients.ports import Site

logger = logging.getLogger(__name__)

#: How one file's publication ended: written, skipped as current, or failed.
Outcome = Literal['written', 'current', 'failed']


def due(mode: Mode, now: datetime, explore_published: bool, explore_every_minutes: int) -> tuple[Section, ...]:
    """Return the sections an invocation publishes.

    ``live`` follows a live-score run and publishes live.json only. ``all`` runs every minute and publishes every
    section; explore.json only on its interval, or when it was never published.

    Args:
        mode: The invocation's mode.
        now: The invocation time.
        explore_published: Whether explore.json exists.
        explore_every_minutes: The interval of explore.json, in minutes.

    Returns:
        The sections to publish.
    """
    if mode is Mode.LIVE:
        return (Section.LIVE,)
    explore = not explore_published or now.minute % explore_every_minutes == 0
    return tuple(section for section in Section if section is not Section.EXPLORE or explore)


def publish(builders: Mapping[Section, Callable[[], BaseModel | None]], site: Site) -> dict[Section, Outcome]:
    """Build and write each section's file; a builder returning None leaves its file as it is.

    Args:
        builders: The builder per section to publish.
        site: Where the files are written.

    Returns:
        How each section ended.

    Raises:
        RuntimeError: Every section failed.
    """
    outcomes: dict[Section, Outcome] = {}
    for section, build in builders.items():
        try:
            if (report := build()) is None:
                outcomes[section] = 'current'
                continue
            site.write(file_name(section), report.model_dump_json().encode())
            outcomes[section] = 'written'
        except Exception:
            logger.exception(f'{file_name(section)} not published')
            outcomes[section] = 'failed'
    logger.info(f'Published: {outcomes}')
    if outcomes and all(outcome == 'failed' for outcome in outcomes.values()):
        raise RuntimeError(f'No data file published: {sorted(outcomes)}')
    return outcomes

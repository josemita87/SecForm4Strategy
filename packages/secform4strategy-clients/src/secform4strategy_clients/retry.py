"""Bounded retries with exponential backoff, for adapters to absorb transient failures (docs/error-handling.md)."""

import logging
import random
import time
from collections.abc import Callable

from secform4strategy_clients.ports import RetryableError

logger = logging.getLogger(__name__)


def with_backoff[T](
    call: Callable[[], T],
    attempts: int = 4,
    base_seconds: float = 1,
    max_seconds: float = 30,
) -> T:
    """Return ``call()``, repeating it after each ``RetryableError`` with exponential backoff and full jitter.

    The wait after attempt ``n`` is uniform between 0 and ``min(max_seconds, base_seconds * 2**(n - 1))``.

    Args:
        call: The operation; it raises ``RetryableError`` for a failure worth repeating.
        attempts: Most calls made, the first included.
        base_seconds: Upper bound of the wait after the first attempt.
        max_seconds: Cap on the upper bound of any wait.

    Returns:
        What ``call`` returns.
    """
    for attempt in range(1, attempts):
        try:
            return call()
        except RetryableError as error:
            delay = random.uniform(0, min(max_seconds, base_seconds * 2 ** (attempt - 1)))
            logger.warning(f'Attempt {attempt} of {attempts} failed, retrying in {delay:.1f}s: {error}')
            time.sleep(delay)
    return call()

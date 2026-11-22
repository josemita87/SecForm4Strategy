"""Amazon EventBridge Scheduler schedules (a ``Schedules`` adapter).

botocore retries throttling and server errors itself, so the client only classifies the failures left over
with ``aws.classified`` (docs/error-handling.md).
"""

from typing import TYPE_CHECKING

import boto3

from secform4strategy_clients.aws import RETRIES, classified

if TYPE_CHECKING:
    from mypy_boto3_scheduler import EventBridgeSchedulerClient


class Scheduler:
    """Read whether schedules are enabled.

    Attributes:
        scheduler: The EventBridge Scheduler client, retrying throttling and server errors in botocore's standard mode.
    """

    def __init__(self, region: str) -> None:
        """Open an EventBridge Scheduler client; credentials come from the environment, as for any AWS SDK.

        Args:
            region: AWS region of the schedules.
        """
        self.scheduler: EventBridgeSchedulerClient = boto3.client('scheduler', region_name=region, config=RETRIES)

    def enabled(self, name: str) -> bool:
        """Return whether the schedule exists and is enabled.

        Args:
            name: The schedule's name, in the default group.

        Returns:
            True when its state is ``ENABLED``.
        """
        return classified(f'Read schedule {name}', self._enabled, name)

    def _enabled(self, name: str) -> bool:
        """Read the schedule's state; a missing schedule is off."""
        try:
            return bool(self.scheduler.get_schedule(Name=name)['State'] == 'ENABLED')
        except self.scheduler.exceptions.ResourceNotFoundException:
            return False

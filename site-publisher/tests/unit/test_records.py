"""Published JSON: timestamps in UTC ending in Z, NaN and infinity as null, naive timestamps refused.

Under test:
    site-publisher/src/records.py
"""

import json
import math
from datetime import UTC, datetime, timedelta, timezone

import pandas as pd
import pytest
from pydantic import ValidationError
from records import Health, LambdaHealth, RunCounts, Switches

T0 = datetime(2026, 11, 23, 15, 2, 11, 123456, tzinfo=UTC)


def report(p90: float, generated_at: datetime = T0) -> Health:
    return Health(
        generated_at=generated_at,
        switches=Switches(stack=True, live_poller=True, weekly_retrain=False),
        queues=(),
        lambdas=(LambdaHealth(name='inference', invocations_1h=1, errors_1h=0, p90_duration_ms=p90),),
        live_runs_24h=RunCounts(succeeded=0, failed=0, running=0),
        retrain=None,
        model=None,
    )


class TestTimestamps:
    """Every timestamp is ISO 8601 UTC to the second, ending in Z."""

    def test_utc_ends_in_z_without_fraction(self) -> None:
        assert json.loads(report(1.0).model_dump_json())['generated_at'] == '2026-11-23T15:02:11Z'

    def test_other_zones_are_converted(self) -> None:
        madrid = T0.astimezone(timezone(timedelta(hours=1)))
        assert json.loads(report(1.0, madrid).model_dump_json())['generated_at'] == '2026-11-23T15:02:11Z'

    def test_pandas_timestamps_are_accepted(self) -> None:
        stamp = pd.Timestamp('2026-11-23 15:02:11', tz='UTC')
        assert json.loads(report(1.0, stamp).model_dump_json())['generated_at'] == '2026-11-23T15:02:11Z'

    def test_naive_timestamps_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            report(1.0, datetime(2026, 11, 23, 15, 2))  # noqa: DTZ001


class TestMissingNumbers:
    """NaN and infinity, nested or not, are written as null."""

    @pytest.mark.parametrize('value', [math.nan, math.inf])
    def test_non_finite_is_null(self, value: float) -> None:
        assert json.loads(report(value).model_dump_json())['lambdas'][0]['p90_duration_ms'] is None

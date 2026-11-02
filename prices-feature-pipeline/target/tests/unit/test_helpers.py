"""Acceptance times map to the next tradable market price and to their New York day.

Under test:
    prices-feature-pipeline/target/src/helpers.py
"""

import pandas as pd
import pytest
from src.helpers import instant, next_print, wall_clock

from tests.factories import ny


class TestNextPrint:
    """The first scheduled 09:30 open or 16:00 close strictly after acceptance (US/Eastern)."""

    @pytest.mark.parametrize(
        ('accepted', 'expected'),
        [
            ('2024-03-05 08:00', '2024-03-05 09:30'),  # before the open
            ('2024-03-05 09:30', '2024-03-05 16:00'),  # exactly at the open: strictly after it
            ('2024-03-05 15:59', '2024-03-05 16:00'),  # in session
            ('2024-03-05 16:00', '2024-03-06 09:30'),  # exactly at the close: the next open
            ('2024-03-08 18:00', '2024-03-11 09:30'),  # Friday evening: Monday
            ('2024-03-09 10:00', '2024-03-11 09:30'),  # Saturday
            ('2024-03-10 10:00', '2024-03-11 09:30'),  # Sunday, the day clocks spring forward
            ('2024-11-03 18:00', '2024-11-04 09:30'),  # Sunday, the day clocks fall back
        ],
    )
    def test_next_print(self, accepted: str, expected: str) -> None:
        assert next_print(pd.Series([ny(accepted)])).iloc[0] == ny(expected)


class TestMarketClock:
    """Instants map to New York wall-clock times and back."""

    def test_a_late_evening_acceptance_keeps_its_new_york_day(self) -> None:
        # 23:30 in New York is already the next day in UTC
        assert wall_clock(pd.Series([ny('2024-03-05 23:30')])).dt.normalize().iloc[0] == pd.Timestamp('2024-03-05')

    def test_a_wall_clock_time_round_trips_across_daylight_saving(self) -> None:
        moments = pd.Series([ny('2024-03-08 16:00'), ny('2024-03-11 16:00')])
        assert instant(wall_clock(moments)).equals(moments)

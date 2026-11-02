"""Labels enter at the row's next print (or the first real one after it) and exit a period later, or stay missing.

Under test:
    prices-feature-pipeline/target/src/main.py: compute_target
"""

import pandas as pd
import pytest
from src.helpers import next_print
from src.main import compute_target

from tests.factories import daily_bars, ny

DAYS = pd.bdate_range('2024-03-01', '2024-05-31')
#: Every day opens at 100 + i and closes at 100.5 + i, so each price names its day and side
BARS = daily_bars(DAYS, opens=[100.0 + i for i in range(len(DAYS))], closes=[100.5 + i for i in range(len(DAYS))])


def price(day: str, side: str) -> float:
    """The open or close of ``day`` in ``BARS``."""
    i = DAYS.get_loc(pd.Timestamp(day))
    return 100.0 + i + (0.5 if side == 'close' else 0.0)


def row(accepted: str) -> pd.DataFrame:
    """One disclosure row of ticker AAA accepted at ``accepted`` (New York time), with its next print."""
    accepted_at = pd.Series([ny(accepted)])
    return pd.DataFrame(
        {'key': ['k'], 'ticker': ['AAA'], 'accepted_at': accepted_at, 'next_print': next_print(accepted_at)}
    )


def label(accepted: str, bars: pd.DataFrame = BARS, period: int = 30) -> pd.Series:
    """The label of one row of ticker AAA accepted at ``accepted`` (New York time)."""
    return compute_target(row(accepted), bars, period).iloc[0]


class TestEntry:
    """The first open or close strictly after acceptance."""

    def test_an_after_hours_filing_enters_at_the_next_open(self) -> None:
        # Exit: the first close on or after 2024-03-06 + 30 days = 2024-04-05
        row = label('2024-03-05 18:00')
        assert row['pct_change'] == round(price('2024-04-05', 'close') / price('2024-03-06', 'open') - 1, 5)

    def test_an_in_session_filing_enters_at_the_same_days_close(self) -> None:
        row = label('2024-03-05 11:00')
        assert row['pct_change'] == round(price('2024-04-04', 'close') / price('2024-03-05', 'close') - 1, 5)

    def test_a_filing_at_exactly_the_close_waits_for_the_next_open(self) -> None:
        assert label('2024-03-05 16:00')['pct_change'] == label('2024-03-05 18:00')['pct_change']

    def test_a_next_print_on_a_holiday_rolls_to_the_next_real_open(self) -> None:
        # Good Friday 2024-03-29 has no bar: the row keyed to its open enters on Monday's
        holiday = BARS[BARS['date'] != '2024-03-29']
        row = label('2024-03-28 18:00', holiday)
        assert row['pct_change'] == round(price('2024-05-01', 'close') / price('2024-04-01', 'open') - 1, 5)


class TestExit:
    """The label becomes known at the exit close."""

    def test_a_weekend_exit_moves_to_the_next_close(self) -> None:
        # Entry Friday 2024-03-08 close; 30 days later is Sunday 2024-04-07, so Monday's close
        row = label('2024-03-08 11:00')
        assert row['pct_change'] == round(price('2024-04-08', 'close') / price('2024-03-08', 'close') - 1, 5)

    def test_the_label_is_known_at_the_exit_close(self) -> None:
        assert label('2024-03-05 18:00')['label_known_at'] == ny('2024-04-05 16:00')


class TestUnlabelled:
    """No label rather than a wrong one."""

    def test_an_exit_after_the_tickers_last_bar_is_not_labelled(self) -> None:
        stale = BARS[BARS['date'] <= '2024-03-29']
        assert compute_target(row('2024-03-05 18:00'), stale, 30).empty

    @pytest.mark.parametrize(
        ('missing', 'why'),
        [
            (('2024-04-02', '2024-04-30'), 'a gap of weeks around the exit'),
            (('2024-03-06', '2024-03-20'), 'no entry price within a week'),
        ],
    )
    def test_a_long_gap_in_the_bars_leaves_the_label_missing(self, missing: tuple[str, str], why: str) -> None:
        gappy = BARS[(BARS['date'] < missing[0]) | (BARS['date'] > missing[1])]
        assert compute_target(row('2024-03-05 18:00'), gappy, 30).dropna(subset=['label_known_at']).empty, why

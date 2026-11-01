"""Builders for daily price bars under test."""

from collections.abc import Mapping

import pandas as pd

TODAY = pd.Timestamp.now('America/New_York').normalize().tz_localize(None)


def bars(
    days: pd.DatetimeIndex, factor: float = 1.0, actions: Mapping[str, tuple[float, float]] | None = None
) -> pd.DataFrame:
    """Daily bars traded at 100, adjusted by ``factor``; ``actions`` maps a day to its (split, dividend).

    Args:
        days: The trading days.
        factor: Adjusted over as-traded close, the same on every day.
        actions: Day (``YYYY-MM-DD``) -> (split ratio, dividend) on that day.

    Returns:
        Bars in the market-data client's columns, without ``ticker``.
    """
    events = [(actions or {}).get(day.strftime('%Y-%m-%d'), (0.0, 0.0)) for day in days]
    return pd.DataFrame(
        {
            'date': days,
            'open': 100.0 * factor,
            'close': 100.0 * factor,
            'close_as_traded': 100.0,
            'split': [split for split, _ in events],
            'dividend': [dividend for _, dividend in events],
        }
    )


def stored(histories: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Bars as read back from the prices feature group: one frame tagged by ticker."""
    return pd.concat([frame.assign(ticker=ticker) for ticker, frame in histories.items()], ignore_index=True)

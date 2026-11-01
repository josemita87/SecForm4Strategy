"""A row's expanding average uses only its ticker's labels known at its acceptance, in training and inference alike.

Under test:
    prices-feature-pipeline/target/src/main.py: known_label_mean
"""

import numpy as np
import pandas as pd
from src.main import known_label_mean

from tests.factories import ny

LABELS = pd.DataFrame(
    {
        'ticker': ['AAA', 'AAA', 'AAA', 'BBB'],
        'label_known_at': [
            ny('2024-03-01 16:00'),
            ny('2024-03-10 16:00'),
            ny('2024-03-12 16:00'),
            ny('2024-03-01 16:00'),
        ],
        'pct_change': [0.10, 0.20, np.nan, 0.50],
    }
)


def means(*rows: tuple[str, str]) -> list[float]:
    """The known-label mean of each (ticker, New York acceptance time)."""
    queries = pd.DataFrame({'ticker': [t for t, _ in rows], 'accepted_at': [ny(a) for _, a in rows]})
    return known_label_mean(LABELS, queries).tolist()


class TestKnownLabelMean:
    """Only labels whose exit close is at or before acceptance count."""

    def test_only_labels_already_known_count(self) -> None:
        assert means(('AAA', '2024-03-05 18:00')) == [0.10]

    def test_a_label_known_at_the_very_moment_counts(self) -> None:
        assert means(('AAA', '2024-03-10 16:00')) == [0.15000000000000002]

    def test_missing_returns_are_skipped(self) -> None:
        assert means(('AAA', '2024-03-13 18:00')) == [0.15000000000000002]

    def test_no_known_label_gives_nan(self) -> None:
        assert all(np.isnan(means(('AAA', '2024-02-01 18:00'), ('CCC', '2024-03-05 18:00'))))

    def test_tickers_do_not_mix(self) -> None:
        assert means(('BBB', '2024-03-05 18:00'), ('AAA', '2024-03-05 18:00')) == [0.50, 0.10]

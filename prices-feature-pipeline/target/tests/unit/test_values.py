"""Rows are valued at the as-traded close of the last trading day before acceptance.

Under test:
    prices-feature-pipeline/target/src/main.py: add_value_features
"""

import math
from typing import TYPE_CHECKING

from src.main import add_value_features, aggregate_disclosures

from tests.factories import daily_bars, transactions

if TYPE_CHECKING:
    import pandas as pd

#: Friday and Monday bars: adjusted closes 100, as-traded 200 then 210 (a later 2-for-1 split)
BARS = daily_bars(['2024-03-08', '2024-03-11'], closes=[100.0, 105.0], as_traded=[200.0, 210.0])


def valued(**override: object) -> pd.Series:
    """Value a one-transaction row accepted on Monday evening against ``BARS``."""
    rows = aggregate_disclosures(transactions({'accepted_at': '2024-03-11 18:00', 'date': '2024-03-11'} | override))
    return add_value_features(rows, BARS).iloc[0]


class TestValuation:
    """One market price per row: known at disclosure and in that day's shares."""

    def test_monday_acceptance_is_valued_at_fridays_as_traded_close(self) -> None:
        # Monday's close prints the same day, so the last day strictly before acceptance is Friday
        assert valued(shares=10.0)['tx_value'] == 10.0 * 200.0

    def test_derivative_shares_are_valued_at_the_stocks_close(self) -> None:
        assert valued(derivative=True, shares=4.0)['derivative_value'] == 4.0 * 200.0

    def test_total_held_adds_traded_and_untraded_accounts(self) -> None:
        row = valued(remaining_shares=5.0, direct_holding=2.0, indirect_holding=1.0)
        assert row['total_held'] == (5.0 + 2.0 + 1.0) * 200.0

    def test_share_counts_give_way_to_usd_values(self) -> None:
        assert not {'shares', 'derivative_shares', 'remaining_shares'} & set(valued().index)


class TestPriceToClose:
    """The insider's reported price is compared with that close."""

    def test_a_reported_price_is_divided_by_the_close(self) -> None:
        assert valued(price=220.0)['price_to_close'] == 1.1

    def test_a_price_in_another_unit_leaves_it_missing(self) -> None:
        assert math.isnan(valued(price=7983.0)['price_to_close'])

    def test_no_reported_price_leaves_it_missing(self) -> None:
        assert math.isnan(valued(price=0.0)['price_to_close'])

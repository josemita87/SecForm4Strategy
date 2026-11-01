"""Transactions collapse into one row per ticker and trading opportunity, with the insiders that preceded it.

Under test:
    prices-feature-pipeline/target/src/main.py: aggregate_disclosures, add_cluster_features
"""

import pandas as pd
import pytest
from src.main import add_cluster_features, aggregate_disclosures

from tests.factories import transactions


def one_row(*overrides: dict[str, object]) -> pd.Series:
    """Aggregate transactions that must collapse into a single disclosure row, and return it."""
    rows = aggregate_disclosures(transactions(*overrides))
    assert len(rows) == 1
    return rows.iloc[0]


class TestGrouping:
    """Everything disclosed before the same tradable price is one row."""

    def test_filings_before_the_same_open_share_a_row(self) -> None:
        row = one_row(
            {'accession': 'A-1', 'insider_cik': 'I1', 'accepted_at': '2024-03-05 17:00'},
            {'accession': 'A-2', 'insider_cik': 'I2', 'accepted_at': '2024-03-05 19:30'},
        )
        assert (row['n_filings'], row['n_insiders']) == (2, 2)
        assert row['accepted_at'] == pd.Timestamp('2024-03-05 19:30', tz='America/New_York')
        assert row['key'] == 'AAA@2024-03-06T14:30Z'

    def test_filings_either_side_of_a_close_get_separate_rows(self) -> None:
        rows = aggregate_disclosures(
            transactions({'accepted_at': '2024-03-05 11:00'}, {'accession': 'A-2', 'accepted_at': '2024-03-05 17:00'})
        )
        assert sorted(rows['key']) == ['AAA@2024-03-05T21:00Z', 'AAA@2024-03-06T14:30Z']


class TestQuantities:
    """Stock and derivative quantities stay apart; positions and holdings are counted once."""

    def test_stock_and_derivative_shares_are_summed_apart(self) -> None:
        row = one_row({'shares': 100.0}, {'position': 1, 'shares': 50.0}, {'derivative': True, 'shares': 30.0})
        assert (row['shares'], row['derivative_shares']) == (150.0, 30.0)

    def test_the_price_averages_reported_stock_prices_only(self) -> None:
        row = one_row({'price': 10.0}, {'position': 1, 'price': 0.0}, {'derivative': True, 'price': 50.0})
        assert row['price'] == 10.0

    def test_remaining_shares_take_each_insiders_last_transaction_per_ownership_line(self) -> None:
        row = one_row(
            {'insider_cik': 'I1', 'position': 0, 'remaining_shares': 900.0},
            {'insider_cik': 'I1', 'position': 1, 'remaining_shares': 1100.0},  # a later buy: last, not lowest
            {'insider_cik': 'I1', 'position': 2, 'remaining_shares': 50.0, 'ownership': 'I'},  # held via a trust
            {'insider_cik': 'I2', 'accession': 'A-2', 'remaining_shares': 300.0},
        )
        assert row['remaining_shares'] == 1100.0 + 50.0 + 300.0

    def test_holdings_count_each_filing_once_from_its_stock_table(self) -> None:
        holdings = {'direct_holding': 500.0, 'indirect_holding': 20.0}
        # The derivative table's holdings come first, so only the stock-table rule picks the right ones
        row = one_row(
            {'derivative': True, 'direct_holding': 7.0, 'indirect_holding': 7.0},
            holdings,
            holdings | {'position': 1},
        )
        assert (row['direct_holding'], row['indirect_holding']) == (500.0, 20.0)


class TestFlags:
    """Codes and roles become flags; the filing lag runs from the earliest trade."""

    def test_codes_and_roles_are_flagged(self) -> None:
        row = one_row(
            {'coding': 'S', 'owner_code': '1000'},
            {'position': 1, 'coding': 'G'},
            {'position': 2, 'coding': 'F'},
            {'derivative': True, 'coding': 'M'},
        )
        assert row[['sale', 'gift', 'tax_withholding', 'exercise', 'director', 'officer']].tolist() == [True] * 6

    def test_the_filing_lag_runs_from_the_earliest_trade_to_acceptance(self) -> None:
        row = one_row({'date': '2024-03-01'}, {'position': 1, 'date': '2024-03-04'})
        assert row['filing_lag_days'] == 4


class TestClusters:
    """Each row counts the distinct insiders, officers and filings of its ticker in the trailing window."""

    @pytest.fixture
    def counts(self) -> pd.Series:
        tx = transactions(
            {'accession': 'A-1', 'insider_cik': 'I1', 'accepted_at': '2024-02-01 18:00'},  # 33 days before: outside
            {'accession': 'A-2', 'insider_cik': 'I2', 'accepted_at': '2024-02-20 18:00', 'owner_code': '1000'},
            {'accession': 'A-3', 'insider_cik': 'I3', 'accepted_at': '2024-03-05 18:00'},  # the row itself
            {'accession': 'A-4', 'insider_cik': 'I4', 'accepted_at': '2024-03-20 18:00'},  # later: not yet public
            {'accession': 'B-1', 'insider_cik': 'I5', 'accepted_at': '2024-03-04 18:00', 'ticker': 'BBB'},
        )
        rows = add_cluster_features(aggregate_disclosures(tx), tx, pd.Timedelta(days=30))
        return rows.set_index('key').loc['AAA@2024-03-06T14:30Z']

    def test_the_window_counts_only_filings_public_within_it(self, counts: pd.Series) -> None:
        assert (counts['insiders_recent'], counts['filings_recent']) == (2, 2)

    def test_officers_are_counted_among_them(self, counts: pd.Series) -> None:
        assert counts['officers_recent'] == 1

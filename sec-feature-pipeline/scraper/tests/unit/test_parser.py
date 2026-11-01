"""A real Form 4 submission becomes one JSON-ready record per transaction, timed at SEC acceptance.

Under test:
    sec-feature-pipeline/scraper/src/parser.py
    sec-feature-pipeline/scraper/src/form4.py
    sec-feature-pipeline/scraper/src/transaction.py
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from form4 import Submission
from parser import parse_filing
from pydantic import ValidationError
from secform4strategy_clients.fakes.filings import FakeFilings
from secform4strategy_clients.ports import Filing
from tests.paths import BOTH_HOLDINGS, FOOTNOTE_10B5_1, JOINT_FILING, OWNERSHIP_LINES, STOCK_AND_DERIVATIVE
from transaction import Trade


def parse(fixture: Path, text: str | None = None) -> list[dict[str, object]]:
    """Parse a fixture as the scraper would, served by a fake EDGAR (``text`` overrides its content)."""
    filing = Filing(fixture.stem, f'edgar/data/1/{fixture.name}', datetime(2023, 1, 1, tzinfo=UTC))
    url = f'https://www.sec.gov/Archives/{filing.file_path}'
    return parse_filing(filing, FakeFilings({url: fixture.read_text() if text is None else text}))


class TestSubmission:
    """The SGML wrapper yields the acceptance time and the ownership document."""

    def test_acceptance_time_is_read_in_new_york_time(self) -> None:
        assert {r['accepted_at'] for r in parse(FOOTNOTE_10B5_1)} == {'2023-03-22T21:52:41-04:00'}

    def test_a_malformed_acceptance_stamp_fails_validation(self) -> None:
        with pytest.raises(ValidationError, match='accepted_at'):
            Submission.model_validate({'accepted_at': '20231399000000', 'document': b'<ownershipDocument/>'})

    def test_a_filing_that_is_not_served_yields_no_records(self) -> None:
        filing = Filing('0000000000-23-000000', 'edgar/data/1/missing.txt', datetime(2023, 1, 1, tzinfo=UTC))
        assert parse_filing(filing, FakeFilings({})) == []

    def test_a_filing_without_acceptance_header_yields_no_records(self) -> None:
        text = FOOTNOTE_10B5_1.read_text().replace('<ACCEPTANCE-DATETIME>', '<NO-ACCEPTANCE>')
        assert parse(FOOTNOTE_10B5_1, text) == []


class TestRecords:
    """A record carries the published fields only, ready to serialize."""

    def test_records_are_json_ready_and_keep_inputs_out(self) -> None:
        record = parse(FOOTNOTE_10B5_1)[0]
        assert isinstance(record['accepted_at'], str)
        assert {'key', 'owner_code', 'accession', 'position'} <= record.keys()
        assert not {'director', 'officer', 'ten_percent_owner', 'other'} & record.keys()

    def test_keys_are_deterministic_and_unique_per_transaction(self) -> None:
        first, again = parse(OWNERSHIP_LINES), parse(OWNERSHIP_LINES)
        assert [r['key'] for r in first] == [r['key'] for r in again]
        assert len({r['key'] for r in first}) == len(first) == 5

    def test_the_table_sets_derivative_and_positions_count_per_table(self) -> None:
        records = parse(FOOTNOTE_10B5_1)
        assert [(r['derivative'], r['position']) for r in records] == [(False, 0), (True, 0)]
        assert records[0]['key'] != records[1]['key']

    def test_an_unreported_price_takes_the_default_zero(self) -> None:
        stock = next(r for r in parse(STOCK_AND_DERIVATIVE) if not r['derivative'])
        assert stock['price'] == 0.0


class TestRule10b5One:
    """A transaction is flagged by the checkbox or by any 10b5-1 footnote attached to it."""

    def test_a_footnote_on_the_shares_flags_only_its_transaction(self) -> None:
        assert [r['rule105b1'] for r in parse(FOOTNOTE_10B5_1)] == [True, False]

    def test_a_footnote_on_the_transaction_code_flags_it(self) -> None:
        assert [r['rule105b1'] for r in parse(BOTH_HOLDINGS)] == [True]

    def test_the_checkbox_flags_every_transaction(self) -> None:
        assert all(r['rule105b1'] for r in parse(JOINT_FILING))


class TestOwners:
    """A joint filing is attributed to its first reporting owner."""

    def test_the_first_of_seven_owners_is_taken(self) -> None:
        records = parse(JOINT_FILING)
        assert {(r['insider_name'], r['owner_code']) for r in records} == {('GV 2019 GP, L.L.C.', '0010')}


class TestHoldings:
    """Holdings are set only when the filing lists both direct and indirect ones."""

    def test_both_listed_sets_them(self) -> None:
        record = parse(BOTH_HOLDINGS)[0]
        assert (record['direct_holding'], record['indirect_holding']) == (547990.0, pytest.approx(6535.45))

    def test_none_listed_leaves_them_zero(self) -> None:
        assert {(r['direct_holding'], r['indirect_holding']) for r in parse(OWNERSHIP_LINES)} == {(0.0, 0.0)}


class TestTicker:
    """Symbols are normalized; placeholders for "no symbol" become missing."""

    @pytest.mark.parametrize(('raw', 'ticker'), [(' nvda ', 'NVDA'), ('NONE', None), ('n/a', None), ('---', None)])
    def test_ticker_normalization(self, raw: str, ticker: str | None) -> None:
        trade = Trade.model_validate(
            {
                'link': 'l',
                'accepted_at': datetime(2023, 1, 1, tzinfo=UTC),
                'derivative': False,
                'accession': 'a',
                'position': 0,
                'ticker': raw,
            }
        )
        assert trade.ticker == ticker

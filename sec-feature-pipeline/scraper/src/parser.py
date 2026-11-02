"""Form 4 parsing service: fetch a filing, read its ownership document, and map it to transaction records.

The mapping decisions (first reporting owner, 10b5-1, holdings, keys) are in docs/form4-parsing.md.
"""

import contextlib
import logging
import re
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple

from form4 import ReportingOwner, Submission
from pydantic import ValidationError
from transaction import Trade

if TYPE_CHECKING:
    from form4 import OwnershipDocument, Position, Transaction
    from secform4strategy_clients.ports import Filing, Filings

logger = logging.getLogger(__name__)


class Holdings(NamedTuple):
    """Shares a table lists as held directly and indirectly, named as the ``Trade`` fields they fill."""

    direct_holding: float
    indirect_holding: float


def parse_filing(filing: Filing, filings: Filings) -> list[dict[str, object]]:
    """Fetch a Form 4 filing and return its transaction records.

    Every transaction carries the filing's acceptance time as ``accepted_at``: the moment
    it became public, which is when a trader could act on it. Records are JSON-ready (times
    as ISO 8601). A transaction whose fields fail validation is logged and skipped.

    Args:
        filing: The filing to fetch (its archive path and accession).
        filings: SEC filings source.

    Returns:
        The transaction records; none if the filing cannot be fetched or parsed (logged).
    """
    if not (submission := _fetch_submission(full_url := f'https://www.sec.gov/Archives/{filing.file_path}', filings)):
        return []
    return [
        trade.model_dump(mode='json')
        for trade in trades(submission.document, filing.accession, full_url, submission.accepted_at)
    ]


def trades(document: OwnershipDocument, accession: str, link: str, accepted_at: datetime) -> list[Trade]:
    """Map an ownership document to one trade per transaction of its first reporting owner.

    A transaction is flagged Rule 10b5-1 by the filing's checkbox or by a footnote attached
    anywhere in it mentioning ``10b5-1``. Holdings are summed per ownership from the transaction's
    own table, 0 when the table lists none.

    Args:
        document: The filing's ownership document.
        accession: The filing's accession number, which keys its trades.
        link: Filing URL, recorded on every trade.
        accepted_at: When SEC accepted the filing.

    Returns:
        The valid trades, non-derivative first.
    """
    owner = document.owners[0] if document.owners else ReportingOwner()
    notes = {note.id: note.text or '' for note in document.footnotes if note.id}
    records = []
    for derivative, table in ((False, document.non_derivative), (True, document.derivative)):
        holdings = _holdings(table.holdings)
        for position, tx in enumerate(table.transactions):
            fields = {
                'company_cik': document.issuer.cik,
                'ticker': document.issuer.ticker,
                'insider_cik': owner.id.cik,
                'insider_name': owner.id.name,
                'director': owner.relationship.director,
                'officer': owner.relationship.officer,
                'ten_percent_owner': owner.relationship.ten_percent_owner,
                'other': owner.relationship.other,
                'rule105b1': _has_10b5_1_footnote(tx, notes) or document.aff10b5_one,
                'shares': tx.amounts.shares.value,
                'acquired_disposed': tx.amounts.acquired_disposed.value,
                'price': tx.amounts.price.value,
                'date': tx.date.value,
                'remaining_shares': tx.shares_owned.value,
                'ownership': tx.ownership.value,
                'coding': tx.coding.code,
                'equity_swap': tx.coding.equity_swap,
            }
            try:
                # Absent values fall back to the record's defaults
                records.append(
                    Trade.model_validate(
                        {
                            'link': link,
                            'accepted_at': accepted_at,
                            'derivative': derivative,
                            'accession': accession,
                            'position': position,
                        }
                        | {name: value for name, value in fields.items() if value}
                        | holdings._asdict()
                    )
                )
            except ValidationError as e:
                logger.warning(f'Skipping malformed transaction in {link}: {e}')
    return records


def _has_10b5_1_footnote(tx: Transaction, notes: dict[str, str]) -> bool:
    """Whether a footnote attached anywhere in the transaction mentions Rule 10b5-1.

    Filers attach it mostly to the transaction code, sometimes to the shares or the security
    title; before April 2023 Form 4 had no 10b5-1 checkbox, so the footnote is the only flag.
    """
    refs = [
        *tx.coding.footnotes,
        *(
            ref
            for value in (
                tx.security_title,
                tx.date,
                tx.amounts.shares,
                tx.amounts.price,
                tx.amounts.acquired_disposed,
                tx.shares_owned,
                tx.ownership,
            )
            for ref in value.footnotes
        ),
    ]
    return any('10b5-1' in notes.get(ref.id or '', '') for ref in refs)


def _holdings(holdings: Iterable[Position]) -> Holdings:
    """Sum the shares held directly and indirectly, skipping amounts that are not numbers."""
    amounts = {'D': 0.0, 'I': 0.0}
    for holding in holdings:
        if holding.ownership.value in amounts and holding.shares_owned.value:
            with contextlib.suppress(ValueError):
                amounts[holding.ownership.value] += float(holding.shares_owned.value)
    return Holdings(amounts['D'], amounts['I'])


def _fetch_submission(full_url: str, filings: Filings) -> Submission | None:
    """Fetch the full submission and validate its acceptance time and ownership document.

    The submission is an SGML wrapper, not XML: its header carries ``<ACCEPTANCE-DATETIME>`` and its
    ``<XML>`` block the ownership document, so each is cut out and ``Submission`` types them.

    Args:
        full_url: Absolute filing URL.
        filings: SEC filings source.

    Returns:
        The submission, or None (logged) if fetching or parsing fails.
    """
    try:
        if (xml := re.search(r'<XML>\s*(<\?xml[^>]+?>.*?)</XML>', text := filings.get(full_url).text, re.DOTALL)) and (
            accepted := re.search(r'<ACCEPTANCE-DATETIME>(\d{14})', text)
        ):
            return Submission.model_validate({'accepted_at': accepted.group(1), 'document': xml.group(1).encode()})
        error = 'no embedded XML document or acceptance time'
    except Exception as e:
        error = str(e)
    logger.warning(f'Could not fetch or parse {full_url}: {error}')
    return None

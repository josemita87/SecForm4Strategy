"""Form 4 parsing service: fetch a filing, read its ownership document, and map it to transaction records."""

import contextlib
import logging
import re
import time
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from form4 import OwnershipDocument
from pydantic import ValidationError
from transaction import Trade

if TYPE_CHECKING:
    from form4 import Position, Transaction
    from secform4strategy_clients.ports import Filing, Filings

logger = logging.getLogger(__name__)


def parse_filing(filing: Filing, filings: Filings, sleep_time: float = 0.1) -> list[dict[str, object]]:
    """Fetch a Form 4 filing and return its transaction records.

    Every transaction carries the filing's acceptance time as ``timestamp`` (epoch
    ms): the moment it became public, which is when a trader could act on it. A
    transaction whose fields fail validation is logged and skipped.

    Args:
        filing: The filing to fetch (its archive path and accession).
        filings: SEC filings source.
        sleep_time: Seconds to sleep before fetching, to throttle requests.

    Returns:
        The transaction records; none if the filing cannot be fetched or parsed (logged).
    """
    full_url = f'https://www.sec.gov/Archives/{filing.file_path}'
    time.sleep(sleep_time)
    if (fetched := _fetch_filing(full_url, filings)) is None:
        return []
    document, accepted_ms = fetched
    return [trade.model_dump() for trade in trades(document, filing.accession, full_url, accepted_ms)]


def trades(document: OwnershipDocument, accession: str, link: str, accepted_ms: int) -> list[Trade]:
    """Map an ownership document to one trade per transaction of its first reporting owner.

    A transaction is flagged Rule 10b5-1 by the filing's checkbox or by a footnote on its
    shares mentioning ``10b5-1``. Holdings are summed from the transaction's own table and
    stay 0 unless the filing lists both direct and indirect ones.

    Args:
        document: The filing's ownership document.
        accession: The filing's accession number, which keys its trades.
        link: Filing URL, recorded on every trade.
        accepted_ms: SEC acceptance time, epoch ms.

    Returns:
        The valid trades, non-derivative first.
    """
    owner = next(iter(document.owners), None)
    notes = {note.id: note.text or '' for note in document.footnotes if note.id}
    records = []
    for derivative, table in ((False, document.non_derivative), (True, document.derivative)):
        direct, indirect = _holdings(table.holdings)
        for position, tx in enumerate(table.transactions):
            fields = {
                'company_cik': document.issuer.cik,
                'ticker': document.issuer.ticker,
                'insider_cik': owner and owner.id.cik,
                'insider_name': owner and owner.id.name,
                'director': owner and owner.relationship.director,
                'officer': owner and owner.relationship.officer,
                'ten_percent_owner': owner and owner.relationship.ten_percent_owner,
                'other': owner and owner.relationship.other,
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
                            'timestamp': accepted_ms,
                            'derivative': derivative,
                            'accession': accession,
                            'position': position,
                        }
                        | {name: value for name, value in fields.items() if value}
                        | ({'direct_holding': direct, 'indirect_holding': indirect} if direct and indirect else {})
                    )
                )
            except ValidationError as e:
                logger.warning(f'Skipping malformed transaction in {link}: {e}')
    return records


def _has_10b5_1_footnote(tx: Transaction, notes: dict[str, str]) -> bool:
    """Whether a footnote on the transaction's shares mentions Rule 10b5-1."""
    return any('10b5-1' in notes.get(ref.id or '', '') for ref in tx.amounts.shares.footnotes)


def _holdings(holdings: Iterable[Position]) -> tuple[float, float]:
    """Sum the shares held directly and indirectly, skipping amounts that are not numbers."""
    amounts = {'D': 0.0, 'I': 0.0}
    for holding in holdings:
        if holding.ownership.value in amounts and holding.shares_owned.value:
            with contextlib.suppress(ValueError):
                amounts[holding.ownership.value] += float(holding.shares_owned.value)
    return amounts['D'], amounts['I']


def _fetch_filing(full_url: str, filings: Filings) -> tuple[OwnershipDocument, int] | None:
    """Fetch the filing and read its ownership document and acceptance time.

    The acceptance time is the ``<ACCEPTANCE-DATETIME>`` header, which EDGAR stamps in US/Eastern.

    Args:
        full_url: Absolute filing URL.
        filings: SEC filings source.

    Returns:
        The ownership document and the acceptance time (epoch ms), or None (logged)
        if fetching or parsing fails.
    """
    try:
        document = filings.get(full_url).text
        xml = re.search(r'<XML>\s*(<\?xml[^>]+?>.*?)</XML>', document, re.DOTALL)
        accepted = re.search(r'<ACCEPTANCE-DATETIME>(\d{14})', document)
        if xml is not None and accepted is not None:
            accepted_at = datetime.strptime(accepted.group(1), '%Y%m%d%H%M%S').replace(
                tzinfo=ZoneInfo('America/New_York')
            )
            return OwnershipDocument.from_xml(xml.group(1).encode()), int(accepted_at.timestamp() * 1000)
        error = 'no embedded XML document or acceptance time'
    except Exception as e:
        error = str(e)
    logger.warning(f'Could not fetch or parse {full_url}: {error}')
    return None

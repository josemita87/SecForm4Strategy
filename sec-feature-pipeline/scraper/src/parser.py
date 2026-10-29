"""Parse SEC Form 4 filings: fetch a filing and extract transaction dicts from its XML."""

import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from const import derivative_paths, derivative_root, insider_paths, paths, root
from pydantic import ValidationError
from transaction import EssentialTrade

if TYPE_CHECKING:
    from secform4strategy_clients.ports import Filings

logger = logging.getLogger(__name__)

BASE_URL = 'https://www.sec.gov/Archives/'
XML_DELIM = r'<XML>\s*(<\?xml[^>]+?>.*?)</XML>'


def parse_filing(url: str, filings: Filings, sleep_time: float = 0.1) -> list[dict[str, object]] | None:
    """Fetch a Form 4 filing and parse its transactions from the embedded XML.

    A transaction whose fields fail validation is logged and skipped; the rest of
    the filing is kept.

    Args:
        url: Filing path appended to the archives base URL.
        filings: SEC filings source.
        sleep_time: Seconds to sleep before fetching, to throttle requests.

    Returns:
        A list of transaction dicts, or None if the filing XML is missing.
    """
    full_url = BASE_URL + url
    time.sleep(sleep_time)
    if (filing_xml := _fetch_filing(full_url, filings)) is None:
        return None

    # Insider fields live at the filing root, transaction fields on each transaction node
    insider = _query_xml(filing_xml, insider_paths)
    trades = []
    for tx_root, tx_paths in ((root, paths), (derivative_root, derivative_paths)):
        for node in filing_xml.findall(tx_root):
            try:
                trade = EssentialTrade.model_validate(
                    {'link': full_url, 'xml': filing_xml, **insider, **_query_xml(node, tx_paths)}
                )
            except ValidationError as e:
                logger.warning(f'Skipping malformed transaction in {full_url}: {e}')
                continue
            trades.append(trade.to_dict())
    return trades


def _fetch_filing(full_url: str, filings: Filings) -> ET.Element | None:
    """Fetch the filing and extract its embedded XML document.

    Args:
        full_url: Absolute filing URL.
        filings: SEC filings source.

    Returns:
        The parsed XML element on success, or None (logged) if fetching or parsing fails.
    """
    try:
        if (match := re.search(XML_DELIM, filings.get(full_url).text, re.DOTALL)) is not None:
            return ET.fromstring(match.group(1))
        error = 'no embedded XML document'
    except Exception as e:
        error = str(e)
    logger.warning(f'Could not fetch or parse {full_url}: {error}')
    return None


def _query_xml(etree: ET.Element, query_paths: dict[str, str]) -> dict[str, str]:
    """Read the raw text of each present field; footnote fields take the footnote id instead.

    Args:
        etree: The element to query.
        query_paths: Output field name -> XML path of its value.

    Returns:
        Field name -> raw text, for the fields present in the XML.
    """
    return {
        key: value
        for key, path in query_paths.items()
        if (element := etree.find(path)) is not None
        and (value := element.get('id') if 'footnote_id' in key else element.text) is not None
    }

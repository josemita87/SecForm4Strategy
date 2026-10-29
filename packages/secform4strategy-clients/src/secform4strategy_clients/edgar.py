"""SEC EDGAR HTTP client.

Encapsulates how to talk to SEC EDGAR: the required ``User-Agent`` header, a
default timeout, polite rate-limiting, and EDGAR's file formats (the quarterly
master index, the current-filings Atom feed, the submission acceptance header).
Service logic (keys, timestamps, publishing) stays with the caller.
"""

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from http import HTTPStatus
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from requests import Response

logger = logging.getLogger(__name__)

ARCHIVES_URL = 'https://www.sec.gov/Archives/'
ATOM = {'atom': 'http://www.w3.org/2005/Atom'}
EASTERN = ZoneInfo('America/New_York')


class EdgarClient:
    """Perform HTTP GETs against SEC EDGAR with the required headers.

    Attributes:
        headers: Request headers sent with every GET (includes the SEC User-Agent).
        timeout: Default per-request timeout in seconds.
    """

    #: Current-filings Atom feed; ``owner=only`` lists ownership filings (Forms 3, 4, 5).
    CURRENT_FILINGS_FEED = (
        'https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form_type}&owner=only'
        '&start={start}&count=100&output=atom'
    )

    def __init__(
        self,
        user_agent: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 10,
    ) -> None:
        """Configure the client's headers and default timeout.

        Args:
            user_agent: Value for the ``User-Agent`` header (SEC requires one).
                Ignored if ``headers`` is given.
            headers: Full headers dict to use verbatim; overrides ``user_agent``.
            timeout: Default request timeout in seconds.
        """
        # Lazy import so the SDK is only required when this client is built.
        import requests

        self._requests = requests
        self.headers = (
            dict(headers)
            if headers is not None
            else {
                'Accept-Encoding': 'gzip, deflate',
                'Host': 'www.sec.gov',
                **({'User-Agent': user_agent} if user_agent else {}),
            }
        )
        self.timeout = timeout

    def get(self, url: str, timeout: int | None = None) -> Response:
        """GET a URL with the configured headers.

        Args:
            url: The full URL to request.
            timeout: Optional per-request timeout override (seconds).

        Returns:
            The raw ``requests.Response`` (caller reads ``.text``/``.content``/etc.).
        """
        response: Response = self._requests.get(
            url, headers=self.headers, timeout=self.timeout if timeout is None else timeout
        )
        return response

    #: Fields of each master-index line, in file order.
    MASTER_INDEX_FIELDS = ('cik', 'company', 'form_type', 'date', 'file_path')

    def fetch_master_index(self, year: int, quarter: str, throttle: float = 1.0) -> list[dict[str, str]] | None:
        """Fetch and parse an EDGAR quarterly master index.

        Knows the master.idx URL pattern and the file's pipe-delimited format
        (a preamble followed by a ``...|...`` header, then one filing per line
        with fields ``cik|company|form_type|date|file_path``). Callers get every
        filing as a dict of strings and apply their own form-type filtering.

        Args:
            year: Calendar year of the master index.
            quarter: Quarter identifier (e.g. ``"QTR1"``).
            throttle: Seconds to sleep after a successful fetch, to respect SEC
                rate limits. Set to 0 to disable.

        Returns:
            One dict per filing, or ``None`` if the request was not 200.
        """
        response = self.get(f'https://www.sec.gov/Archives/edgar/full-index/{year}/{quarter}/master.idx')
        if response.status_code != HTTPStatus.OK:
            return None

        # Crop the preamble: data starts two lines after the header row.
        lines = response.content.decode('utf-8').splitlines()
        start_index = next((i + 2 for i, line in enumerate(lines) if '|' in line), 0)
        # A company name may itself contain '|', so the CIK and the last three fields are split off around it
        filings = [
            dict(zip(self.MASTER_INDEX_FIELDS, (cik, *rest.rsplit('|', 3)), strict=True))
            for cik, rest in (line.split('|', 1) for line in lines[start_index:] if line.strip())
        ]

        if throttle:
            time.sleep(throttle)
        return filings

    def fetch_current_filings(self, form_type: str, num_pages: int = 1) -> list[dict[str, str]]:
        """Return the newest ownership filings of ``form_type`` from EDGAR's current-filings Atom feed.

        The feed lists a filing once per filer (issuer and reporting owner) and matches
        form types by prefix, so entries are de-duplicated by accession number and kept
        only on an exact form match.

        Args:
            form_type: Form type to keep, e.g. ``'4'``.
            num_pages: Feed pages of 100 entries to read, newest first.

        Returns:
            One dict per filing: ``accession``, ``file_path`` (archive path of the full
            submission) and ``date`` (the feed's ISO timestamp for the filing).
        """
        filings: dict[str, dict[str, str]] = {}
        for page in range(num_pages):
            response = self.get(self.CURRENT_FILINGS_FEED.format(form_type=form_type, start=page * 100))
            response.raise_for_status()
            for entry in ET.fromstring(response.content).iterfind('atom:entry', ATOM):
                accession = entry.findtext('atom:id', '', ATOM).rpartition('=')[2]
                category, link = entry.find('atom:category', ATOM), entry.find('atom:link', ATOM)
                if category is None or link is None or category.get('term') != form_type or accession in filings:
                    continue
                # The index page sits in the filer's directory, next to the full submission
                filer_dir = link.get('href', '').rsplit('/', 2)[0].removeprefix(ARCHIVES_URL)
                filings[accession] = {
                    'accession': accession,
                    'file_path': f'{filer_dir}/{accession}.txt',
                    'date': entry.findtext('atom:updated', '', ATOM),
                }
        return list(filings.values())


def acceptance_time(document: str) -> datetime | None:
    """Return when SEC accepted a filing, from the full submission's ``<ACCEPTANCE-DATETIME>`` header.

    Args:
        document: The full ``.txt`` submission.

    Returns:
        The timezone-aware acceptance time (EDGAR stamps it in US/Eastern), or None if absent.
    """
    if (match := re.search(r'<ACCEPTANCE-DATETIME>(\d{14})', document)) is None:
        return None
    return datetime.strptime(match.group(1), '%Y%m%d%H%M%S').replace(tzinfo=EASTERN)

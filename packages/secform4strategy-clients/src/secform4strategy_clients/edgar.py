"""SEC EDGAR HTTP client.

Encapsulates how to talk to SEC EDGAR: the required ``User-Agent`` header, a
default timeout, a request-rate cap, and EDGAR's listing formats (the quarterly
master index, the current-filings Atom feed). Service logic (keys, timestamps,
publishing) stays with the caller.
"""

import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from http import HTTPStatus

import httpx

from secform4strategy_clients.ports import Filing

ATOM = {'atom': 'http://www.w3.org/2005/Atom'}


class EdgarClient:
    """Perform HTTP GETs against SEC EDGAR over one pooled connection, at most ``max_rate`` per second.

    Request starts are spaced at least ``1 / max_rate`` apart; a request that comes after
    a longer gap goes out at once. SEC fair access allows 10 requests per second per IP; the default leaves room.

    Attributes:
        http: The HTTP client, carrying the SEC headers and default timeout.
    """

    def __init__(self, user_agent: str, max_rate: float = 8, timeout: float = 10) -> None:
        """Open the client with the SEC headers, a rate cap and a default timeout.

        Args:
            user_agent: ``User-Agent`` header; SEC fair access requires a real name and contact email.
            max_rate: Most requests started per second.
            timeout: Default request timeout in seconds.
        """
        self.http = httpx.Client(
            headers=httpx.Headers({'User-Agent': user_agent, 'Accept-Encoding': 'gzip, deflate'}),
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
        )
        self._interval = 1 / max_rate
        self._next_start = 0.0

    def get(self, url: str) -> httpx.Response:
        """GET a URL with the configured headers and timeout, waiting first if the rate cap requires it.

        Args:
            url: The full URL to request.

        Returns:
            The raw response; the caller checks its status.
        """
        if (wait := self._next_start - time.monotonic()) > 0:
            time.sleep(wait)
        self._next_start = time.monotonic() + self._interval
        return self.http.get(url)

    def fetch_master_index(self, year: int, quarter: int, form_type: str) -> tuple[Filing, ...] | None:
        """Fetch a quarterly master index and return its filings of ``form_type``.

        The index is a preamble, a ``...|...`` header, then one pipe-delimited line per
        filer of each filing (``cik|company|form_type|date|file_path``); a filing is kept
        once per accession and dated at UTC midnight of its filing day.

        Args:
            year: Calendar year of the master index.
            quarter: Quarter number, 1 to 4.
            form_type: Form type to keep, e.g. ``'4'``.

        Returns:
            One filing per accession, or ``None`` if the request was not 200.
        """
        if (
            response := self.get(f'https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/master.idx')
        ).status_code != HTTPStatus.OK:
            return None

        # Data starts two lines after the header row; a company name may contain '|', so split from the right
        lines = response.text.splitlines()
        start = next((i + 2 for i, line in enumerate(lines) if '|' in line), 0)
        rows = (line.rsplit('|', 3)[1:] for line in lines[start:] if line.strip())
        filings = {
            (accession := file_path.rsplit('/', 1)[-1].removesuffix('.txt')): Filing(
                accession, file_path, datetime.fromisoformat(filed).replace(tzinfo=UTC)
            )
            for form, filed, file_path in rows
            if form == form_type
        }
        return tuple(filings.values())

    def fetch_current_filings(self, form_type: str, num_pages: int = 1) -> tuple[Filing, ...]:
        """Return the newest ownership filings of ``form_type`` from EDGAR's current-filings Atom feed.

        The feed (``owner=only``: Forms 3, 4, 5) lists a filing once per filer and matches
        form types by prefix, so entries are kept once per accession and only on an exact
        form match.

        Args:
            form_type: Form type to keep, e.g. ``'4'``.
            num_pages: Feed pages of 100 entries to read, newest first.

        Returns:
            One filing per accession, timestamped with the feed's ``updated`` time.
        """
        filings: dict[str, Filing] = {}
        for page in range(num_pages):
            response = self.get(
                'https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent'
                f'&type={form_type}&owner=only&start={page * 100}&count=100&output=atom'
            )
            response.raise_for_status()
            for entry in ET.fromstring(response.content).iterfind('atom:entry', ATOM):
                accession = entry.findtext('atom:id', '', ATOM).rpartition('=')[2]
                category, link = entry.find('atom:category', ATOM), entry.find('atom:link', ATOM)
                if category is None or link is None or category.get('term') != form_type or accession in filings:
                    continue
                # The index page sits in the filer's directory, next to the full submission
                filer_dir = link.get('href', '').rsplit('/', 2)[0].removeprefix('https://www.sec.gov/Archives/')
                filings[accession] = Filing(
                    accession,
                    f'{filer_dir}/{accession}.txt',
                    datetime.fromisoformat(entry.findtext('atom:updated', '', ATOM)),
                )
        return tuple(filings.values())

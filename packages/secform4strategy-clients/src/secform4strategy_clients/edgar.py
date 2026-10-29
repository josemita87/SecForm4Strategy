"""SEC EDGAR HTTP client.

Encapsulates how to talk to SEC EDGAR: the required ``User-Agent`` header, a
default timeout, polite rate-limiting, and the EDGAR-specific file formats (e.g.
the quarterly master index). It does NOT hold service business logic — filtering
by form type, deriving keys/timestamps, and publishing are the caller's domain.
"""

from __future__ import annotations

import logging
import time
from http import HTTPStatus
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from requests import Response
    from selenium.webdriver.chrome.webdriver import WebDriver

logger = logging.getLogger(__name__)


class EdgarClient:
    """Perform HTTP GETs against SEC EDGAR with the required headers.

    Attributes:
        headers: Request headers sent with every GET (includes the SEC User-Agent).
        timeout: Default per-request timeout in seconds.
    """

    #: EDGAR current-filings page used to discover the newest Form 4 links.
    LIVE_FILINGS_URL = (
        'https://www.sec.gov/cgi-bin/browse-edgar?company=&CIK=&type=4&owner=include&count=100&action=getcurrent'
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

    def fetch_live_links(self, num_pages: int, wait_time: int = 10) -> set[str]:
        """Scrape the newest Form 4 filing links from EDGAR's current-filings page.

        Drives a headless browser over the paginated ``getcurrent`` results and
        collects relative ``.txt`` filing paths. This is SEC EDGAR interaction
        (infra); callers receive plain link strings and decide what to do with
        them. Requires the ``edgar-live`` extra (Selenium + BeautifulSoup).

        Args:
            num_pages: Number of paginated result pages to scrape.
            wait_time: Maximum seconds to wait for page elements to load.

        Returns:
            A set of relative ``.txt`` filing link paths.
        """
        import random

        from bs4 import BeautifulSoup
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        driver = self._configure_driver()
        wait = WebDriverWait(driver, wait_time)
        all_links: set[str] = set()

        # Always quit the browser, including when a page fails mid-pagination.
        try:
            driver.get(self.LIVE_FILINGS_URL)
            logger.debug('Opened SEC EDGAR current-filings page')

            for _ in range(num_pages):
                wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'a[href]')))

                # Light human-like scrolling to reduce bot detection.
                for _ in range(random.randint(1, 3)):
                    driver.execute_script(f'window.scrollBy(0, {random.randint(100, 300)})')
                    time.sleep(random.uniform(0.5, 1.5))

                soup = BeautifulSoup(driver.page_source, 'html.parser')
                all_links |= {
                    href.removeprefix('/Archives/')
                    for a in soup.find_all('a', href=True)
                    if (href := a['href']).endswith('.txt')
                }

                wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@value='Next 100']"))).click()
                logger.debug("Clicked 'Next 100' button")

        except Exception as e:
            logger.debug(f'Collected {len(all_links)} links. Exception: {e}')

        finally:
            driver.quit()

        return all_links

    def _configure_driver(self) -> WebDriver:
        """Create a masked headless Chromium WebDriver for EDGAR scraping."""
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument(
            'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
        )
        chrome_options.binary_location = '/usr/bin/chromium'

        service = Service(executable_path='/usr/bin/chromedriver')
        driver = webdriver.Chrome(service=service, options=chrome_options)

        # Mask Selenium and apply the SEC headers.
        driver.execute_cdp_cmd(
            'Page.addScriptToEvaluateOnNewDocument',
            {'source': "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )
        driver.execute_cdp_cmd('Network.setExtraHTTPHeaders', {'headers': self.headers})
        return driver

"""Fake of the ``Filings`` port: SEC EDGAR serving fixed documents."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from secform4strategy_clients.ports import PermanentError

if TYPE_CHECKING:
    from secform4strategy_clients.ports import Filing, Filings


@dataclass(frozen=True)
class FakeResponse:
    """The slice of an HTTP response that callers read."""

    status_code: int
    text: str

    @property
    def content(self) -> bytes:
        """The body as bytes."""
        return self.text.encode()


@dataclass
class FakeFilings:
    """SEC EDGAR serving fixed documents by URL; an unknown URL raises ``PermanentError``, as a 404 does.

    Attributes:
        documents: URL -> document text.
        errors: URL -> the error its request raises, e.g. a ``RetryableError``.
        requests: Every URL requested.
    """

    documents: Mapping[str, str]
    errors: Mapping[str, Exception] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)

    def get(self, url: str) -> FakeResponse:
        """Serve the document at ``url``, or raise its error."""
        self.requests.append(url)
        if (error := self.errors.get(url)) is not None:
            raise error
        if (text := self.documents.get(url)) is None:
            raise PermanentError(f'GET {url}: HTTP 404')
        return FakeResponse(200, text)

    def fetch_master_index(self, year: int, quarter: int, form_type: str) -> tuple[Filing, ...]:
        """No master index is served."""
        return ()

    def fetch_current_filings(self, form_type: str, num_pages: int = 1) -> tuple[Filing, ...]:
        """No current filings are served."""
        return ()


_conforms: Filings = FakeFilings({})

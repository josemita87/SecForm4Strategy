"""Fake of the ``Filings`` port: SEC EDGAR serving fixed documents."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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
    """SEC EDGAR serving fixed documents by URL; an unknown URL answers 404.

    Attributes:
        documents: URL -> document text.
        requests: Every URL requested.
    """

    documents: Mapping[str, str]
    requests: list[str] = field(default_factory=list)

    def get(self, url: str) -> FakeResponse:
        """Serve the document at ``url``."""
        self.requests.append(url)
        return FakeResponse(200, text) if (text := self.documents.get(url)) is not None else FakeResponse(404, '')

    def fetch_master_index(self, year: int, quarter: int, form_type: str) -> tuple[Filing, ...] | None:
        """No master index is served."""
        return None

    def fetch_current_filings(self, form_type: str, num_pages: int = 1) -> tuple[Filing, ...]:
        """No current filings are served."""
        return ()


_conforms: Filings = FakeFilings({})

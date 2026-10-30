"""The transaction record the scraper publishes: one per Form 4 transaction."""

from typing import Annotated

import xxhash
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, computed_field

#: Codes and symbols as downstream joins expect them: stripped and upper-case.
Code = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True)]

#: A trading symbol, or None where the filer wrote a placeholder for "no symbol".
Ticker = Annotated[Code | None, AfterValidator(lambda t: None if t in {'NONE', 'N/A', 'NA', '-', '--', '---'} else t)]


class Trade(BaseModel):
    """One insider transaction; pydantic coerces the filing's raw text, and ``model_dump()`` is the record.

    The four relationship flags are inputs only: the record carries them as ``owner_code``.
    ``accession`` and ``position`` (the transaction's index in its table) only identify the record.
    """

    model_config = ConfigDict(frozen=True)

    link: str
    timestamp: int = Field(description='SEC acceptance time, epoch ms')
    derivative: bool
    accession: str = Field(exclude=True)
    position: int = Field(exclude=True)
    company_cik: str | None = None
    ticker: Ticker = None
    insider_cik: str | None = None
    insider_name: str | None = None
    director: bool = Field(False, exclude=True)
    officer: bool = Field(False, exclude=True)
    ten_percent_owner: bool = Field(False, exclude=True)
    other: bool = Field(False, exclude=True)
    rule105b1: bool = False
    shares: float = 0
    acquired_disposed: Code | None = None
    price: float = 0.0
    date: str | None = None
    remaining_shares: float = 0
    ownership: str = 'D'
    coding: str | None = None
    equity_swap: bool = False
    direct_holding: float = 0.0
    indirect_holding: float = 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def owner_code(self) -> str:
        """Relationship to the issuer as four bits: director, officer, 10% owner, other."""
        return ''.join(str(int(flag)) for flag in (self.director, self.officer, self.ten_percent_owner, self.other))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def key(self) -> str:
        """Deterministic record key: the filing's accession, its table and the transaction's position in it.

        A filed accession never changes, so every redelivery of a transaction upserts the same row.
        """
        return xxhash.xxh64(f'{self.accession}:{int(self.derivative)}:{self.position}').hexdigest()

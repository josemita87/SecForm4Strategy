"""SEC Form 4 ownership document, declared after its XML schema.

Pure data: each model mirrors one element and keeps its raw text, stripped. Absent elements
default to empty models, so a missing branch reads as ``None`` values rather than
failing the document; elements not declared here are ignored.
"""

from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import BaseModel, BeforeValidator, ConfigDict
from pydantic_xml import BaseXmlModel, attr, element, wrapped

#: EDGAR's ``ACCEPTANCE-DATETIME`` stamp (``YYYYMMDDHHMMSS``, US/Eastern) as an aware datetime.
EdgarTimestamp = Annotated[
    datetime,
    BeforeValidator(
        lambda stamp: (
            datetime.strptime(stamp, '%Y%m%d%H%M%S').replace(tzinfo=ZoneInfo('America/New_York'))
            if isinstance(stamp, str)
            else stamp
        )
    ),
]


class Node(BaseXmlModel, search_mode='unordered'):
    """Base of every Form 4 element: children are matched by tag in any order."""

    model_config = ConfigDict(str_strip_whitespace=True)


class FootnoteRef(Node, tag='footnoteId'):
    """A reference from a value to a footnote."""

    id: str | None = attr(default=None)


class Value(Node):
    """A ``<value>`` wrapper and the footnotes attached to it."""

    value: str | None = element(default=None)
    footnotes: list[FootnoteRef] = element(default_factory=list)


class Issuer(Node):
    """The company whose securities were traded."""

    cik: str | None = element(tag='issuerCik', default=None)
    ticker: str | None = element(tag='issuerTradingSymbol', default=None)


class OwnerId(Node):
    """Who the reporting owner is."""

    cik: str | None = element(tag='rptOwnerCik', default=None)
    name: str | None = element(tag='rptOwnerName', default=None)


class Relationship(Node):
    """The reporting owner's relationship to the issuer (``'1'``/``'0'`` or ``'true'``/``'false'``)."""

    director: str | None = element(tag='isDirector', default=None)
    officer: str | None = element(tag='isOfficer', default=None)
    ten_percent_owner: str | None = element(tag='isTenPercentOwner', default=None)
    other: str | None = element(tag='isOther', default=None)


class ReportingOwner(Node):
    """An insider reporting the transactions."""

    id: OwnerId = element(tag='reportingOwnerId', default_factory=OwnerId)
    relationship: Relationship = element(tag='reportingOwnerRelationship', default_factory=Relationship)


class Coding(Node):
    """Transaction code, whether an equity swap was involved, and the footnotes attached to the coding."""

    code: str | None = element(tag='transactionCode', default=None)
    equity_swap: str | None = element(tag='equitySwapInvolved', default=None)
    footnotes: list[FootnoteRef] = element(default_factory=list)


class Amounts(Node):
    """Shares traded, price per share and direction."""

    shares: Value = element(tag='transactionShares', default_factory=Value)
    price: Value = element(tag='transactionPricePerShare', default_factory=Value)
    acquired_disposed: Value = element(tag='transactionAcquiredDisposedCode', default_factory=Value)


class Position(Node):
    """Shares owned after the transaction and whether directly or indirectly."""

    shares_owned: Value = wrapped(
        'postTransactionAmounts', element(tag='sharesOwnedFollowingTransaction', default_factory=Value)
    )
    ownership: Value = wrapped('ownershipNature', element(tag='directOrIndirectOwnership', default_factory=Value))


class Transaction(Position):
    """One reported transaction; both tables share this layout."""

    security_title: Value = element(tag='securityTitle', default_factory=Value)
    date: Value = element(tag='transactionDate', default_factory=Value)
    coding: Coding = element(tag='transactionCoding', default_factory=Coding)
    amounts: Amounts = element(tag='transactionAmounts', default_factory=Amounts)


class NonDerivativeTable(Node):
    """Common-stock transactions and holdings."""

    transactions: list[Transaction] = element(tag='nonDerivativeTransaction', default_factory=list)
    holdings: list[Position] = element(tag='nonDerivativeHolding', default_factory=list)


class DerivativeTable(Node):
    """Option, warrant and other derivative transactions and holdings."""

    transactions: list[Transaction] = element(tag='derivativeTransaction', default_factory=list)
    holdings: list[Position] = element(tag='derivativeHolding', default_factory=list)


class Footnote(Node, tag='footnote'):
    """A footnote's text, referenced by id."""

    id: str | None = attr(default=None)
    text: str | None = None


class OwnershipDocument(Node, tag='ownershipDocument'):
    """A Form 4 filing's XML document."""

    issuer: Issuer = element(tag='issuer', default_factory=Issuer)
    owners: list[ReportingOwner] = element(tag='reportingOwner', default_factory=list)
    aff10b5_one: str | None = element(tag='aff10b5One', default=None)
    non_derivative: NonDerivativeTable = element(tag='nonDerivativeTable', default_factory=NonDerivativeTable)
    derivative: DerivativeTable = element(tag='derivativeTable', default_factory=DerivativeTable)
    footnotes: list[Footnote] = wrapped('footnotes', element(tag='footnote', default_factory=list))


class Submission(BaseModel):
    """A filing's full EDGAR submission: when SEC accepted it, from the header, and its ownership document."""

    model_config = ConfigDict(frozen=True)

    accepted_at: EdgarTimestamp
    document: Annotated[
        OwnershipDocument,
        BeforeValidator(lambda xml: OwnershipDocument.from_xml(xml) if isinstance(xml, str | bytes) else xml),
    ]

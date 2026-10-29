"""Validated model of one SEC Form 4 transaction, built from the raw XML text of its filing."""

import contextlib
import xml.etree.ElementTree as ET

from const import derivative_hold, hold, holding_amounts, holding_ownership
from pydantic import BaseModel, ConfigDict

# Output transaction field -> its derivative-table counterpart on the model.
TX_FIELDS = {
    'shares': 'd_shares',
    'acquired_disposed': 'd_acquired_disposed',
    'price': 'd_acquired_price',
    'date': 'd_execution_date',
    'remaining_shares': 'd_remaining_shares',
    'ownership': 'd_ownership',
    'coding': 'd_coding',
}


class EssentialTrade(BaseModel):
    """One Form 4 transaction; Pydantic coerces the XML text ('1', 'false', '100.5') into the field types."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    # Filing-level (insider) fields
    link: str
    xml: ET.Element
    company_cik: str | None = None
    ticker: str | None = None
    insider_cik: str | None = None
    insider_name: str | None = None
    director: bool = False
    officer: bool = False
    ten_percent_owner: bool = False
    other: bool = False
    rule105b1: bool = False

    # Non-derivative transaction fields
    shares: float = 0
    acquired_disposed: str | None = None
    price: float = 0.0
    date: str | None = None
    remaining_shares: float = 0
    ownership: str = 'D'
    coding: str | None = None
    equity_swap: bool = False
    footnote_id: str | None = None

    # Derivative transaction fields
    d_shares: float = 0
    d_acquired_disposed: str | None = None
    d_acquired_price: float = 0.0
    d_execution_date: str | None = None
    d_remaining_shares: float = 0
    d_ownership: str = 'D'
    d_coding: str | None = None
    d_footnote_id: str | None = None

    @property
    def is_derivative(self) -> bool:
        """Whether the trade came from the derivative table (it carries no non-derivative shares)."""
        return self.shares == 0

    def _is_10b5_1(self) -> bool:
        """Whether the trade is flagged Rule 10b5-1, directly or in its own footnote."""
        footnote_id = self.d_footnote_id if self.is_derivative else self.footnote_id
        return self.rule105b1 or any(
            footnote.get('id') == footnote_id and '10b5-1' in (footnote.text or '')
            for footnote in self.xml.findall('footnotes/footnote')
        )

    def _holdings(self) -> tuple[float, float]:
        """Sum the direct and indirect holdings listed in the filing; both stay 0 unless both are present."""
        amounts = {'D': 0.0, 'I': 0.0}
        for holding in self.xml.findall(derivative_hold if self.is_derivative else hold):
            ownership, amount = holding.findtext(holding_ownership), holding.findtext(holding_amounts)
            if ownership is not None and ownership in amounts and amount:
                with contextlib.suppress(ValueError):
                    amounts[ownership] += float(amount)
        return (amounts['D'], amounts['I']) if amounts['D'] and amounts['I'] else (0.0, 0.0)

    def to_dict(self) -> dict[str, object]:
        """Flatten the trade into one record, choosing the derivative or non-derivative fields.

        Returns:
            A dictionary merging insider data with the relevant transaction data.
        """
        direct_holding, indirect_holding = self._holdings()
        return {
            'company_cik': self.company_cik,
            'ticker': self.ticker,
            'insider_cik': self.insider_cik,
            'insider_name': self.insider_name,
            'owner_code': ''.join(
                str(int(flag)) for flag in (self.director, self.officer, self.ten_percent_owner, self.other)
            ),
            'rule105b1': self._is_10b5_1(),
            'derivative': self.is_derivative,
            'link': self.link,
            **{field: getattr(self, d_field if self.is_derivative else field) for field, d_field in TX_FIELDS.items()},
            'direct_holding': direct_holding,
            'indirect_holding': indirect_holding,
            'equity_swap': self.equity_swap,
        }

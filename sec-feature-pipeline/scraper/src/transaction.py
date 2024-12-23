from pydantic import BaseModel
from typing import Optional
import sys
import os
import xml.etree.ElementTree as ET
from pprint import pprint


from const import hold, derivative_hold, holding_ownership, holding_amounts


class EssentialTrade(BaseModel):

    class Config:
        arbitrary_types_allowed = True

    # Insider paths
    link: str
    xml: ET.Element
    company_cik: Optional[int] = None
    ticker: Optional[str] = None
    insider_cik: Optional[int] = None
    insider_name: Optional[str] = None
    director: Optional[bool] = False
    officer: Optional[bool] = False
    ten_percent_owner: Optional[bool] = False
    other: Optional[bool] = False
    rule105b1: Optional[bool] = False

    # Transaction paths
    shares: Optional[int] = 0
    acquired_disposed: Optional[str] = None
    price: float = 0.0
    date: Optional[str] = None
    remaining_shares: Optional[int] = 0
    ownership: Optional[str] = "D"
    coding: Optional[str] = None
    equity_swap: bool = False
    footnote_id: Optional[str] = None

    # Derivative paths
    d_shares: Optional[int] = 0
    d_acquired_disposed: Optional[str] = None
    d_acquired_price: Optional[float] = 0.0
    d_execution_date: Optional[str] = None
    d_remaining_shares: Optional[int] = 0
    d_ownership: Optional[str] = "D"
    d_coding: Optional[str] = None
    d_footnote_id: Optional[str] = None

    # Derived Fields
    is_derivative: bool = False
    is_equity_swap: bool = False
    direct_holding: Optional[int] = 0
    indirect_holding: Optional[int] = 0
    d_direct_holding: Optional[int] = 0
    d_indirect_holding: Optional[int] = 0
    owner_code: str = None

    def _set_is_derivative(self) -> None:
        """Determine if this is a derivative trade."""
        self.is_derivative = self.shares == 0

    def _set_equity_swap(self) -> None:
        """Set various flags based on attributes."""
        self.is_equity_swap = str(self.equity_swap).lower() in ['true', '1']

    def _set_rule105b1(self) -> str:
        """Determine if this is a Rule 10b5-1 trade.
        This method first checks if the rule105b1 attribute is set to True.
        If it is not, it checks the footnotes for the presence of '10b5-1' 
        for the footnote corresponding to the transaction."""
        
        if str(self.rule105b1).lower() in ['true', '1']:
            self.rule105b1 = True

        else:
            id = self.d_footnote_id if self.is_derivative else self.footnote_id
            for footnote in self.xml.findall('footnotes/footnote'):
                if footnote.get('id') == id and '10b5-1' in footnote.text:
                    self.rule105b1 = True
                    break

    def _handle_multiple_ownerships(self):
        """Compute direct and indirect holdings."""

        # Determine if using derivative path or regular path
        path = derivative_hold if self.is_derivative else hold
        holdings = self.xml.findall(path)

        # Initialize holding amounts
        direct_amount = 0
        indirect_amount = 0

        # Iterate through elements and accumulate holdings
        for h in holdings:
            amount = float(h.find(holding_amounts).text)
            ownership = str(h.find(holding_ownership).text)

            if ownership == 'D':
                direct_amount += amount
            elif ownership == 'I':
                indirect_amount += amount

        if not self.is_derivative:
            self.direct_holding, self.indirect_holding = direct_amount, indirect_amount

        else:
            self.d_direct_holding, self.d_indirect_holding = direct_amount, indirect_amount

    def _handle_owner_code(self):
        self.owner_code = "".join(
            '1' if str(pos).lower() in ['1', 'true'] else '0' for pos in [
                self.director,
                self.officer,
                self.ten_percent_owner,
                self.other
            ]
        )

    def to_dict(self) -> dict:
        """Convert object to dictionary."""

        self._set_is_derivative()
        self._set_equity_swap()
        self._set_rule105b1()
        self._handle_owner_code()
        self._handle_multiple_ownerships()

        insider_data = {
            'company_cik': self.company_cik,
            'ticker': self.ticker,
            'insider_cik': self.insider_cik,
            'insider_name': self.insider_name,
            'owner_code': self.owner_code,
            'rule105b1': self.rule105b1,
            'derivative': self.is_derivative,
            'link': self.link,
        }

        if self.is_derivative:
            tx_data = {
                'shares': self.d_shares,
                'acquired_disposed': self.d_acquired_disposed,
                'price': self.d_acquired_price,
                'date': self.d_execution_date,
                'remaining_shares': self.d_remaining_shares,
                'ownership': self.d_ownership,
                'coding': self.d_coding,
                'direct_holding': self.d_direct_holding,
                'indirect_holding': self.d_indirect_holding,
                'equity_swap': self.is_equity_swap
            }

        else:
            tx_data = {
                'shares': self.shares,
                'acquired_disposed': self.acquired_disposed,
                'price': self.price,
                'date': self.date,
                'remaining_shares': self.remaining_shares,
                'ownership': self.ownership,
                'coding': self.coding,
                'direct_holding': self.direct_holding,
                'indirect_holding': self.indirect_holding,
                'equity_swap': self.is_equity_swap
            }

        return {**insider_data, **tx_data}

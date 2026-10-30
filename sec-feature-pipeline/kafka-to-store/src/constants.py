"""Domain constants for the kafka-to-store microservice."""

from enum import Enum, StrEnum


class FeatureGroup(str, Enum):
    """References for the feature groups this service writes.

    Members double as their feature-store reference strings, so they can be
    passed straight to the feature-store client and used as catalog keys.
    """

    BT4 = 'bt4'
    BI4 = 'bi4'


class Column(StrEnum):
    """Columns this service reads or writes."""

    COMPANY_CIK = 'company_cik'
    TICKER = 'ticker'
    INSIDER_CIK = 'insider_cik'
    INSIDER_NAME = 'insider_name'
    OWNER_CODE = 'owner_code'
    ACQUIRED_DISPOSED = 'acquired_disposed'
    CODING = 'coding'
    OWNERSHIP = 'ownership'
    RULE_10B5_1 = 'rule105b1'
    DERIVATIVE = 'derivative'
    EQUITY_SWAP = 'equity_swap'
    SHARES = 'shares'
    PRICE = 'price'
    REMAINING_SHARES = 'remaining_shares'
    DIRECT_HOLDING = 'direct_holding'
    INDIRECT_HOLDING = 'indirect_holding'
    TIMESTAMP = 'timestamp'
    DATE = 'date'
    LINK = 'link'

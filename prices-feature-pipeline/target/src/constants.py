"""Domain constants and reference-data types for the target microservice."""

import json
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path

import pandas as pd


class FeatureGroup(str, Enum):
    """References for the feature groups this service reads and writes.

    Members double as their feature-store reference strings, so they can be
    passed straight to the feature-store client and used as catalog keys.
    """

    PRICES = 'p'
    BT4 = 'bt4'
    BI4 = 'bi4'
    RT4 = 'rt4'
    BIR4 = 'bir4'


class Column(StrEnum):
    """Columns this service reads, writes, or joins on."""

    TICKER = 'ticker'
    DATE = 'date'
    TIMESTAMP = 'timestamp'
    COMPANY_CIK = 'company_cik'
    ACQUIRED_DISPOSED = 'acquired_disposed'
    PRICE = 'price'
    SHARES = 'shares'
    REMAINING_SHARES = 'remaining_shares'
    DIRECT_HOLDING = 'direct_holding'
    INDIRECT_HOLDING = 'indirect_holding'
    TX_VALUE = 'tx_value'
    REMAINING_VALUE = 'remaining_value'
    OPEN = 'open'
    CLOSE = 'close'
    MARKET_CAP = 'market_cap'
    EXCHANGE = 'exchange'
    SIC = 'sic'
    SIC_GROUP = 'sic_group'
    PCT_CHANGE = 'pct_change'
    LABEL_KNOWN_AT = 'label_known_at'
    AVG_TARGET_EXPANDING = 'avg_target_expanding'

    # Join keys and intermediates, never stored
    SNAPSHOT = 'snapshot'
    LAST_CLOSE = 'last_close'
    CLOSED_AT = 'closed_at'
    ENTRY_DAY = 'entry_day'
    ENTRY_PRICE = 'entry_price'
    PRINTS_AT = 'prints_at'
    EXIT_DUE = 'exit_due'
    EXIT_DATE = 'exit_date'
    EXIT_PRICE = 'exit_price'


class Direction(str, Enum):
    """SEC Form 4 transaction direction (``acquired_disposed`` column)."""

    ACQUIRED = 'A'
    DISPOSED = 'D'


#: US equity market timezone and regular-session open/close, which bound when a filing can first be traded.
MARKET_TZ = 'America/New_York'
MARKET_OPEN = pd.Timedelta(hours=9, minutes=30)
MARKET_CLOSE = pd.Timedelta(hours=16)


@dataclass(frozen=True, slots=True)
class ReferenceData:
    """Static company data joined onto transactions.

    Attributes:
        market_caps: Monthly snapshots with ``ticker``, ``snapshot`` (first business day) and ``market_cap``.
        companies: ``exchange``, ``sic`` and ``sic_group`` indexed by zero-padded issuer CIK.
    """

    market_caps: pd.DataFrame
    companies: pd.DataFrame

    @classmethod
    def load(cls, market_caps_path: Path, companies_path: Path) -> ReferenceData:
        """Read the market-cap parquet and the company JSON (``{field: {cik: value}}``).

        Args:
            market_caps_path: Parquet with ``ticker``, ``marketcap`` and ``fbd`` columns.
            companies_path: JSON mapping each field to a ``{cik: value}`` table.

        Returns:
            The loaded reference data, sorted for as-of joins.
        """
        caps = pd.read_parquet(market_caps_path)
        return cls(
            market_caps=pd.DataFrame(
                {
                    Column.TICKER: caps['ticker'].astype(str),
                    Column.SNAPSHOT: pd.to_datetime(caps['fbd']),
                    Column.MARKET_CAP: caps['marketcap'],
                }
            ).sort_values(Column.SNAPSHOT),
            companies=pd.DataFrame(json.loads(companies_path.read_text(encoding='utf-8'))),
        )

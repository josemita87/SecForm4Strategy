"""Domain constants and reference-data types for the target microservice."""

import json
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from pathlib import Path


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
                    'ticker': caps['ticker'].astype(str),
                    'snapshot': pd.to_datetime(caps['fbd']),
                    'market_cap': caps['marketcap'],
                }
            ).sort_values('snapshot'),
            companies=pd.DataFrame(json.loads(companies_path.read_text(encoding='utf-8'))),
        )

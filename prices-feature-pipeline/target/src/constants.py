"""Domain constants for the target microservice."""

from enum import Enum


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

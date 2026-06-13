"""Domain constants for the yahoo-connect microservice."""

from enum import Enum


class FeatureGroup(str, Enum):
    """References for the feature groups this service touches.

    Members double as their feature-store reference strings, so they can be
    passed straight to the feature-store client and used as catalog keys.
    """

    PRICES = 'p'
    BT4 = 'bt4'
    BI4 = 'bi4'

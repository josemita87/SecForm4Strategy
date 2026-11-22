"""Domain constants for the training microservice."""

from enum import Enum, StrEnum


class FeatureGroup(str, Enum):
    """References for the feature groups this service reads.

    Members double as their feature-store reference strings, so they can be
    passed straight to the feature-store client and used as catalog keys.
    """

    RT4 = 'rt4'


class Col(StrEnum):
    """Columns this service reads or writes."""

    TICKER = 'ticker'
    ACCEPTED_AT = 'accepted_at'
    LABEL_KNOWN_AT = 'label_known_at'
    TICKER_HISTORY = 'ticker_history'


#: Share of the captured returns cut from each end for the trimmed mean.
TRIMMED_SHARE = 0.05

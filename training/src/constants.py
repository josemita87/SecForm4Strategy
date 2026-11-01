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

    ACCEPTED_AT = 'accepted_at'
    LABEL_KNOWN_AT = 'label_known_at'
    AVG_TARGET_EXPANDING = 'avg_target_expanding'

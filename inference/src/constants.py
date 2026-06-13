"""Domain constants for the inference microservice."""

from enum import Enum


class FeatureGroup(str, Enum):
    """References for the feature groups this service reads.

    Members double as their feature-store reference strings, so they can be
    passed straight to the feature-store client and used as catalog keys.
    """

    BIR4 = 'bir4'

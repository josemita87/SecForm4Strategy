"""Domain constants for the inference microservice."""

from enum import Enum, StrEnum


class FeatureGroup(str, Enum):
    """References for the feature groups this service reads and writes.

    Members double as their feature-store reference strings, so they can be
    passed straight to the feature-store client and used as catalog keys.
    """

    BIR4 = 'bir4'
    RT4 = 'rt4'
    PREDICTIONS = 'predictions'


class Col(StrEnum):
    """Columns this service reads or writes."""

    KEY = 'key'
    TICKER = 'ticker'
    ACCEPTED_AT = 'accepted_at'
    PROBABILITY = 'probability'
    PCT_CHANGE = 'pct_change'
    ROW_KEY = 'row_key'
    PROBABILITY_CUTOFF = 'probability_cutoff'
    MODEL_VERSION = 'model_version'
    SOURCE = 'source'
    SCORED_AT = 'scored_at'
    RUN_ID = 'run_id'
    SHORTED = 'shorted'
    CLIENT_ORDER_ID = 'client_order_id'
    ORDER_OUTCOME = 'order_outcome'


class Source(StrEnum):
    """Which path scored a prediction."""

    LIVE = 'live'
    BATCH = 'batch'


class OrderOutcome(StrEnum):
    """What placing a short order did."""

    SUBMITTED = 'submitted'
    PLACED_BEFORE = 'placed_before'
    #: The broker does not trade or short the symbol
    UNTRADABLE = 'untradable'
    REJECTED = 'rejected'
    #: Failed transiently and left for a rerun
    FAILED = 'failed'

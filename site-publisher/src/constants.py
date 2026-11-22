"""Domain constants for the site publisher, and the data file name of each section."""

from enum import Enum, StrEnum


class FeatureGroup(str, Enum):
    """References for the feature groups this service reads; members double as catalog keys."""

    RT4 = 'rt4'
    BT4 = 'bt4'
    BI4 = 'bi4'


class Col(StrEnum):
    """Columns this service reads or writes."""

    KEY = 'key'
    TICKER = 'ticker'
    ACCEPTED_AT = 'accepted_at'
    LABEL_KNOWN_AT = 'label_known_at'
    INSIDER_CIK = 'insider_cik'
    INSIDER_NAME = 'insider_name'
    OWNER_CODE = 'owner_code'
    ACQUIRED_DISPOSED = 'acquired_disposed'
    DERIVATIVE = 'derivative'
    SHARES = 'shares'
    PRICE = 'price'
    VALUE = 'value'
    PREDICTED = 'predicted'
    ACTUAL = 'actual'
    DATE = 'date'
    CLOSE = 'close'
    DAY = 'day'
    STRATEGY = 'strategy'
    SPY = 'spy'
    INSIDERS = 'insiders'
    FIRST_ACCEPTED_AT = 'first_accepted_at'
    LAST_ACCEPTED_AT = 'last_accepted_at'


class Section(StrEnum):
    """The site's data files, each written to ``data/<section>.json``."""

    LIVE = 'live'
    HEALTH = 'health'
    PORTFOLIO = 'portfolio'
    MODEL = 'model'
    EXPLORE = 'explore'


def file_name(section: Section) -> str:
    """Return a section's data file name.

    Args:
        section: The section.

    Returns:
        Its file name, such as ``live.json``.
    """
    return f'{section.value}.json'


class Mode(StrEnum):
    """What an invocation publishes: ``live`` after each live-score run, ``all`` on the minute schedule."""

    ALL = 'all'
    LIVE = 'live'


#: Acquired-disposed code of a sale.
DISPOSED = 'D'
#: ``owner_code`` bits, in order: director, officer, 10% owner, other.
ROLES = ('Director', 'Officer', '10% Owner', 'Other')

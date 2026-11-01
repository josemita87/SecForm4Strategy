"""Domain constants for the yahoo-connect microservice."""

from enum import Enum, StrEnum

import pandas as pd


class FeatureGroup(str, Enum):
    """References for the feature groups this service touches.

    Members double as their feature-store reference strings, so they can be
    passed straight to the feature-store client and used as catalog keys.
    """

    PRICES = 'p'
    BT4 = 'bt4'
    BI4 = 'bi4'


class Col(StrEnum):
    """Columns this service reads or writes."""

    TICKER = 'ticker'
    DATE = 'date'
    CLOSE = 'close'
    CLOSE_AS_TRADED = 'close_as_traded'
    SPLIT = 'split'
    DIVIDEND = 'dividend'


#: US equity market timezone and regular-session close; a bar is final once its close has printed.
MARKET_TZ = 'America/New_York'
MARKET_CLOSE = pd.Timedelta(hours=16)
#: Stored days fetched again per ticker; their adjustment factors reveal splits and dividends applied since.
REBASE_LOOKBACK_DAYS = 30

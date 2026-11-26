"""Lambda entry point for the live path: fetches the recent closes of the tickers a new filing names.

The live-score state machine invokes it with ``{"tickers": [...]}`` and passes its output to target.
It reads and writes no feature group: the batch task alone refreshes the prices group. The image is
the same as the batch task's; Lambda runs ``python -m awslambdaric src.handler.handle`` from the service directory.
"""

import logging
from typing import TYPE_CHECKING, Any

from secform4strategy_clients.market_data import MarketDataClient

from src.prices import recent_closes

if TYPE_CHECKING:
    from collections.abc import Mapping

# The Lambda runtime installs its own log handler first, so main's basicConfig leaves the level at WARNING
logging.getLogger().setLevel(logging.INFO)

market = MarketDataClient()


def handle(event: Mapping[str, Any], _context: object) -> dict[str, object]:
    """Return the event's tickers with their final as-traded closes of the last days (``recent_closes``).

    Args:
        event: ``{"tickers": [...]}``.
        _context: The Lambda context, unused; Lambda passes it to every handler.

    Returns:
        ``{"tickers": [...], "prices": [{"ticker", "date", "close_as_traded"}, ...]}``, for target.
    """
    return {'tickers': (tickers := list(event['tickers'])), 'prices': recent_closes(market, tickers)}

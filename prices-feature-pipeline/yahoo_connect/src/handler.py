"""Lambda entry point for the live path: refreshes the daily bars of the tickers a new filing names.

The live-score state machine invokes it with ``{"tickers": [...]}``. The image is the same as the
batch task's; Lambda runs ``python -m awslambdaric src.handler.handle`` from the service directory.
The feature store is opened once per execution environment, at import, and reused.
"""

import logging
from typing import TYPE_CHECKING, Any

from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.market_data import MarketDataClient

from src.config import config
from src.main import refresh_prices
from src.paths import SPEC_PATH

if TYPE_CHECKING:
    from collections.abc import Mapping

# The Lambda runtime installs its own log handler first, so main's basicConfig leaves the level at WARNING
logging.getLogger().setLevel(logging.INFO)

store = IcebergFeatureStore(
    load_catalog('default'),
    config.feature_store_namespace,
    load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
)
market = MarketDataClient()


def handle(event: Mapping[str, Any], _context: object) -> dict[str, list[str]]:
    """Refresh the event's tickers' prices; a ``RetryableError`` fails the invocation, and the state machine retries it.

    Args:
        event: ``{"tickers": [...]}``.
        _context: The Lambda context, unused; Lambda passes it to every handler.

    Returns:
        The tickers, for the next step.
    """
    refresh_prices(store, market, tickers := list(event['tickers']), config.buffer_size)
    return {'tickers': tickers}

"""Lambda entry point for the live path: builds the BIR4 rows of the tickers a new filing names.

The live-score state machine invokes it with ``{"tickers": [...]}``, after their prices are
refreshed. The image is the same as the batch task's; Lambda runs ``python -m awslambdaric
src.handler.handle`` from the service directory. The feature store and the reference data are
loaded once per execution environment, at import, and reused.
"""

import logging
from typing import TYPE_CHECKING, Any

import pandas as pd
from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore

from src.config import config
from src.constants import Col, ReferenceData
from src.main import publish_inference_rows
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
reference = ReferenceData.load(config.market_caps_path, config.companies_path)
window = pd.Timedelta(days=config.cluster_window_days)


def handle(event: Mapping[str, Any], _context: object) -> dict[str, list[str]]:
    """Build and upsert the BIR4 rows of the event's tickers, exactly as the batch inference run does.

    Args:
        event: ``{"tickers": [...]}``.
        _context: The Lambda context, unused; Lambda passes it to every handler.

    Returns:
        The tickers that got rows, for the next step; none when no transaction of the tickers passes the filter.
    """
    where = {config.filter_key: config.acquired_disposed, Col.TICKER: list(event['tickers'])}
    # An empty frame may lack the ticker column, so it is checked before the column is read
    rows = publish_inference_rows(store, where, reference, window)
    return {'tickers': [] if rows.empty else sorted(rows[Col.TICKER].unique())}

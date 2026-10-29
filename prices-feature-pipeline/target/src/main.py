"""Entry point: wires the feature store from config and runs the target training or inference flow."""

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from secform4strategy_clients.feature_store import HopsworksClient, load_feature_group_catalog

from src import clean, computations
from src.config import config
from src.constants import FeatureGroup

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from secform4strategy_clients.ports import FeatureStore, Filters

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)


def run_training(
    feature_store: FeatureStore, where: Filters, agg_dict: Mapping[str, str | Callable[..., object]], delta_period: int
) -> None:
    """Build RT4 (transactions joined with their forward-return target) and store it.

    Args:
        feature_store: Source of BT4 and prices; destination of RT4.
        where: Filter selecting the transactions to label.
        agg_dict: Per-column aggregation collapsing same-ticker, same-day transactions.
        delta_period: Days ahead the forward return is measured.
    """
    BT4 = feature_store.read(FeatureGroup.BT4, where=where)
    tickers = clean.get_unique_tickers(BT4)
    logger.info(f'{len(tickers)} tickers to label')

    # Without prices no target can be computed, so a failed read fails the run
    BT4, P = clean.normalize_data(BT4, feature_store.read(FeatureGroup.PRICES, where={'ticker': tickers}))

    # Aggregate daily, attach the forward return, then the leakage-free expanding average
    semiRT4 = computations.compute_target(computations.data_aggregation(BT4, agg_dict), P, delta_period)
    RT4 = clean.data_cleaning(
        computations.compute_RT4(df=semiRT4, timedelta=delta_period), ['pct_change', 'avg_target_expanding']
    )
    feature_store.push(FeatureGroup.RT4, RT4)


def run_inference(
    feature_store: FeatureStore, where: Filters, agg_dict: Mapping[str, str | Callable[..., object]]
) -> None:
    """Map the trained per-ticker targets onto new transactions (BIR4) and store them.

    Args:
        feature_store: Source of BI4 and RT4; destination of BIR4.
        where: Filter selecting the transactions to score.
        agg_dict: Per-column aggregation collapsing same-ticker, same-day transactions.
    """
    BI4, _ = clean.normalize_data(feature_store.read(FeatureGroup.BI4, where=where))
    BIR4 = computations.map_RT4_to_BIR4(
        feature_store.read(FeatureGroup.RT4), computations.data_aggregation(BI4, agg_dict)
    )
    # BIR4 has no realized return yet, only the mapped expanding average
    feature_store.push(FeatureGroup.BIR4, clean.data_cleaning(BIR4, ['avg_target_expanding']))


if __name__ == '__main__':
    # Connect to the feature store and route by system configuration.
    time.sleep(config.delay)
    feature_store = HopsworksClient(
        config.project_name,
        config.hopsworks_api_key,
        load_feature_group_catalog(Path(__file__).parent / 'feature_groups.yaml', config.feature_group_version),
    )
    where = {config.filter_key: config.acquired_disposed}
    if config.system_training:
        run_training(feature_store, where, config.agg_dict, config.delta_period)
    elif config.system_inference:
        run_inference(feature_store, where, config.agg_dict)

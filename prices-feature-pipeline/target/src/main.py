"""Entry point: wires the feature store from config and runs the target training or inference flow."""

import logging
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from pyiceberg.catalog import load_catalog
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore

from src import computations
from src.config import config
from src.constants import Column, FeatureGroup, ReferenceData

if TYPE_CHECKING:
    from secform4strategy_clients.ports import FeatureStore, Filters

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)


def run_training(
    feature_store: FeatureStore,
    where: Filters,
    agg_dict: Mapping[str, str | Callable[..., object]],
    delta_period: int,
    reference: ReferenceData,
) -> None:
    """Build RT4 (transactions joined with their forward-return target) and store it.

    Args:
        feature_store: Source of BT4 and prices; destination of RT4.
        where: Filter selecting the transactions to label.
        agg_dict: Per-column aggregation collapsing same-ticker, same-day transactions.
        delta_period: Days between entry and exit of the forward return.
        reference: Market caps and company table joined onto each daily row.
    """
    BT4 = feature_store.read(FeatureGroup.BT4, where=where)
    logger.info(f'{len(tickers := BT4[Column.TICKER].unique())} tickers to label')

    # Without prices no target can be computed, so a failed read fails the run
    P = feature_store.read(FeatureGroup.PRICES, where={Column.TICKER: tickers})

    # Aggregate daily, add company context and value features, attach the forward return, then the expanding average
    daily = computations.add_company_context(computations.data_aggregation(BT4, agg_dict), reference)
    semiRT4 = computations.compute_target(computations.add_value_features(daily, P), P, delta_period)
    # Only labelled rows feed the expanding average; `label_known_at` stays for training's purged split
    labelled = semiRT4.dropna(subset=[Column.LABEL_KNOWN_AT])
    RT4 = computations.compute_RT4(labelled).dropna(subset=[Column.PCT_CHANGE, Column.AVG_TARGET_EXPANDING])
    feature_store.push(FeatureGroup.RT4, RT4)


def run_inference(
    feature_store: FeatureStore,
    where: Filters,
    agg_dict: Mapping[str, str | Callable[..., object]],
    reference: ReferenceData,
) -> None:
    """Map the trained per-ticker targets onto new transactions (BIR4) and store them.

    Args:
        feature_store: Source of BI4, prices and RT4; destination of BIR4.
        where: Filter selecting the transactions to score.
        agg_dict: Per-column aggregation collapsing same-ticker, same-day transactions.
        reference: Market caps and company table joined onto each daily row.
    """
    BI4 = feature_store.read(FeatureGroup.BI4, where=where)
    # The same features as training: company context and USD values from prices known at acceptance
    P = feature_store.read(FeatureGroup.PRICES, where={Column.TICKER: BI4[Column.TICKER].unique()})
    daily = computations.add_company_context(computations.data_aggregation(BI4, agg_dict), reference)
    BIR4 = computations.map_RT4_to_BIR4(feature_store.read(FeatureGroup.RT4), computations.add_value_features(daily, P))
    # BIR4 has no realized return yet, only the mapped expanding average
    feature_store.push(FeatureGroup.BIR4, BIR4.dropna(subset=[Column.AVG_TARGET_EXPANDING]))


if __name__ == '__main__':
    # Connect to the feature store and route by system configuration.
    time.sleep(config.delay)
    feature_store = IcebergFeatureStore(
        load_catalog('default'),
        config.feature_store_namespace,
        load_feature_group_catalog(Path(__file__).parent / 'spec.yaml', config.feature_group_version),
    )
    where = {config.filter_key: config.acquired_disposed}
    reference = ReferenceData.load(config.market_caps_path, config.companies_path)
    if config.system_training:
        run_training(feature_store, where, config.agg_dict, config.delta_period, reference)
    elif config.system_inference:
        run_inference(feature_store, where, config.agg_dict, reference)

"""Entry point for the target microservice training and inference flows."""

import logging
import time
from pathlib import Path

import pandas as pd
from inside_out_clients.feature_store import HopsworksClient, load_feature_group_catalog

from src import clean, computations
from src.config import config
from src.constants import FeatureGroup

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)

# Catalog of feature groups this service touches, loaded from the YAML spec template.
FEATURE_GROUPS = load_feature_group_catalog(Path(__file__).parent / 'feature_groups.yaml', config.feature_group_version)


def run_training(feature_store: HopsworksClient) -> None:
    """Build RT4 (transactions joined with their forward-return target) and store it."""
    # Fetch transactions data
    BT4 = feature_store.read(
        FeatureGroup.BT4,
        where=lambda fg: fg.filter(fg[config.filter_key] == config.acquired_disposed),
        read_options={'use_hive': True},
    )
    # Get unique tickers
    tickers_to_process = clean.get_unique_tickers(BT4)

    logger.warning(tickers_to_process)
    # Fetch P (price data)
    try:
        P = feature_store.read(
            FeatureGroup.PRICES,
            where=lambda fg: fg.filter(fg['ticker'].isin(tickers_to_process)),
            read_options={'use_hive': True},
        )
    except Exception:
        P = pd.DataFrame()
    logger.warning(P)

    # Assert and format data
    BT4, P = clean.normalize_data(BT4, P)

    # Aggregate the data on a daily basis, as defined by config.adg_dict
    BT4_AGG = computations.data_aggregation(BT4, config.agg_dict)

    # Compute percentage change in price after config.delta_period days
    semiRT4 = computations.compute_target(BT4_AGG, P, config.delta_period)

    # Compute RT4 (form4 + computations)
    RT4 = computations.compute_RT4(df=semiRT4, timedelta=config.delta_period)
    RT4 = clean.data_cleaning(RT4)

    # Push RT4 to the feature store
    feature_store.push(FeatureGroup.RT4, RT4)


def run_inference(feature_store: HopsworksClient) -> None:
    """Map the trained per-ticker targets onto new transactions (BIR4) and store them."""
    # Fetch transactions data
    BI4 = feature_store.read(
        FeatureGroup.BI4,
        where=lambda fg: fg.filter(fg[config.filter_key] == config.acquired_disposed),
        read_options={'use_hive': True},
    )
    # Normalize tickers
    BI4, _ = clean.normalize_data(BI4)

    # Aggregate the data on a daily basis, as defined by config.adg_dict
    BI4_AGG = computations.data_aggregation(BI4, config.agg_dict)

    # Fetch RT4 (form4 + computations)
    RT4 = feature_store.read(FeatureGroup.RT4, read_options={'use_hive': True})

    # Map the average pct_change to BIR4
    BIR4 = computations.map_RT4_to_BIR4(RT4, BI4_AGG)
    BIR4 = clean.data_cleaning(BIR4)

    # Push BIR4 to the feature store
    feature_store.push(FeatureGroup.BIR4, BIR4)


def main():
    """Entry point: connect to the feature store and route by system configuration."""
    time.sleep(config.delay)
    feature_store = HopsworksClient(config.project_name, config.hopsworks_api_key, FEATURE_GROUPS)
    if config.system_training:
        run_training(feature_store)
    elif config.system_inference:
        run_inference(feature_store)


if __name__ == '__main__':
    main()

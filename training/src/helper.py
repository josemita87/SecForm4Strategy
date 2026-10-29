"""Data loading helpers for training."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from constants import FeatureGroup

if TYPE_CHECKING:
    import pandas as pd
    from secform4strategy_clients.ports import FeatureStore

logger = logging.getLogger(__name__)


def load_and_preprocess_data(feature_store: FeatureStore, columns_to_drop: list[str]) -> pd.DataFrame:
    """Load the labelled transactions (RT4) and drop the unused columns.

    Args:
        feature_store: Feature-store client whose catalog includes RT4.
        columns_to_drop: Column names to remove from the loaded data when
            present.

    Returns:
        A DataFrame with the unnecessary columns dropped.
    """
    data = feature_store.read(FeatureGroup.RT4)
    return data.drop(columns=[col for col in columns_to_drop if col in data.columns])

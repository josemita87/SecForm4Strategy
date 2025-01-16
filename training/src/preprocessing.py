from sklearn.preprocessing import StandardScaler
import h2o
import pandas as pd
from loguru import logger

def scale_features(X:pd.DataFrame) -> pd.DataFrame:
    """Scale features using StandardScaler from sklearn."""
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    return pd.DataFrame(X_scaled, columns=X.columns)


def load_and_preprocess_data(file_path: str) -> pd.DataFrame:
    """Load data and prepare it for training with H2O."""

    data = pd.read_csv(file_path)

    # Drop categorical data
    columns_to_drop = ['ticker', 'company_cik', 'key', 'timestamp', 'date', 'coding', 'price']
    data.drop(columns=columns_to_drop, inplace=True)

    logger.info(f"Data loaded and processed with shape: {data.shape}")
    return data

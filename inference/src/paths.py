"""Filesystem paths for the inference service, anchored on the service root.

Centralizes path construction so locations are derived from the repository
layout rather than hardcoded as absolute-path constants in env files. Config
fields default to these and may still be overridden by environment variables.
"""

from pathlib import Path

# inference/ — two levels up from this file (inference/src/paths.py).
SERVICE_ROOT = Path(__file__).resolve().parents[1]

LOGS_DIR = SERVICE_ROOT / 'logs'
DATA_DIR = SERVICE_ROOT / 'data'

# Fitted scikit-learn pipeline produced by the training service.
MODEL_PATH = SERVICE_ROOT / 'models' / 'model.joblib'
# CSV fallback used when the feature store is disabled.
CSV_PATH = DATA_DIR / 'inference_data.csv'
LOG_PATH = LOGS_DIR / 'inference.log'

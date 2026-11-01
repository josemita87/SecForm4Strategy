"""Filesystem locations of the inference service, anchored on its directory; config fields default to them."""

from pathlib import Path

SRC = Path(__file__).resolve().parent

SPEC_PATH = SRC / 'spec.yaml'
MODEL_PATH = SRC.parent / 'models' / 'model.joblib'
#: Stands in for the feature store when ``USE_FEATURE_STORE`` is off.
CSV_PATH = SRC.parent / 'data' / 'inference_data.csv'
LOG_PATH = SRC.parent / 'logs' / 'inference.log'

"""Filesystem locations of the training service, anchored on its source directory; config fields default to them."""

from pathlib import Path

SRC = Path(__file__).resolve().parent

SPEC_PATH = SRC / 'spec.yaml'
MODEL_PATH = SRC / 'models' / 'model.joblib'
PLOT_PATH = SRC / 'plots' / 'simulation.png'
LOG_PATH = SRC / 'logs' / 'training.log'

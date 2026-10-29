"""Filesystem paths for the training service, anchored on the source root.

Centralizes path construction so locations are derived from the repository
layout rather than hardcoded as absolute-path constants in env files. Config
fields default to these and may still be overridden by environment variables.
"""

from pathlib import Path

# training/src — the directory holding this file.
SRC_ROOT = Path(__file__).resolve().parent

MODELS_DIR = SRC_ROOT / 'models'
PLOTS_DIR = SRC_ROOT / 'plots'
LOGS_DIR = SRC_ROOT / 'logs'
DATA_DIR = SRC_ROOT / 'data'

MODEL_PATH = MODELS_DIR / 'model.joblib'
DATA_SOURCE_PATH = DATA_DIR / 'training_data.csv'
PLOT_PATH = PLOTS_DIR / 'simulation.png'
LOG_PATH = LOGS_DIR / 'training.log'

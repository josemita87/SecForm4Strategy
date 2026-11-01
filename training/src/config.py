"""Application settings loaded from environment variables via Pydantic."""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from paths import LOG_PATH, MODEL_PATH, PLOT_PATH, SPEC_PATH
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load the main .env file and then the credentials.env file
load_dotenv('.env')
load_dotenv('.credentials.env')


@lru_cache
def _specs() -> dict[str, Any]:
    """Load the static feature-selection spec (column lists) from spec.yaml."""
    return dict(yaml.safe_load(SPEC_PATH.read_text()))


class Config(BaseSettings):
    """Typed application settings sourced from environment variables."""

    # Iceberg feature store; the catalog itself is configured by PYICEBERG_CATALOG__DEFAULT__* (docs/configuration.md)
    feature_store_namespace: str = 'secform4strategy'
    feature_group_version: int

    # Paths (defaults anchored on the repo layout via paths.py; env overridable)
    model_path: Path = MODEL_PATH
    plot_path: Path = PLOT_PATH
    log_path: Path = LOG_PATH

    # Training settings
    train_test_ratio: float
    random_state: int

    # Feature Selection (column lists loaded from spec.yaml)
    target_feature: str
    columns_to_drop: tuple[str, ...] = Field(default_factory=lambda: _specs()['columns_to_drop'])
    identification_features: tuple[str, ...] = Field(default_factory=lambda: _specs()['identification_features'])

    # Model hyperparameters (loaded from spec.yaml)
    model_params: dict[str, int | float] = Field(default_factory=lambda: _specs()['model_params'])

    # Commit recorded in the model metadata (set in CI/Docker; falls back to `git rev-parse`)
    git_sha: str | None = None

    # `model_path` / `model_params` are settings, not pydantic internals
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', protected_namespaces=('settings_',))


config = Config()

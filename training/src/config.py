"""Application settings loaded from environment variables via Pydantic."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from paths import LOG_PATH, MODEL_PATH, PLOT_PATH, SPEC_PATH
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load the main .env file and then the credentials.env file
load_dotenv('.env')
load_dotenv('.credentials.env')


@lru_cache
def _specs() -> dict[str, Any]:
    """Load the static spec (column lists, model parameters, report thresholds) from spec.yaml."""
    return dict(yaml.safe_load(SPEC_PATH.read_text()))


class LocalModels(BaseModel):
    """A model file on local disk, its metadata beside it."""

    backend: Literal['local']
    path: Path = MODEL_PATH


class S3Models(BaseModel):
    """Versioned models in an S3 bucket, on AWS."""

    backend: Literal['s3']
    region: str
    bucket: str
    prefix: str = 'models'


class Config(BaseSettings):
    """Typed application settings sourced from environment variables."""

    # Iceberg feature store; the catalog itself is configured by PYICEBERG_CATALOG__DEFAULT__* (docs/configuration.md)
    feature_store_namespace: str = 'secform4strategy'
    feature_group_version: int

    # Model store: MODEL_STORE__BACKEND picks it, MODEL_STORE__<FIELD> sets its fields
    model_store: LocalModels | S3Models = Field(LocalModels(backend='local'), discriminator='backend')

    # Paths (defaults anchored on the repo layout via paths.py; env overridable)
    plot_path: Path = PLOT_PATH
    log_path: Path = LOG_PATH

    # Training settings: the latest holdout_ratio of rows is scored once; n_folds walk forward over the rest
    holdout_ratio: float = Field(gt=0, lt=1)
    n_folds: int = Field(ge=1)
    random_state: int
    # Short when the predicted return is below this, as inference trades
    prediction_threshold: float = Field(le=0)

    # Feature Selection (column lists loaded from spec.yaml)
    target_feature: str
    columns_to_drop: tuple[str, ...] = Field(default_factory=lambda: _specs()['columns_to_drop'])
    identification_features: tuple[str, ...] = Field(default_factory=lambda: _specs()['identification_features'])

    # Model hyperparameters, fixed and searched, and the thresholds reported (loaded from spec.yaml)
    model_params: dict[str, bool | int | float | str] = Field(default_factory=lambda: _specs()['model_params'])
    param_grid: dict[str, tuple[bool | int | float | str, ...]] = Field(default_factory=lambda: _specs()['param_grid'])
    report_thresholds: tuple[float, ...] = Field(default_factory=lambda: _specs()['report_thresholds'])

    # Commit recorded in the model metadata (set in CI/Docker; falls back to `git rev-parse`)
    git_sha: str | None = None

    # `model_store` / `model_params` are settings, not pydantic internals
    model_config = SettingsConfigDict(
        env_file='.env', env_file_encoding='utf-8', env_nested_delimiter='__', protected_namespaces=('settings_',)
    )


config = Config()

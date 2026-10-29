"""Application settings loaded from environment variables via Pydantic."""

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from dotenv import load_dotenv
from paths import DATA_SOURCE_PATH, LOG_PATH, MODEL_PATH, PLOT_PATH
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from typing import Any

# Load the main .env file and then the credentials.env file
load_dotenv('.env')
load_dotenv('.credentials.env')


@lru_cache
def _specs() -> dict[str, Any]:
    """Load the static feature-selection spec (column lists) from specs.yaml."""
    return dict(yaml.safe_load((Path(__file__).parent / 'specs.yaml').read_text()))


class Config(BaseSettings):
    """Typed application settings sourced from environment variables."""

    # Hopsworks settings
    project_name: str = Field(..., json_schema_extra={'env': 'PROJECT_NAME'})
    feature_group_version: int = Field(..., json_schema_extra={'env': 'FEATURE_GROUP_VERSION'})
    hopsworks_api_key: str = Field(..., json_schema_extra={'env': 'HOPSWORKS_API_KEY'})

    # Paths (defaults anchored on the repo layout via paths.py; env overridable)
    model_path: Path = Field(default=MODEL_PATH, json_schema_extra={'env': 'MODEL_PATH'})
    data_source_path: Path = Field(default=DATA_SOURCE_PATH, json_schema_extra={'env': 'DATA_SOURCE_PATH'})
    plot_path: Path = Field(default=PLOT_PATH, json_schema_extra={'env': 'PLOT_PATH'})
    log_path: Path = Field(default=LOG_PATH, json_schema_extra={'env': 'LOG_PATH'})

    # Training settings
    train_test_ratio: float = Field(..., json_schema_extra={'env': 'TRAIN_TEST_RATIO'})
    random_state: int = Field(..., json_schema_extra={'env': 'RANDOM_STATE'})

    # Feature Selection (column lists loaded from specs.yaml)
    target_feature: str = Field(..., json_schema_extra={'env': 'TARGET_FEATURE'})
    columns_to_drop: list[str] = Field(default_factory=lambda: _specs()['columns_to_drop'])
    identification_features: list[str] = Field(default_factory=lambda: _specs()['identification_features'])

    # Model hyperparameters (loaded from specs.yaml)
    model_params: dict[str, int | float] = Field(default_factory=lambda: _specs()['model_params'])

    # Commit recorded in the model metadata (set in CI/Docker; falls back to `git rev-parse`)
    git_sha: str | None = Field(None, json_schema_extra={'env': 'GIT_SHA'})

    # `model_path` / `model_params` are settings, not pydantic internals
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', protected_namespaces=('settings_',))


config = Config()
